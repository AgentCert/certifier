<div align="center">

# AgentCert — Certifier

**Automated certification for AI agents operating under Kubernetes fault injection**

Consumes raw Langfuse traces, extracts per-fault metrics across N experimental runs,
and produces structured 12-section certification reports with LLM-synthesised narrative
and deterministic statistical aggregation.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square&logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.127-009688?style=flat-square&logo=fastapi)
![MongoDB](https://img.shields.io/badge/MongoDB-7-47A248?style=flat-square&logo=mongodb)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker)

</div>

---

## How It Works

```
Raw Langfuse Trace (JSON)
         │
         ▼
┌─────────────────────┐
│  Phase 0            │  LLM classifies interleaved trace events into
│  Fault Bucketing    │  per-fault lifecycle buckets
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  Phase 1            │  Extracts quantitative (TTD, TTR, tokens) and
│  Metrics Extraction │  qualitative metrics per fault  →  *_metrics.json
└────────┬────────────┘
         │   (repeat across N runs)
         ▼
┌─────────────────────┐
│  Phase 2            │  Pure-Python statistics per fault category
│  Aggregation        │  + LLM Council (k judges + meta-judge) synthesis
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  Phase 3            │  Builds a validated 12-section CertificationReport
│  Certification      │  with 5 concurrent LLM narrative builders
└────────┬────────────┘
         │            └──▶ workspace/{agent_id}/{experiment_id}/cert-builder/certification.json
         ▼
┌─────────────────────┐
│  cert_reporter      │  LangGraph pipeline renders certification.json into
│  (report rendering) │  a polished HTML report and A4 PDF
└─────────────────────┘
         │
         └──▶ workspace/{agent_id}/{experiment_id}/certification/<doc_id>.html
              workspace/{agent_id}/{experiment_id}/certification/<doc_id>.pdf
```

Both pipeline pairs are exposed as **async REST endpoints** (submit → poll) and
as **CLI commands** for direct local execution.

`POST /api/v1/aggregation-certification` runs all four steps above in a single background task. The rendered HTML and PDF are available at `POST /api/generate/html` and `POST /api/generate/pdf` once the task completes.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | |
| MongoDB | 7 | Local, Docker, or Atlas |
| Azure OpenAI — extraction model | — | e.g. GPT-4o; used for Phase 1 + Phase 3 narratives |
| Azure OpenAI — reasoning model | — | e.g. o1 / o3-mini; used for LLM Council in Phase 2; `temperature` stripped automatically |
| Azure OpenAI — embedding model | — | e.g. text-embedding-3-small |
| Docker + Compose | v2+ | Required for the Docker path only |

---

## Getting Started

### Option A — Docker Compose (recommended)

The fastest path. Docker Compose starts the API server and MongoDB together.

**Step 1 — Configure credentials**

```bash
cp .env.example .env
```

Edit `.env` and fill in the three Azure OpenAI blocks:

```ini
# ── Extraction model (GPT-4o or equivalent) ───────────────────────────────────
AZURE_OPENAI_ENDPOINT              = https://<resource>.openai.azure.com/
AZURE_OPENAI_API_KEY               = <key>
AZURE_OPENAI_API_VERSION           = 2024-08-01-preview
AZURE_OPENAI_CHAT_DEPLOYMENT_NAME  = gpt-4o

# ── Reasoning model (o1 / o3-mini) — temperature stripped automatically ───────
AZURE_OPENAI_GPT5_ENDPOINT             = https://<resource>.openai.azure.com/
AZURE_OPENAI_GPT5_API_KEY              = <key>
AZURE_OPENAI_GPT5_API_VERSION          = 2024-12-01-preview
AZURE_OPENAI_GPT5_CHAT_DEPLOYMENT_NAME = o1-mini

# ── Embedding model ───────────────────────────────────────────────────────────
AZURE_EMBEDDING_ENDPOINT    = https://<resource>.openai.azure.com/
AZURE_EMBEDDING_API_KEY     = <key>
AZURE_EMBEDDING_API_VERSION = 2024-02-01
AZURE_EMBEDDING_MODEL       = text-embedding-3-small
```

> All other variables have working defaults. MongoDB is managed by Compose —
> leave `MONGODB_CONNECTION_STRING` as-is in `.env`.

**Step 2 — Build and start**

```bash
docker compose up --build
```

| Service | URL |
|---|---|
| API + interactive docs | `http://localhost:8000/docs` |
| MongoDB | `localhost:27017` |

**Step 3 — Verify**

```bash
curl -s http://localhost:8000/docs | head -1
# HTTP/1.1 200 OK
```

---

### Option B — Local (without Docker)

**Step 1 — Create a virtual environment**

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
```

**Step 2 — Install dependencies**

```bash
pip install -r requirements.txt
```

**Step 3 — Configure credentials**

```bash
cp .env.example .env
# fill in Azure credentials as shown in Option A
```

**Step 4 — Set PYTHONPATH**

The top-level packages (`main`, `utils`, `fault_analyzer`, `metrics_extractor`,
`aggregator`, `cert_builder`) are imported directly from the repo root — there
is no `setup.py` install step.

```bash
export PYTHONPATH=$(pwd)        # Windows: set PYTHONPATH=%cd%
```

**Step 5 — Start MongoDB**

```bash
# Quickest option — Docker single container
docker run -d --name mongo -p 27017:27017 mongo:7
```

Or point `MONGODB_CONNECTION_STRING` in `.env` at any running MongoDB instance.

**Step 6 — Start the API server**

```bash
python -m main.main
# Uvicorn reads API_HOST / API_PORT from env (defaults: 0.0.0.0:8000)
```

---

## Running the Pipelines

### REST API

Both pipelines follow the same **async job pattern**:

```
POST /api/v1/<endpoint>   →  202 Accepted  { task_id, poll_url }
GET  /api/v1/<poll_url>   →  { status: PENDING | RUNNING | COMPLETED | FAILED }
```

#### Phase 0+1 — Fault Bucketing + Metrics Extraction

```bash
# 1. Submit a job
curl -s -X POST http://localhost:8000/api/v1/bucketing-extraction \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id":      "my-agent",
    "experiment_id": "exp-001",
    "run_id":        "run-42",
    "trace_source": {
      "type":      "file",
      "file_path": "/app/workspace/traces/run42.json"
    }
  }' | jq .
```

```json
{ "task_id": "550e8400-...", "poll_url": "/api/v1/tasks/550e8400-..." }
```

```bash
# 2. Poll until COMPLETED
curl -s http://localhost:8000/api/v1/tasks/550e8400-... | jq '{status, stage}'
```

Output written to `workspace/exp-001/run-42/`:

```
traces/
└── raw_trace.json
fault_buckets/
└── <fault_id>_bucket.json      (one per detected fault)
metrics/
└── <fault_id>_metrics.json     (one per fault — input for Phase 2+3)
pipeline_summary.json
```

#### Phase 2+3 — Aggregation + Certification

```bash
# 1. Submit a job
#    metrics_dir is optional — auto-derived as workspace/{experiment_id}/
curl -s -X POST http://localhost:8000/api/v1/aggregation-certification \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id":             "my-agent",
    "agent_name":           "My Agent v1.0",
    "experiment_id":        "exp-001",
    "certification_run_id": "v1.0.0"
  }' | jq .
```

```json
{ "cert_task_id": "7c4a8d64-...", "poll_url": "/api/v1/cert-tasks/7c4a8d64-..." }
```

```bash
# 2. Poll until COMPLETED
curl -s http://localhost:8000/api/v1/cert-tasks/7c4a8d64-... | jq '{status, stage}'
```

Output written to `workspace/my-agent/exp-001/`:

```
aggregation/
└── aggregation.json              ← Phase 2 aggregated scorecard
cert-builder/
└── certification.json            ← Phase 3 certification report (input to cert_reporter)
certification/
├── <doc_id>.html                 ← HTML report (generated by cert_reporter pipeline)
└── <doc_id>.pdf                  ← PDF report  (generated by cert_reporter pipeline)
pipeline_summary.json
```

The `storage_paths` field in the completed task response includes paths to all five files above, including `html_report` and `pdf_report`.

> **Trace sources** — `trace_source.type` can be `"file"` (path on the server)
> or `"langfuse"` (fetch live from a Langfuse instance using `base_url`,
> `public_key`, `secret_key`, `from_timestamp`).

---

### CLI

The CLIs invoke the pipeline services directly — no running server required.
Useful for development, one-off runs, and scripting.

#### Phase 0+1

```bash
python -m main.cli.run_bucketing_and_extraction_pipeline \
    --trace-file  /path/to/trace.json \
    --output-dir  workspace/exp-001/run-42 \
    --batch-size  5
```

| Flag | Required | Default | Description |
|---|---|---|---|
| `--trace-file` | Yes | — | Path to raw Langfuse trace JSON array |
| `--output-dir` | Yes | — | Root output directory |
| `--batch-size` | No | `5` | LLM events per batch during Phase 0 bucketing |
| `--store` | No | off | Also persist extracted metrics to MongoDB |

#### Phase 2+3

```bash
python -m main.cli.run_aggregation_and_certification_pipeline \
    --metrics-dir  workspace/exp-001 \
    --output-dir   workspace/cert/my-agent/exp-001 \
    --agent-id     my-agent \
    --agent-name   "My Agent v1.0" \
    --certification-run-id v1.0.0
```

| Flag | Required | Default | Description |
|---|---|---|---|
| `--metrics-dir` | Yes | — | Directory containing `*metrics.json` files from Phase 1 |
| `--output-dir` | Yes | — | Root output directory for scorecard and report |
| `--agent-id` | Yes | — | Agent identifier matching the metrics documents |
| `--agent-name` | Yes | — | Human-readable name written into the report |
| `--certification-run-id` | No | `""` | Caller-supplied identifier (e.g. git SHA) |
| `--runs-per-fault` | No | `30` | Expected N runs per fault for statistical checks |
| `--debug` | No | off | Retain intermediate outputs for post-mortem inspection |

---

## Environment Variables

### Required

| Variable | Description |
|---|---|
| `MONGODB_CONNECTION_STRING` | Motor/PyMongo connection string |
| `AZURE_OPENAI_ENDPOINT` | Extraction model endpoint |
| `AZURE_OPENAI_API_KEY` | Extraction model API key |
| `AZURE_OPENAI_API_VERSION` | Extraction model API version |
| `AZURE_OPENAI_CHAT_DEPLOYMENT_NAME` | Extraction model deployment name |
| `AZURE_OPENAI_GPT5_ENDPOINT` | Reasoning model endpoint |
| `AZURE_OPENAI_GPT5_API_KEY` | Reasoning model API key |
| `AZURE_OPENAI_GPT5_API_VERSION` | Reasoning model API version |
| `AZURE_OPENAI_GPT5_CHAT_DEPLOYMENT_NAME` | Reasoning model deployment name |
| `AZURE_EMBEDDING_ENDPOINT` | Embedding model endpoint |
| `AZURE_EMBEDDING_API_KEY` | Embedding model API key |
| `AZURE_EMBEDDING_API_VERSION` | Embedding model API version |
| `AZURE_EMBEDDING_MODEL` | Embedding model deployment name |

### Optional

| Variable | Default | Description |
|---|---|---|
| `MONGODB_DATABASE` | `agentcert` | Database name |
| `API_HOST` | `0.0.0.0` | Uvicorn bind address |
| `API_PORT` | `8000` | Uvicorn bind port |
| `WORKSPACE_DIR` | `workspace` | Phase 0+1 output root |
| `CERT_WORKSPACE_DIR` | `workspace/cert` | Phase 2+3 output root |
| `API_MAX_CONCURRENT_TASKS` | `4` | Max simultaneous Phase 0+1 pipeline runs |
| `API_MAX_CONCURRENT_CERT_TASKS` | `2` | Max simultaneous Phase 2+3 pipeline runs |
| `AZURE_CONTENT_SAFETY_ENDPOINT` | — | Required for RAI compliance checks |
| `AZURE_CONTENT_SAFETY_API_KEY` | — | Required for RAI compliance checks |
| `AZURE_STORAGE_CONNECTION_STRING` | — | Required for `storage_config.type = "blob_storage"` |
| `PYTHONPATH` | — | Must include repo root; set automatically in Docker |

> See [`.env.example`](.env.example) for the full annotated list.

---

## Tests

```bash
# Run the full test suite
pytest

# Run tests for a specific module
pytest fault_analyzer/tests/
pytest metrics_extractor/tests/
pytest aggregator/tests/
pytest cert_builder/tests/

# Run a single test file or specific test case
pytest fault_analyzer/tests/test_fault_bucketing.py
pytest fault_analyzer/tests/test_fault_bucketing.py::TestEventClassification::test_minimal_creation
```

---

## Project Structure

```
certifier/
│
├── main/                           # FastAPI application layer
│   ├── main.py                     # App factory, MongoDB lifespan, index creation
│   ├── config/settings.py          # Env-var-backed settings singleton
│   ├── models/                     # Pydantic request / response models
│   ├── routers/                    # HTTP route handlers
│   │   ├── bucketing_extraction.py           # POST /api/v1/bucketing-extraction
│   │   └── aggregation_certification.py      # POST /api/v1/aggregation-certification
│   ├── services/
│   │   ├── pipeline_service.py     # CertPipelineService + generate_cert_report_documents()
│   │   └── session_service.py      # Task session CRUD (MongoDB)
│   ├── workers/
│   │   └── cert_task_runner.py     # Background task: phases 2+3 + cert_reporter pipeline
│   └── cli/                        # CLI entry points — no HTTP layer
│
├── fault_analyzer/                 # Phase 0 — LLM fault bucketing
├── metrics_extractor/              # Phase 1 — quantitative + qualitative metrics
├── aggregator/                     # Phase 2 — deterministic stats + LLM Council
├── cert_builder/                   # Phase 3 — 12-section CertificationReport
├── cert_reporter/                  # Report rendering — LangGraph HTML + PDF pipeline
│   ├── main.py                     # serve / generate subcommands
│   ├── api/                        # POST /api/generate/pdf, POST /api/generate/html
│   └── pipeline/                   # LangGraph static + agentic pipelines
├── utils/                          # Shared utilities: AzureLLMClient, ConfigLoader
│
├── workspace/                      # Runtime output root (agent_id/experiment_id/…)
├── configs/configs.json            # Pipeline model config (ENV_ variable references)
├── Dockerfile                      # Multi-stage build (builder + runtime)
├── docker-compose.yml              # API server + MongoDB
├── requirements.txt
└── .env.example                    # Annotated environment variable template
```

---

## Documentation

| Document | Description |
|---|---|
| [API Reference](docs/api.md) | All endpoints, request/response schemas, error codes, usage examples |
| [Architecture](docs/architecture.md) | Full system design — pipeline phases, request flows, concurrency model, design decisions |
| [MongoDB Storage](docs/mongodb-storage.md) | All 5 collections, full schemas, indexes, and per-API write flows |
