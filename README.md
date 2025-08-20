# Projeto: Pipeline de Telemetria Industrial (Webhook + Airflow + BigQuery Sandbox)

> **Resumo executivo**: Este projeto implementa ingestão **batch** e **(proto) streaming** de telemetria industrial e manutenção, com orquestração em **Apache Airflow** (Docker), persistência em **BigQuery** e camada analítica construída via **VIEWs** (compatível com **BigQuery Sandbox**). Restrições de faturamento (billing) impediram o uso de Cloud Run/Dataflow; por isso o desenho privilegia **LOAD jobs** e **Views** sem DML.

---

## 1) Objetivos de negócio e técnicos

* **Consolidar dados** de sensores (telemetria) e manutenção em camadas: `raw` → `trusted` → `refined`.
* **Qualidade**: validar schema, normalizar unidades, isolar inválidos (DLQ lógico) e preparar indicadores.
* **Orquestração confiável**: Airflow para execução reprodutível, versionável e observável.
* **Compatibilidade Sandbox**: evitar DML/serviços pagos — usar Views e LOAD jobs.
* **Evolutividade**: caminho claro para streaming real (Pub/Sub → Cloud Run/Dataflow → BigQuery) quando billing for habilitado.

KPIs sugeridos:

* % de leituras inválidas (DLQ) por dia;
* Latência do batch (arquivo → consultas);
* Cobertura de sensores por equipamento;
* Tendências (min/p50/max/avg) por sensor/dia.

---

## 2) Escopo e entregáveis

* **DAG 1 — `raw_ingest`**: criação de tabelas `raw.*`, limpeza e LOAD de telemetria (NDJSON) e manutenção (CSV→NDJSON), tolerante a formatos imperfeitos.
* **DAG 2 — `transform_refine_views_only`**: criação/atualização de **VIEWs** para DLQ lógico, trusted e refined (agregações, último valor, enriquecimento com manutenção).
* **Webhook (prototipado)**: FastAPI que recebe POST `/ingest` e publica no **Pub/Sub** (ordering opcional). Cloud Run não implantado por falta de billing.
* **Infra local**: `docker-compose` (Airflow Celery + Redis + Postgres), `.env`, volumes `dags/`, `data/`, `keys/`.
* **Documentação** (este documento) + instruções de operação e troubleshooting.

---

## 3) Arquitetura

```
[Fontes locais / Webhook]
 telemetria.ndjson ─┐
 manutencao.csv   ───┤         Docker (localhost)
                     │      ┌────────────────────────┐
  FastAPI /ingest  ──┴──▶   │ Apache Airflow (Celery)│
                            │  DAG raw_ingest        │
                            │  DAG transform_refine  │
                            └────────────────────────┘
                                     │ LOAD (BQ API)
                                     ▼
                               BigQuery (Sandbox)
                     ┌────────────────────────────────────────────┐
                     │ raw.telemetry_ingest (tabela)              │
                     │ raw.maintenance_ingest (tabela)            │
                     │ raw.telemetry_dlq_view (VIEW)              │
                     │ trusted.telemetry (VIEW)                   │
                     │ refined.telemetry_daily (VIEW)             │
                     │ refined.telemetry_latest_by_sensor (VIEW)  │
                     │ refined.telemetry_with_maintenance (VIEW)  │
                     └────────────────────────────────────────────┘
```

**Decisões-chave**:

* **Views no Sandbox** para evitar DML (CREATE VIEW é DDL, permitido).
* **Preprocess CSV→NDJSON** para fugir de aspas/delimitadores quebrados do parser CSV do BigQuery.
* **Particionamento/cluster** nas tabelas `raw` para performance de consulta.

Roadmap (quando houver billing):

* Substituir Views por **tabelas materializadas** ou ETLs com DML (`MERGE`), habilitar **Cloud Run**/Dataflow para streaming.

---

## 4) Componentes e responsabilidades

### 4.1 Airflow (Docker Compose)

* Serviços: `airflow-apiserver`, `airflow-scheduler`, `airflow-worker`, `redis`, `postgres`.
* Volumes: `dags/` (código), `data/` (arquivos), `keys/` (SA JSON), `logs/`.
* Credenciais: variáveis no `.env`; SA com papéis de BigQuery (ao menos Data Editor no dataset/projeto).

### 4.2 BigQuery (Sandbox)

* Datasets: `raw`, `trusted`, `refined`.
* Tabelas: `raw.telemetry_ingest` (particionada por `DATE(timestamp)`, cluster por `equipment_id,sensor_id`) e `raw.maintenance_ingest` (particionada por `DATE(start_ts)`).
* Views: DLQ lógico, trusted, refined (agregações, latest, join com manutenção).

### 4.3 Webhook (prototipado)

* FastAPI `/ingest` — valida payload, serializa datetimes, publica em Pub/Sub.
* Autenticação GCP: `GOOGLE_APPLICATION_CREDENTIALS` apontando para SA.
* Erros resolvidos: 422 (falta `timestamp`), serialização `datetime`, ordering key, ADC.

---

## 5) Contratos de dados

### 5.1 Telemetria (NDJSON — 1 registro por linha)

```json
{
  "timestamp": "2024-05-25T10:30:00.123Z",
  "equipment_id": "TURBINE_A",
  "sensor_id": "TBN_001_TMP",  // ^(TBN|GNR)_[0-9]{3}_(TMP|VIB|PRS)$
  "value": 150.5,
  "unit": "Celsius"             // mapeado p/ C|bar|g
}
```

Campos obrigatórios: `timestamp`, `equipment_id`, `sensor_id`. Regras validadas em Views (trusted/DLQ).

### 5.2 Manutenção (CSV → NDJSON)

Header: `maintenance_id,equipment_id,start_ts,end_ts,type,notes`. Linhas curtas: `notes=NULL`.

---

## 6) DAGs

### 6.1 `raw_ingest`

* **ensure\_raw\_tables**: cria dataset `raw` e tabelas com particionamento/cluster; cria `raw.telemetry_dlq` (para uso futuro material).
* **load\_telemetry**: limpa NDJSON (remove comentários `#...`, vírgulas finais, ruído fora de `{}`) e **LOAD** para `raw.telemetry_ingest` (schema fixo com `REQUIRED` nas chaves).
* **load\_maintenance**: pré-processa CSV problemático para **NDJSON** (normaliza delimitadores/aspas, garante 6 colunas) e **LOAD** para `raw.maintenance_ingest`.

### 6.2 `transform_refine_views_only`

* Garante datasets `trusted` e `refined`.
* Cria/atualiza Views:

  * `raw.telemetry_dlq_view`: lista inválidos com `reason` (regex sensor, unidades desconhecidas, NULLs).
  * `trusted.telemetry`: **válidos** + unidades normalizadas (C, bar, g) + `event_date`.
  * `refined.telemetry_daily`: min/p50/max/avg/contagem por dia/equipamento/sensor.
  * `refined.telemetry_latest_by_sensor`: `ROW_NUMBER()` para último valor por sensor.
  * `refined.telemetry_with_maintenance`: flag de sobreposição com janelas de manutenção.

---

## 7) Operação (runbooks)

### 7.1 Subir o ambiente

1. `docker compose up airflow-init`
2. `docker compose up -d`
3. UI do Airflow: `http://localhost:8080` (admin/admin).

### 7.2 Executar pipelines

* Coloque arquivos em `data/`: `telemetria.ndjson`, `manutencao.csv`.
* Rode `raw_ingest` (carrega `raw.*`).
* Rode `transform_refine_views_only` (cria Views).

### 7.3 Reprocessar

* **Clear** task(s) e **Run** novamente; ou `airflow tasks clear` / `airflow dags trigger` via CLI.

### 7.4 Consultas úteis

* Inválidos: `SELECT * FROM raw.telemetry_dlq_view LIMIT 50;`
* Válidos normalizados: `SELECT * FROM trusted.telemetry ORDER BY event_ts DESC LIMIT 50;`
* Agregados: `SELECT * FROM refined.telemetry_daily WHERE sensor_id LIKE '%_VIB';`
* Últimos valores: `SELECT * FROM refined.telemetry_latest_by_sensor;`
* Em manutenção: `SELECT * FROM refined.telemetry_with_maintenance WHERE in_maintenance;`

---

## 8) Qualidade de dados e idempotência

* **Limpeza** (telemetria): descarta linhas irrecuperáveis (comentadas, inválidas JSON).
* **CSV→NDJSON** (manutenção): evita falhas por aspas/delimitadores; garante schema estável.
* **DLQ lógico**: `raw.telemetry_dlq_view` explicita o motivo de rejeição; auditável.
* **Idempotência**: `WRITE_APPEND` — repetir a ingestão duplica registros. Com billing: evoluir para `MERGE`/dedupe em `trusted`.

---

## 9) Segurança e IAM

* **Service Account** (JSON) montada em `keys/sa.json` e referenciada por `GOOGLE_APPLICATION_CREDENTIALS`.
* Princípios: menor privilégio (BigQuery Data Editor no dataset), nunca versionar a chave, rotacionar periodicamente.

---

## 10) Performance e custo

* **Views** calculam on-demand; custo proporcional aos dados lidos (no Sandbox não há cobrança, mas há limites de uso).
* **Partição + Cluster** nas tabelas `raw` favorecem *partition pruning* e filtros por sensor/equipamento.
* Com billing: considerar **Materialized Views**/tabelas agregadas para dashboards pesados.

---

## 11) Troubleshooting (erros reais e correções)

* **`ValidationError PROJECT_ID`**: definir `PROJECT_ID` no `.env` e reiniciar serviços.
* **`DefaultCredentialsError`**: setar `GOOGLE_APPLICATION_CREDENTIALS` para a SA; garantir permissões de BigQuery.
* **422 no `/ingest`**: payload sem `timestamp`; alinhar schema do POST.
* **`datetime is not JSON serializable`**: converter para string ISO (`.isoformat()` ou `%Y-%m-%dT%H:%M:%S.%fZ`).
* **`Cannot publish with ordering key`**: habilitar ordering no tópico Pub/Sub ou remover ordering key.
* **CSV erros (aspas/delimitadores)**: usar **pré-processamento CSV→NDJSON**.
* **`Provided schema does not match`**: alinhar `REQUIRED` vs `NULLABLE` e desativar `autodetect` no LOAD de manutenção.
* **`CREATE VIEW AS SELECT AS VALUE`**: não suportado; usar CTE + `ROW_NUMBER()`.
* **`billingNotEnabled / DML not allowed`**: usar **Views** (DDL) e LOAD jobs; evitar DML.

---

## 12) Roadmap de evolução

1. **Habilitar billing**; migrar Views para **tabelas materializadas**/MERGE idempotente.
2. **Streaming** fim-a-fim: Pub/Sub → Cloud Run (leve) ou **Dataflow (streaming)** → BigQuery (`trusted/refined`).
3. **DLQ material**: popular `raw.telemetry_dlq` (tabela) com DML e auditoria detalhada.
4. **Observabilidade**: métricas (prometheus/stackdriver), alertas Airflow, monitor de % inválidos.
5. **Catálogo**: descrever contratos e políticas (Data Catalog/BigQuery tags).
6. **CICD**: testes unitários para DAGs e SQLs, lint, deploy automatizado (Composer/GitHub Actions).

---

## 13) Apêndices

### 13.1 Exemplo de POST (Webhook)

```bash
curl -X POST http://localhost:8080/ingest \
  -H 'Content-Type: application/json' \
  -d '{
    "sensor_id":"TBN_001_TMP",
    "equipment_id":"TURBINE_A",
    "timestamp":"2024-05-25T10:30:00.123Z",
    "value":150.5,
    "unit":"Celsius"
  }'
```

### 13.2 Snippet para enviar linha a linha (arquivo NDJSON)

```python
import json, time, requests
url = "http://localhost:8080/ingest"
with open("telemetria.ndjson") as f:
    for line in f:
        payload = json.loads(line)
        r = requests.post(url, json=payload, timeout=5)
        print(r.status_code, r.text)
        time.sleep(0.1)  # opcional
```

### 13.3 SQLs de validação

```sql
-- contagens básicas
SELECT COUNT(*) FROM raw.telemetry_ingest;
SELECT COUNT(*) FROM raw.maintenance_ingest;
SELECT COUNT(*) FROM raw.telemetry_dlq_view;
SELECT * FROM trusted.telemetry LIMIT 10;
SELECT * FROM refined.telemetry_latest_by_sensor LIMIT 10;
```

### 13.4 Variáveis de ambiente (.env)

```
AIRFLOW_IMAGE_NAME=apache/airflow:3.0.4
AIRFLOW_UID=50000
AIRFLOW_PROJ_DIR=.
_AIRFLOW_WWW_USER_USERNAME=admin
_AIRFLOW_WWW_USER_PASSWORD=admin

PROJECT_ID=SEU_PROJECT_ID
BQ_LOCATION=US
GOOGLE_APPLICATION_CREDENTIALS=/opt/airflow/keys/sa.json
_PIP_ADDITIONAL_REQUIREMENTS=google-cloud-bigquery==3.20.0
```

---

## 14) Conclusão

O pipeline entrega uma base sólida para ingestão e análise de telemetria com **baixo atrito operacional** no Sandbox, respeitando limitações de billing e mantendo **clareza de camadas**. Quando o faturamento for ativado, a evolução natural envolve **materialização** (para dashboards de baixa latência), **streaming stateful** (Dataflow) e **observabilidade** completa.
#