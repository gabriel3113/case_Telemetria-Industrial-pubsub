# Ingestão Webhook → Pub/Sub (FastAPI + GCP)

Este repositório implementa um **gateway de ingestão HTTP** para eventos de telemetria. A API valida o payload, aplica autenticação simples por chave e **publica as mensagens no Google Cloud Pub/Sub** para processamento assíncrono.

> **Público-alvo**: equipes de dados/engenharia que precisam receber eventos de diversos clientes/sistemas e distribuí-los de forma resiliente para pipelines downstream (ETL/ELT, streaming, BigQuery, Dataflow, etc.).

---

## Sumário

* [Arquitetura](#arquitetura)
* [Estrutura do projeto](#estrutura-do-projeto)
* [Arquivos principais (explicação detalhada)](#arquivos-principais-explicação-detalhada)

  * [main.py](#mainpy)
  * [models.py](#modelspy)
  * [config.py](#configpy)
  * [pubsub.py](#pubsubpy)
* [Contratos de API](#contratos-de-api)
* [Configuração](#configuração)

  * [Variáveis de ambiente](#variáveis-de-ambiente)
  * [.env de exemplo](#env-de-exemplo)
* [Execução local](#execução-local)
* [Container (Docker)](#container-docker)
* [Deploy no Cloud Run](#deploy-no-cloud-run)
* [Boas práticas de segurança](#boas-práticas-de-segurança)
* [Observabilidade](#observabilidade)
* [Testes rápidos (curl)](#testes-rápidos-curl)
* [Resolução de problemas](#resolução-de-problemas)
* [Roadmap / Próximos passos](#roadmap--próximos-passos)
* [Licença](#licença)

---

## Arquitetura

**Objetivo**: Receber eventos via HTTP, validar dados, autenticar a chamada e publicar em um **tópico Pub/Sub** com **ordering key opcional**. Consumidores downstream (Dataflow, Cloud Functions, etc.) realizam o processamento.

**Fluxo (alto nível)**:

```
Cliente → POST /ingest (/ingest/batch) → Validação (Pydantic) → Auth (x-api-key)
      → (definição de ordering_key) → Publicar no Pub/Sub → 202 Accepted
```

**Decisões de projeto**

* **FastAPI**: leve, rápido, ótimo suporte a validação e OpenAPI.
* **Pydantic**: contratos fortes e mensagens de erro consistentes.
* **Pub/Sub**: desacoplamento, escalabilidade e entrega at-least-once.
* **Ordering Key**: controle de ordenação por chave (ex.: `equipment_id`).

---

## Estrutura do projeto

```
.
├── main.py        # API FastAPI: endpoints, auth, roteamento e integração com publisher
├── models.py      # Modelos Pydantic (esquemas, validações e aliases)
├── config.py      # Configurações/variáveis de ambiente e credenciais
├── pubsub.py      # Publisher do Google Cloud Pub/Sub (credenciais e publish)
└── README.md      # Este documento
```

---

## Arquivos principais (explicação detalhada)

### `main.py`

**Responsabilidade**: expor API HTTP com endpoints de ingestão e saúde.

**Pontos-chave**:

* **`GET /healthz`**: verificação de vida/prontidão.
* **`POST /ingest`**: recebe **um** evento; valida contra `Telemetry`; autentica via `x-api-key` (opcionalmente exigido).
* **`POST /ingest/batch`**: recebe **lista** de eventos; valida cada item e publica individualmente.
* **Autenticação**: controlada por `ALLOW_ANON`; quando for `false`, `x-api-key` deve estar presente e válido.
* **Ordering Key**: se `ORDERING_KEY_FIELD` estiver configurado e existir no payload, é enviado ao Pub/Sub para garantir ordenação por chave (consumidor com message ordering habilitado).
* **Códigos de resposta**: `202` (enfileirado), `401` (falha auth), `422` (payload inválido), `503` (falha pub).

**Por que separar a camada HTTP do publisher?**

* Mantém o **SRP (Single Responsibility Principle)**.
* Facilita testes unitários e substituição do backend de mensagens no futuro.

---

### `models.py`

**Responsabilidade**: definir o **contrato do payload** e aplicar validações consistentes.

**Modelo principal: `Telemetry`**

* `timestamp: datetime` **com timezone** (aware); aceita alias `event_ts`.
* `equipment_id: str` (obrigatório).
* `sensor_id: str` com **regex**: `^(TBN|GNR)_[0-9]{3}_(TMP|VIB|PRS)$`.

  * Exemplos válidos: `TBN_001_TMP`, `GNR_123_VIB`.
  * Exemplos inválidos: `TBN_12_TMP`, `FOO_999_BAR`.
* `value: float | None` e `unit: str | None` (opcionais).

**Benefícios**

* Rejeita cedo payloads inválidos (menor custo downstream).
* Mensagens de erro claras para chamadores corrigirem dados.

---

### `config.py`

**Responsabilidade**: centralizar as **variáveis de ambiente** e como as credenciais são obtidas.

**Variáveis**

* `PROJECT_ID`: projeto GCP.
* `TOPIC_ID`: tópico Pub/Sub (padrão: `telemetry-events`).
* `ALLOW_ANON`: permite chamadas sem `x-api-key` quando `true` (não recomendado em prod).
* `API_KEY`: segredo comparado ao header `x-api-key` quando `ALLOW_ANON=false`.
* `ORDERING_KEY_FIELD`: nome do campo do payload usado como `ordering_key` (ex.: `equipment_id`).
* **Credenciais**: `CREDENTIALS_PATH` (arquivo JSON) **ou** `CREDENTIALS_JSON_B64` (conteúdo JSON Base64). Se nenhum for definido, usa **ADC** (Application Default Credentials).

**Motivação**

* Fornecer flexibilidade: rodar localmente, em contêiner, ou no Cloud Run/Compute Engine/… sem reescrever código.

---

### `pubsub.py`

**Responsabilidade**: publicar mensagens no **Google Cloud Pub/Sub**.

**Pontos-chave**

* Cria um **`PublisherClient` singleton** com as credenciais resolvidas.
* Monta `topic_path` a partir de `PROJECT_ID` + `TOPIC_ID`.
* Serializa o payload como JSON (UTF-8) e chama `publish(...).result(timeout=30)`.
* Suporte a **ordering key** quando configurada (`ordering_key=...`).
* Tratamento de exceções de publish; você pode evoluir para **retry/backoff** e **DLQ**.

**Por que isolar o publisher?**

* Evita dependência do GCP na camada HTTP.
* Facilita *mocking* e testes unitários.

---

## Contratos de API

### `GET /healthz`

**200 OK**

```json
{"status":"ok"}
```

### `POST /ingest`

**Headers**

* `Content-Type: application/json`
* `x-api-key: <segredo>` (requerido se `ALLOW_ANON=false`)

**Body** (exemplo)

```json
{
  "timestamp": "2025-08-20T15:45:00-03:00",
  "equipment_id": "EQP-9001",
  "sensor_id": "TBN_123_TMP",
  "value": 42.7,
  "unit": "C"
}
```

**Responses**

* `202 Accepted`: `{ "status": "queued" }`
* `401 Unauthorized`: `{ "detail": "invalid api key" }`
* `422 Unprocessable Entity`: erros de validação do Pydantic

### `POST /ingest/batch`

**Body**: `[{Telemetry}, {Telemetry}, ...]`

**Responses**

* `202 Accepted`: `{ "status": "queued", "count": <n> }`

---

## Configuração

### Variáveis de ambiente

| Variável               |           Obrigatória |            Default | Descrição                                  |
| ---------------------- | --------------------: | -----------------: | ------------------------------------------ |
| `PROJECT_ID`           |                   sim |                  — | ID do projeto GCP                          |
| `TOPIC_ID`             |                   não | `telemetry-events` | Nome do tópico Pub/Sub                     |
| `ALLOW_ANON`           |                   não |            `False` | Permite requisições sem `x-api-key`        |
| `API_KEY`              | se `ALLOW_ANON=false` |                  — | Valor esperado do header `x-api-key`       |
| `ORDERING_KEY_FIELD`   |                   não |                  — | Campo do payload usado como `ordering_key` |
| `CREDENTIALS_PATH`     |                   não |                  — | Caminho para JSON da service account       |
| `CREDENTIALS_JSON_B64` |                   não |                  — | Conteúdo JSON (Base64) da service account  |

### .env de exemplo

```
PROJECT_ID=meu-projeto
TOPIC_ID=telemetry-events
ALLOW_ANON=false
API_KEY=sua-chave-super-secreta
ORDERING_KEY_FIELD=equipment_id
# CREDENTIALS_PATH=/run/secrets/sa.json
# CREDENTIALS_JSON_B64=eyJ0eXAiOiJKV1QiIC4uLg==
```

> **Dica**: Em produção (Cloud Run), prefira **Workload Identity/ADC** para evitar montar chaves no contêiner. Conceda ao serviço o papel `roles/pubsub.publisher` no tópico.

---

## Execução local

1. Crie e preencha o arquivo `.env` (veja exemplo acima).
2. (Opcional) Autentique no GCP localmente (`gcloud auth application-default login`).
3. Instale dependências e rode a API:

```bash
pip install -r requirements.txt  # (ou poetry/pipenv)
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Abra `http://localhost:8000/healthz` para checar.

---

## Container (Docker)

**Dockerfile** mínimo sugerido:

```Dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

**Build & Run**

```bash
docker build -t ingest-pubsub:latest .
# usando .env local
docker run --rm -p 8080:8080 --env-file .env ingest-pubsub:latest
```

---

## Deploy no Cloud Run

1. Crie o tópico Pub/Sub (se ainda não existir):

```bash
gcloud pubsub topics create telemetry-events --project=$PROJECT_ID
```

2. Faça o deploy:

```bash
gcloud run deploy ingest-pubsub \
  --source . \
  --project $PROJECT_ID \
  --region us-central1 \
  --allow-unauthenticated=false \
  --set-env-vars PROJECT_ID=$PROJECT_ID,TOPIC_ID=telemetry-events,ALLOW_ANON=false,ORDERING_KEY_FIELD=equipment_id,API_KEY=xxx
```

3. Conceda o papel `roles/pubsub.publisher` à service account do serviço (ou configure **Workload Identity**).

> **TLS**: Cloud Run já expõe HTTPS gerenciado.

---

## Boas práticas de segurança

* **Desative** `ALLOW_ANON` em produção e **exija** `x-api-key` (ou, idealmente, OAuth/JWT).
* Armazene `API_KEY`/segredos em **Secret Manager**.
* Use **least privilege** para a service account (somente `pubsub.publisher`).
* Audite logs de acesso negado e 4xx/5xx.

---

## Observabilidade

* **Logs estruturados** (JSON) com correlação por `request_id`.
* Métricas de: latência, taxa de erro, TPS, *publish latency*, timeouts.
* Alertas (ex.: `5xx` acima de limiar; tempo de fila no Pub/Sub).
* (Opcional) **OpenTelemetry** para traces distribuídos.

---

## Testes rápidos (curl)

Evento único:

```bash
curl -X POST http://localhost:8000/ingest \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: sua-chave-super-secreta' \
  -d '{
    "timestamp": "2025-08-20T15:45:00-03:00",
    "equipment_id": "EQP-9001",
    "sensor_id": "TBN_123_TMP",
    "value": 42.7,
    "unit": "C"
  }'
```

Lote:

```bash
curl -X POST http://localhost:8000/ingest/batch \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: sua-chave-super-secreta' \
  -d '[
    {"timestamp":"2025-08-20T15:45:00-03:00","equipment_id":"EQP-1","sensor_id":"TBN_001_TMP","value":10.1},
    {"timestamp":"2025-08-20T15:45:30-03:00","equipment_id":"EQP-2","sensor_id":"GNR_777_VIB","value":99.9}
  ]'
```

---

## Resolução de problemas

* **401 Unauthorized**: verifique `ALLOW_ANON=false` + header `x-api-key` correspondendo a `API_KEY`.
* **422 Unprocessable Entity**: payload não atende ao contrato `Telemetry` (timestamp com timezone, `sensor_id` respeitando regex, etc.).
* **503 Service Unavailable**: falha ao publicar no Pub/Sub. Verifique credenciais, permissão `pubsub.publisher`, conectividade e timeout.
* **Ordering key**: se houver baixa vazão, a chave pode estar concentrando mensagens; diversifique (ex.: por `equipment_id`).
* **Credenciais**: em local, use `CREDENTIALS_PATH`/`CREDENTIALS_JSON_B64` ou `gcloud auth application-default login`. Em Cloud Run prefira ADC/Workload Identity.

---

## Roadmap / Próximos passos

* **DLQ (Dead Letter Queue)**: configurar assinatura com DLQ para mensagens não processadas pelos consumidores.
* **Retry/backoff**: usar política de retry no publisher com *exponential backoff*.
* **Rate limiting**: conter abuso e proteger o downstream.
* **Schema Registry**: versionar `Telemetry` (ex.: JSON Schema) e validar por versão.
* **Autenticação robusta**: migrar de API key para OAuth2/JWT.
* **Batch nativo Pub/Sub**: otimizar *publish* usando `BatchSettings` do client.

---

## Licença

Este projeto é disponibilizado nos termos da licença MIT (ou ajuste conforme necessidade interna).
