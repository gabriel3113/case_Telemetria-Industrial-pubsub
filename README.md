# Projeto: Ingestão por Webhook → Pub/Sub (sem Airflow)

> **Resumo:** Este documento descreve a solução **atual** entregue: um **Webhook HTTP** (FastAPI/Poetry) que recebe eventos de telemetria e publica no **Google Cloud Pub/Sub**. Não há etapas de orquestração (Airflow) nem persistência em BigQuery dentro deste escopo, por **limitações de billing** no GCP. O desenho, entretanto, já prepara a evolução para consumidores e camadas analíticas quando o faturamento puder ser habilitado.

---

## 1) Objetivos

* **Receber eventos** de telemetria via HTTP de forma simples e rápida.
* **Validar e padronizar** campos mínimos do payload.
* **Publicar de forma confiável** os eventos no **Pub/Sub** com atributos úteis (tracing/ordering), permitindo escala horizontal futura.
* **Isolar responsabilidades**: captura/validação (webhook) separada do processamento (consumidores a serem adicionados).

**Fora de escopo no momento (motivo: billing GCP):** Cloud Run/Functions como consumidores, Dataflow Streaming, BigQuery (Storage Write API), DLQ material em tópicos separados.

---

## 2) Arquitetura Atual

```
Produtor (cliente) ──HTTP POST /ingest──▶ Webhook (FastAPI)
                                        └─▶ Pub/Sub Topic: telemetry-events
```

* **Webhook**: expõe `/ingest` (JSON). Faz validação leve, normalização básica e publica uma mensagem por evento.
* **Pub/Sub**: armazena e distribui as mensagens para futuras **subscriptions** (pull/push).
* **Sem consumidor** por decisão de escopo: backlog permanece no tópico/assinaturas até existir um subscriber (Cloud Run/Function/Dataflow) em fase 2.

### 2.1 Diagrama de Sequência

1. Cliente envia `POST /ingest` com JSON.
2. Webhook valida (`Pydantic`) e normaliza (`timestamp`, `units`, `strings`).
3. Webhook publica no Pub/Sub (`PublisherClient`) com atributos (ver §5.3).
4. Webhook responde `202 Accepted` (assíncrono por natureza do publish).

---

## 3) Componentes

### 3.1 Ingest Service (FastAPI + Poetry)

* **Endereço**: `POST /ingest` (conteúdo `application/json`).
* **Validação**: `Pydantic` exige campos chave: `timestamp`, `equipment_id`, `sensor_id`, `value`, `unit`.
* **Tratamento de erros**: respostas `422` (schema), `400` (negócio), `500` (falhas internas/pub).
* **Publicação**: utiliza `google-cloud-pubsub` com **Service Account** (ADC) carregada via `GOOGLE_APPLICATION_CREDENTIALS`.
* **Logs**: estruturados, incluem `event_id`, `equipment_id`, `sensor_id` e `publish_result` (message\_id/erro).

### 3.2 Google Cloud Pub/Sub

* **Topic sugerido**: `telemetry-events`.
* **Atributos em cada mensagem** (key/value string):

  * `schema_version` (ex.: `v1`)
  * `equipment_id`
  * `sensor_id`
  * `event_ts` (ISO8601)
  * `ordering_key` (opcional: `equipment_id`)
  * `content_type` (`application/json`)
* **Subscriptions** (a criar na fase 2):

  * `bq-writer` (Cloud Run → BigQuery)
  * `dlq` (dead-letter) com política de reentrega/estouro

### 3.3 Credenciais

* **Service Account** com papel mínimo: `roles/pubsub.publisher` no projeto.
* **Arquivo JSON** montado via variável: `GOOGLE_APPLICATION_CREDENTIALS=/path/sa.json`.

---

## 4) Contrato de Dados (Payload)

### 4.1 Telemetria JSON (requerido no POST)

```json
{
  "timestamp": "2024-05-25T10:30:00.123Z",
  "equipment_id": "TURBINE_A",
  "sensor_id": "TBN_001_TMP",
  "value": 150.5,
  "unit": "Celsius"
}
```

* **Regras mínimas**:

  * `timestamp` em **ISO8601** (`Z` ou offset explícito).
  * `sensor_id` deve seguir regex **`^(TBN|GNR)_[0-9]{3}_(TMP|VIB|PRS)$`** (recomendado; se estrito, responder 400).
  * `unit` aceita: `Celsius`, `C`, `°C`, `Bar`, `g` (mapeáveis a `C`, `bar`, `g`).

### 4.2 Normalizações no Webhook

* Converte `timestamp` para string canonical (UTC Z) se necessário.
* `unit` opcionalmente normalizada para um conjunto conhecido; se desconhecida, pode-se publicar com atributo `unit_unknown=true` para roteamento posterior.

---

## 5) Publicação no Pub/Sub

### 5.1 Código (resumo conceitual)

```python
from google.cloud import pubsub_v1
publisher = pubsub_v1.PublisherClient()
topic_path = publisher.topic_path(PROJECT_ID, "telemetry-events")

# payload: dict já validado por Pydantic
body = json.dumps(payload).encode("utf-8")
attrs = {
  "schema_version": "v1",
  "equipment_id": payload["equipment_id"],
  "sensor_id": payload["sensor_id"],
  "event_ts": payload["timestamp"],
  "content_type": "application/json",
}
future = publisher.publish(topic_path, data=body, **attrs)
message_id = future.result(timeout=10)
```

### 5.2 Ordenação (opcional)

* Para garantir ordem por equipamento, habilitar **message ordering** no tópico e setar `ordering_key=equipment_id`.
* Requer: tópico com ordering **enabled**; consumidor que reconheça a ordenação.

### 5.3 Idempotência

* Recomenda-se incluir `event_id` (hash de `equipment_id+sensor_id+timestamp`) como atributo; consumidores podem **deduplicar**.

### 5.4 DLQ

* Configurar **Dead Letter Policy** na assinatura (fase 2). Mensagens com N falhas vão para `telemetry-events-dlq`.

---

## 6) Estrutura do Projeto

```
repo/
 ├─ src/ingest_service/
 │   ├─ main.py            # FastAPI app, rotas
 │   ├─ models.py          # Pydantic schemas (Request/Response)
 │   ├─ publisher.py       # Cliente Pub/Sub (wrap + retries)
 │   ├─ config.py          # Settings (pydantic-settings)
 │   └─ logging.py         # Config de logs estruturados
 ├─ pyproject.toml         # Poetry
 ├─ .env.example
 ├─ README.md
 └─ Dockerfile (opcional)
```

### 6.1 Variáveis de Ambiente

| Nome                             | Descrição                                |
| -------------------------------- | ---------------------------------------- |
| `PROJECT_ID`                     | ID do projeto GCP                        |
| `PUBSUB_TOPIC`                   | Nome do tópico (ex.: `telemetry-events`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Caminho do JSON da SA                    |
| `PUBLISH_ORDERING_ENABLED`       | `true/false`                             |
| `LOG_LEVEL`                      | `INFO/DEBUG`                             |

### 6.2 Execução local

```bash
poetry install
poetry run uvicorn ingest_service.main:app --reload --port 8080
```

### 6.3 Teste rápido (curl)

```bash
curl -X POST http://127.0.0.1:8080/ingest \
  -H 'content-type: application/json' \
  -d '{
    "timestamp":"2024-05-25T10:30:00.123Z",
    "equipment_id":"TURBINE_A",
    "sensor_id":"TBN_001_TMP",
    "value":150.5,
    "unit":"Celsius"
  }'
```

Resposta esperada: `202 Accepted` (com corpo JSON de confirmação e `message_id`, se exposto).

---

## 7) Operação e Observabilidade

* **Logs estruturados**: cada requisição registra status, latência, atributos de publicação, exceções com stacktrace.
* **Métricas** (opcional): contadores por sensor/equipamento, erros de validação, taxa de publicação, percentil de latência.
* **Healthcheck**: `GET /healthz` retorna 200 para readiness/liveness (Docker/K8s/Run).
* **Backpressure**: se `publisher.publish` atrasar, retornar `202` e enfileirar; impor limites de payload/tamanho.

---

## 8) Segurança

* **Autenticação no GCP**: SA restrita a `roles/pubsub.publisher`.
* **Webhook**: considerar **API Key**/Token no header (`Authorization: Bearer ...`) quando exposto fora da rede confiável.
* **Rate limiting**: proteção contra abuso (ex.: 100 req/s por IP) e limites de tamanho (`Content-Length`).
* **Sanitização**: remover/Pseudonimizar PII se aparecer em `notes`/campos livres.

---

## 9) Limitações atuais (e justificativa)

* **Sem consumidores** (Cloud Run/Dataflow/Functions) e **sem BigQuery** por **billing desativado**.
* **Sem DLQ material** (apenas conceito).
* **Sem garantia de ordenação** se `ordering_key` estiver desligado.

> Essas decisões mantêm o escopo **executável** sem custos, preparando terreno para a Fase 2 com billing.

---

## 10) Evolução (quando billing estiver ativo)

1. **Cloud Run Subscriber** (Push Subscription): recebe mensagens, valida, escreve no **BigQuery** via **Storage Write API** (baixa latência/Exactly-Once), enriquece/roteia para DLQ.
2. **Dataflow Streaming** (se houver janelas/estado): Pub/Sub → Beam → Trusted/Refined (BQ) com janelas, dedup, side inputs (mapa de unidades), late data.
3. **Views/Tabelas Analíticas**: criar `trusted.telemetry` e `refined.*` (daily, latest, with\_maintenance) como **materializadas** ou populadas com **MERGE**.
4. **Observabilidade**: Cloud Logging, Error Reporting, Cloud Monitoring dashboards/alertas.

---

## 11) Anexos

### 11.1 OpenAPI (trecho)

```yaml
openapi: 3.0.3
paths:
  /ingest:
    post:
      summary: Ingestão de eventos de telemetria
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Telemetry'
      responses:
        '202': { description: Aceito }
        '400': { description: Erro de negócio }
        '422': { description: Payload inválido }
        '500': { description: Erro interno }
components:
  schemas:
    Telemetry:
      type: object
      required: [timestamp, equipment_id, sensor_id, value, unit]
      properties:
        timestamp: { type: string, format: date-time }
        equipment_id: { type: string }
        sensor_id: { type: string }
        value: { type: number }
        unit: { type: string }
```

### 11.2 gcloud (criar tópico e assinatura)

```bash
gcloud pubsub topics create telemetry-events \
  --message-storage-policy-allowed-regions=us-east1

gcloud pubsub subscriptions create telemetry-events-pull \
  --topic=telemetry-events \
  --ack-deadline=20
```

### 11.3 Dockerfile (sugestão)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml poetry.lock* ./
RUN pip install poetry && poetry config virtualenvs.create false \
 && poetry install --no-interaction --no-ansi
COPY src ./src
ENV PORT=8080
CMD ["uvicorn", "ingest_service.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### 11.4 Cliente de teste (linhas NDJSON)

```python
import json, time, requests
url = "http://localhost:8080/ingest"
with open("telemetria.ndjson") as f:
    for line in f:
        payload = json.loads(line)
        requests.post(url, json=payload, timeout=3)
        time.sleep(0.05)
```

---

## 12) Conclusão

A solução atual entrega um **canal de ingestão confiável** (Webhook → Pub/Sub), com **contratos claros**, **validação** e **log** adequados, pronta para escalar com consumidores e camada analítica assim que o **billing** estiver disponível. O design separa captura de processamento, reduz acoplamento e facilita a evolução para **tempo real** ou **batch** conforme a necessidade.
