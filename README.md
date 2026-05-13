<div align="center">

# AgentCert — Certifier

**Automated certification for AI agents operating under Kubernetes fault injection**

A four-phase analytical pipeline that consumes raw Langfuse traces from AI agents, extracts
per-fault quantitative and qualitative metrics across N experimental runs, aggregates them
with deterministic statistics + LLM-Council synthesis, and produces structured 12-section
certification reports rendered as polished HTML and A4 PDF.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square&logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.127-009688?style=flat-square&logo=fastapi)
![MongoDB](https://img.shields.io/badge/MongoDB-7-47A248?style=flat-square&logo=mongodb)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker)
![LangGraph](https://img.shields.io/badge/LangGraph-0.2%2B-1C3D5A?style=flat-square)
![License](https://img.shields.io/badge/License-Proprietary-lightgrey?style=flat-square)

</div>

---

## Table of Contents

- [What It Does](#what-it-does)
- [Why It Exists](#why-it-exists)
- [Pipeline at a Glance](#pipeline-at-a-glance)
- [Repository Layout](#repository-layout)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
  - [Option A — Docker Compose (recommended)](#option-a--docker-compose-recommended)
  - [Option B — Local Python (no Docker)](#option-b--local-python-no-docker)
- [Running the Pipelines](#running-the-pipelines)
  - [REST API](#rest-api)
  - [CLI](#cli)
- [Module Reference](#module-reference)
- [Workspace Layout](#workspace-layout)
- [Configuration](#configuration)
- [MongoDB Storage Model](#mongodb-storage-model)
- [Report Rendering — cert_reporter](#report-rendering--cert_reporter)
- [Hypothesis Framework](#hypothesis-framework)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Documentation Index](#documentation-index)
- [License](#license)

---

## What It Does

The Certifier is the certification backbone of the **AgentCert** platform. Given the raw
Langfuse trace of one or more agent runs against a chaos-enabled Kubernetes cluster, it:

1. **Classifies** every interleaved trace event into a per-fault lifecycle bucket using a
   reasoning LLM.
2. **Extracts** quantitative metrics (time-to-detect, time-to-recover, token spend, retry
   counts, success outcomes) and qualitative metrics (root-cause reasoning quality,
   recovery strategy, response safety) per fault.
3. **Aggregates** N independent runs into a deterministic statistical scorecard combined
   with multi-judge LLM Council narrative synthesis.
4. **Builds** a Pydantic-validated 12-section certification report (`certification.json`).
5. **Renders** the report into a publication-grade HTML page and a paginated A4 PDF via a
   LangGraph rendering pipeline that supports both static (deterministic) and agentic
   (LLM-enriched) modes.

All of the above is exposed as **async REST endpoints** (submit-then-poll) and as
**standalone CLIs** for one-off runs and scripting.

---

## Why It Exists

Reliability claims about an autonomous agent are only credible when they are:

- **Reproducible** — the same trace data must produce the same scorecard, not a different
  LLM hallucination on every invocation.
- **Statistically sound** — single-run anecdotes are useless; we need distributions across
  N controlled fault injections.
- **Comparable** — every agent under test must be evaluated on the same 12-section
  framework so version-over-version and agent-over-agent comparisons are meaningful.
- **Auditable** — every quantitative claim must trace back to a specific span in a
  specific trace.

The Certifier is built around those four properties: deterministic numeric aggregation,
explicit N-of-N statistical checks, a fixed report schema, and a workspace layout that
preserves every intermediate artifact.

---

## Pipeline at a Glance

```
                ┌──────────────────────────────────────────────────────────────┐
                │                Raw Langfuse Trace (JSON array)               │
                └──────────────────────────────┬───────────────────────────────┘
                                               │
                                               ▼
            ┌───────────────────────────────────────────────────────────────────┐
            │  Phase 0  ─  fault_analyzer/                                       │
            │  Fault Bucketing                                                   │
            │  Reasoning LLM classifies interleaved trace events into per-fault  │
            │  lifecycle buckets (one bucket per detected fault).                │
            │  → workspace/<agent>/<exp>/fault-bucketing/<run>/fault_buckets/    │
            └────────────────────────────────┬───────────────────────────────── ┘
                                             │
                                             ▼
            ┌───────────────────────────────────────────────────────────────────┐
            │  Phase 1  ─  metrics_extractor/                                    │
            │  Per-fault Metrics Extraction                                      │
            │  • Quantitative: TTD, TTR, tokens, retries, success/failure        │
            │  • Qualitative: root-cause reasoning, mitigation strategy, RAI     │
            │  → workspace/<agent>/<exp>/fault-bucketing/<run>/metrics/          │
            │       <fault_id>_metrics.json                                      │
            └────────────────────────────────┬───────────────────────────────── ┘
                                             │   (repeat across N runs)
                                             ▼
            ┌───────────────────────────────────────────────────────────────────┐
            │  Phase 2  ─  aggregator/                                           │
            │  Aggregation                                                       │
            │  • Pure-Python statistics per fault category (mean/median/p95,     │
            │    success rate, stddev) — fully reproducible, no LLM math.        │
            │  • LLM Council: k independent judges + meta-judge consensus for    │
            │    qualitative narrative fields.                                   │
            │  → workspace/<agent>/<exp>/aggregation/aggregation.json            │
            └────────────────────────────────┬───────────────────────────────── ┘
                                             │
                                             ▼
            ┌───────────────────────────────────────────────────────────────────┐
            │  Phase 3  ─  cert_builder/                                         │
            │  Certification Assembly                                            │
            │  • 5 narrative builders run concurrently (asyncio.gather)          │
            │  • Recommendations builder depends on limitations builder          │
            │  • Final output validated against CertificationReport Pydantic     │
            │    schema — invalid output errors out the pipeline.                │
            │  → workspace/<agent>/<exp>/cert-builder/certification.json         │
            └────────────────────────────────┬───────────────────────────────── ┘
                                             │
                                             ▼
            ┌───────────────────────────────────────────────────────────────────┐
            │  Phase 4  ─  cert_reporter/                                        │
            │  Report Rendering (LangGraph)                                      │
            │  • Static mode  → Jinja2 + Altair/Vega charts → HTML → Playwright  │
            │                   → Chromium → A4 PDF                              │
            │  • Agentic mode → LLM-enriched section intros + domain detection   │
            │  → workspace/<agent>/<exp>/certification/<doc_id>.html             │
            │  → workspace/<agent>/<exp>/certification/<doc_id>.pdf              │
            └───────────────────────────────────────────────────────────────────┘
```

Both pipeline pairs (0+1 and 2+3+4) are exposed as **async background tasks**:

- `POST /api/v1/bucketing-extraction` → runs Phase 0 + Phase 1 on one trace
- `POST /api/v1/aggregation-certification` → runs Phase 2 + Phase 3 + Phase 4 over N
  trace metrics in a single call. The rendered HTML/PDF are then available via
  `GET /api/certification/html` and `GET /api/certification/pdf`.

---

## Repository Layout

```
certifier/
│
├── main/                                # FastAPI application layer
│   ├── main.py                          # App factory, Mongo lifespan, index creation
│   ├── config/settings.py               # Env-var-backed Settings dataclass + singleton
│   ├── models/
│   │   ├── bucket_requests.py           # BucketingExtractionRequest + TraceSource union
│   │   ├── bucket_responses.py          # TaskAcceptedResponse
│   │   ├── cert_requests.py             # AggregationCertificationRequest + storage_config
│   │   └── cert_responses.py            # CertTaskAcceptedResponse
│   ├── routers/
│   │   ├── bucketing_extraction.py      # POST /api/v1/bucketing-extraction, GET /tasks
│   │   └── aggregation_certification.py # POST /api/v1/aggregation-certification, GET /cert-tasks
│   ├── services/
│   │   ├── pipeline_service.py          # BucketPipelineService + CertPipelineService
│   │   ├── session_service.py           # SessionService + CertSessionService (Mongo CRUD)
│   │   └── trace_service.py             # File + Langfuse trace acquisition
│   ├── workers/
│   │   ├── bucket_task_runner.py        # BG task: Phase 0+1 + persistence
│   │   └── cert_task_runner.py          # BG task: Phase 2+3 + cert_reporter render
│   └── cli/                             # CLI entry points (no HTTP layer)
│
├── fault_analyzer/                      # Phase 0 — fault bucketing
├── metrics_extractor/                   # Phase 1 — quantitative + qualitative extraction
├── aggregator/                          # Phase 2 — deterministic stats + LLM Council
├── cert_builder/                        # Phase 3 — 12-section CertificationReport assembly
├── cert_reporter/                       # Phase 4 — HTML + PDF rendering (LangGraph)
│   ├── main.py                          # `serve` (default) + `generate` subcommands
│   ├── api/                             # GET /api/certification/{html,pdf}
│   ├── pipeline/                        # LangGraph nodes + agents/
│   ├── templates/                       # Jinja2: base, cover, blocks/, sections/
│   ├── static/report.css                # Inlined into HTML at render time
│   └── prompts/                         # Narrative-enrichment prompts
│
├── hypothesis_framework/                # Statistical hypothesis testing (notebooks + scripts)
├── mock_trace_generator/                # Synthetic traces for offline / CI testing
├── utils/                               # AzureLLMClient, ConfigLoader, MongoDB util, RAI util
│
├── configs/
│   ├── configs.json                     # Global pipeline config (ENV_* references)
│   └── fault_categories.json            # Sub-fault → category mapping
│
├── workspace/                           # Runtime output root: <agent>/<exp>/...
├── docs/                                # api.md, architecture.md, mongodb-storage.md
├── docker-compose.yml                   # API service (mongo is the shared monorepo one)
├── Dockerfile                           # Multi-stage build (builder + runtime)
├── Makefile                             # Docker image build / push / kind-load helpers
├── requirements.txt
└── .env.example                         # Annotated environment-variable template
```

---

## Prerequisites

| Requirement | Version | Required for | Notes |
|---|---|---|---|
| Python | 3.11+ | Local install | |
| MongoDB | 7 | Both paths | Local, Docker, monorepo shared container, or Atlas |
| Docker + Compose | v2+ | Docker path | Builds a multi-stage image including Chromium |
| Azure OpenAI — extraction | GPT-4o or equivalent | Phase 1 + Phase 3 narratives | Configured as `gpt-4o` in `configs/configs.json` |
| Azure OpenAI — reasoning | o1 / o3-mini / gpt-5.x | Phase 0 bucketing + Phase 2 LLM Council | `AzureLLMClient` auto-strips `temperature` for `model_type=reasoning` |
| Azure OpenAI — embedding | text-embedding-3-small (1536 dims) | MongoDB vector search, RAG | |
| Playwright Chromium | bundled | PDF rendering | Already installed inside the Docker image |
| Langfuse | 3.x | `trace_source.type=langfuse` | Optional |
| Azure Content Safety | — | RAI compliance checks | Optional |
| Azure Blob Storage | — | `storage_config.type=blob_storage` or `"hybrid"` | Optional |

---

## Getting Started

### Option A — Docker Compose (recommended)

This is the fastest, most reproducible path. The certifier image bundles Python deps and a
headless Chromium for PDF rendering. MongoDB is **not** bundled — the compose file targets
the shared monorepo mongo started by `scripts/start-local-services.sh` (admin:1234,
replSet `rs0`).

#### 1. Configure credentials

The certifier reads its env from the **monorepo-root** `.env` at
`/srv/projects/ace-monorepo/.env`. The reference template lives at
`certifier/.env.example` — copy values that aren't already present:

```bash
cp /srv/projects/ace-monorepo/.env.example /srv/projects/ace-monorepo/.env
# Edit /srv/projects/ace-monorepo/.env and fill in the three Azure OpenAI blocks
```

Minimum required:

```ini
# ── Extraction model (GPT-4o or equivalent) ──────────────────────────────────
AZURE_OPENAI_ENDPOINT              = https://<resource>.openai.azure.com/
AZURE_OPENAI_API_KEY               = <key>
AZURE_OPENAI_API_VERSION           = 2024-12-01-preview
AZURE_OPENAI_CHAT_DEPLOYMENT_NAME  = gpt-4o

# ── Reasoning model (o1 / o3-mini / gpt-5.x) — temperature stripped automatically
AZURE_OPENAI_GPT5_ENDPOINT             = https://<resource>.openai.azure.com/
AZURE_OPENAI_GPT5_API_KEY              = <key>
AZURE_OPENAI_GPT5_API_VERSION          = 2024-12-01-preview
AZURE_OPENAI_GPT5_CHAT_DEPLOYMENT_NAME = o1-mini

# ── Embedding model ──────────────────────────────────────────────────────────
AZURE_EMBEDDING_ENDPOINT    = https://<resource>.openai.azure.com/
AZURE_EMBEDDING_API_KEY     = <key>
AZURE_EMBEDDING_API_VERSION = 2024-02-01
AZURE_EMBEDDING_MODEL       = text-embedding-3-small
```

> Mongo is auto-wired via `host.docker.internal` and `directConnection=true` so the same
> URI works inside and outside the container. Override with `CERTIFIER_MONGODB_URI` to
> point at a different cluster (e.g. Atlas).

#### 2. Bring up the shared mongo, then the certifier

```bash
# from the monorepo root
./scripts/start-local-services.sh --only-mongo
./scripts/start-local-services.sh --only-certifier
# — or equivalently —
docker compose --env-file ../.env up --build -d
```

| Service | URL |
|---|---|
| API + interactive Swagger docs | `http://localhost:8000/docs` |
| OpenAPI JSON | `http://localhost:8000/openapi.json` |

#### 3. Verify

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' http://localhost:8000/docs
# → 200
```

---

### Option B — Local Python (no Docker)

#### 1. Virtual environment

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
```

#### 2. Install dependencies + Chromium

```bash
pip install -r requirements.txt
python -m playwright install chromium     # required for PDF rendering
```

#### 3. Configure credentials

```bash
cp .env.example .env
# Fill in Azure credentials as in Option A
```

#### 4. Set `PYTHONPATH`

All top-level packages (`main`, `utils`, `fault_analyzer`, `metrics_extractor`,
`aggregator`, `cert_builder`, `cert_reporter`) are imported directly from the repo root —
there is no `setup.py` install step.

```bash
export PYTHONPATH=$(pwd)            # Windows: set PYTHONPATH=%cd%
```

#### 5. Start MongoDB

```bash
docker run -d --name mongo -p 27017:27017 mongo:7
```

Or point `MONGODB_CONNECTION_STRING` in `.env` at any reachable cluster.

#### 6. Launch the API server

```bash
python -m main.main
# Uvicorn reads API_HOST / API_PORT from .env (defaults: 0.0.0.0:8000)
```

---

## Running the Pipelines

### REST API

Both pipelines follow the same **async job pattern**:

```
POST /api/v1/<endpoint>   →  202 Accepted  { task_id, poll_url }
GET  <poll_url>           →  { status: PENDING | RUNNING | COMPLETED | FAILED, ... }
```

A task document is persisted in MongoDB **before** the background worker is dispatched,
so the very first poll after submission is guaranteed to succeed.

#### Phase 0+1 — Fault Bucketing + Metrics Extraction

```bash
# 1. Submit a job
curl -s -X POST http://localhost:8000/api/v1/bucketing-extraction \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id":      "flash-agent",
    "experiment_id": "exp-001",
    "run_id":        "run-42",
    "trace_source": {
      "type":      "file",
      "file_path": "/app/workspace/traces/run42.json"
    },
    "llm_batch_size": 5,
    "storage_config": { "type": "local" }
  }' | jq .
```

```jsonc
{
  "status":   "accepted",
  "task_id":  "550e8400-e29b-41d4-a716-446655440000",
  "poll_url": "/api/v1/tasks?experiment_id=exp-001&experiment_run_id=run-42"
}
```

```bash
# 2. Poll until COMPLETED
curl -s "http://localhost:8000/api/v1/tasks?experiment_id=exp-001&experiment_run_id=run-42" \
  | jq '{status, stage}'
```

Stage progression: `pending → acquiring_trace → running_pipeline → done (COMPLETED)`.

#### Phase 2+3+4 — Aggregation + Certification + Rendering

```bash
# 1. Submit — metrics_dir auto-derives to workspace/<agent>/<exp>/fault-bucketing/
curl -s -X POST http://localhost:8000/api/v1/aggregation-certification \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id":             "flash-agent",
    "agent_name":           "Flash Agent v1.0",
    "experiment_id":        "exp-001",
    "certification_run_id": "v1.0.0",
    "runs_per_fault":       30,
    "storage_config":       { "type": "local" }
  }' | jq .
```

```jsonc
{
  "status":       "accepted",
  "cert_task_id": "7c4a8d64-...",
  "poll_url":     "/api/v1/cert-tasks?experiment_id=exp-001"
}
```

```bash
# 2. Poll until COMPLETED
curl -s "http://localhost:8000/api/v1/cert-tasks?experiment_id=exp-001" \
  | jq '{status, stage, storage_paths: .result.storage_paths}'
```

The `result.storage_paths` field includes `aggregation`, `certification_json`,
`html_report`, and `pdf_report`.

#### Retrieve rendered reports

```bash
# Most recent HTML for an agent/experiment
curl -fsS "http://localhost:8000/api/certification/html?agent_id=flash-agent&experiment_id=exp-001" \
  -o report.html

# Most recent PDF
curl -fsS "http://localhost:8000/api/certification/pdf?agent_id=flash-agent&experiment_id=exp-001" \
  -o report.pdf
```

#### Trace sources

| `type` | Fields | Behaviour |
|---|---|---|
| `"file"` | `file_path` | Read a Langfuse trace JSON array from a server-side path |
| `"langfuse"` | `page_size`, `max_pages`, `include_observations` | Pull traces live from Langfuse; credentials read from `LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY` at launch (never accepted in the request body) |

#### Duplicate-submission protection

| Status | Cause |
|---|---|
| `409 TASK_ALREADY_ACTIVE` | A `PENDING` or `RUNNING` task already exists for the same `(agent_id, experiment_id, run_id)` (Phase 0+1) or `(agent_id, experiment_id)` (Phase 2+3+4) |
| `400 METRICS_NOT_FOUND` | Local-mode submission: `metrics_dir` is missing or contains no `*metrics.json` documents for the requested `agent_id` |
| `404 TASK_NOT_FOUND` | Polled `(experiment_id, run_id)` was never submitted |
| `500 MONGODB_ERROR` | Failure persisting the initial task session |

---

### CLI

The CLIs invoke the pipeline services directly — no running API server, no MongoDB
session document. Useful for development, one-off runs, post-mortem reruns, and scripting.

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
    --output-dir   workspace/cert/flash-agent/exp-001 \
    --agent-id     flash-agent \
    --agent-name   "Flash Agent v1.0" \
    --certification-run-id v1.0.0 \
    --runs-per-fault 30
```

| Flag | Required | Default | Description |
|---|---|---|---|
| `--metrics-dir` | Yes | — | Directory containing `*metrics.json` files (recursive) |
| `--output-dir` | Yes | — | Root output directory for scorecard + report |
| `--agent-id` | Yes | — | Must match the `agent_id` in the metrics documents |
| `--agent-name` | Yes | — | Human-readable name written into the report header |
| `--certification-run-id` | No | `""` | Caller-supplied identifier (e.g. git SHA) |
| `--runs-per-fault` | No | `30` | Expected N per fault — used in statistical-significance checks |
| `--debug` | No | off | Retain intermediate outputs for post-mortem inspection |

#### Phase 4 — render only

When `certification.json` already exists (for example, after an earlier failed render):

```bash
cd cert_reporter
python main.py generate \
    --agent-id flash-agent \
    --experiment-id exp-001 \
    --format html,pdf \
    --mode static
```

| Flag | Default | Description |
|---|---|---|
| `--mode` | `static` | `static` (deterministic) or `agentic` (LLM-enriched section intros) |
| `--enrich-llm` | off | Enable narrative enrichment in static mode |
| `--provider` | `openai` | `openai` or `anthropic` |
| `--model` | `gpt-4.1-mini` | Model used for enrichment |
| `--temperature` | `0.4` | LLM temperature |

---

## Module Reference

| Module | Phase | Responsibility |
|---|---|---|
| [`fault_analyzer/`](fault_analyzer/) | 0 | Reasoning LLM classifies interleaved trace events into per-fault lifecycle buckets. Output: one bucket JSON per detected fault plus a manifest. |
| [`metrics_extractor/`](metrics_extractor/) | 1 | Extracts **quantitative** metrics (TTD, TTR, tokens, retries, success outcome, detection success) and **qualitative** metrics (reasoning quality, recovery strategy, RAI/safety) per fault. Output: `<fault_id>_metrics.json` (or MongoDB row). |
| [`aggregator/`](aggregator/) | 2 | Aggregates N runs per fault category. **Numeric stats are pure Python** (mean, median, p95, success rate, stddev) — never asked of an LLM. **LLM Council** (k independent judges + meta-judge consensus) synthesises qualitative narrative fields. Output: `aggregation.json`. |
| [`cert_builder/`](cert_builder/) | 3 | Assembles the 12-section certification report from the scorecard. 5 narrative builders run concurrently via `asyncio.gather`; the recommendations builder waits on limitations (explicit dependency). Final output is validated against the `CertificationReport` Pydantic schema — schema failures abort the pipeline rather than emitting a malformed report. |
| [`cert_reporter/`](cert_reporter/) | 4 | LangGraph rendering pipeline. Static mode: Jinja2 + Altair/Vega → HTML → Playwright/Chromium → A4 PDF. Agentic mode: domain inspector + per-section LLM enrichment + planner. Endpoints serve the most-recent rendered file from the workspace. |
| [`hypothesis_framework/`](hypothesis_framework/) | Optional | Statsmodels-based hypothesis tests over aggregated metrics (parametric + non-parametric); usable as a standalone library or via demo notebooks. |
| [`utils/`](utils/) | All | Shared: `AzureLLMClient` (model-type-aware), `ConfigLoader` (ENV_* resolver), `mongodb_util`, `rai_util`, `file_storage`, `setup_logging`, `custom_errors`. |
| [`mock_trace_generator/`](mock_trace_generator/) | Test | Synthetic traces for unit-testing the pipeline without making Azure calls. |

### Key design patterns

- **Deterministic numeric aggregation.** No LLM is ever asked to perform arithmetic.
  Every statistic in the scorecard is computed in pure Python and is bit-for-bit
  reproducible given the same input metrics files.
- **LLM Council for qualitative synthesis.** k independent judges propose narrative
  fields; a meta-judge resolves disagreement. Prompts live in `aggregator/prompt/`.
- **Storage-agnostic query interface.** `DirectoryQueryService` (file-based) and
  `MetricsQueryService` (MongoDB) implement the same interface — the Phase-2 orchestrator
  doesn't know which backend it is talking to. Switching is a single config flag.
- **Reasoning-model handling.** `AzureLLMClient.generate(...)` checks
  `model_type == "reasoning"` in `configs/configs.json` and strips `temperature` before
  the call, so o-series deployments work transparently.
- **Concurrent narrative generation.** Phase 3 fan-outs 5 narrative builders via
  `asyncio.gather`. The recommendations builder runs **after** limitations because it
  depends on its output.
- **Strict schema validation at the boundary.** Phase 3 validates its final output
  against `CertificationReport`. A validation failure fails the pipeline rather than
  shipping a malformed report downstream.
- **Concurrency caps.** `API_MAX_CONCURRENT_TASKS` (Phase 0+1) and
  `API_MAX_CONCURRENT_CERT_TASKS` (Phase 2+3+4) are enforced via `asyncio.Semaphore` so a
  burst of submissions can't exhaust LLM quotas or memory.

---

## Workspace Layout

Both pipelines write everything they produce under a deterministic per-`(agent,
experiment)` tree. This is the contract between the API, the CLI, and the rendering
pipeline — every consumer can locate every artifact without an out-of-band lookup.

```
workspace/
└── <agent_id>/
    └── <experiment_id>/
        ├── fault-bucketing/                        ← Phase 0+1 output
        │   └── <run_id>/
        │       ├── traces/
        │       │   └── raw_trace.json
        │       ├── fault_buckets/
        │       │   └── <fault_id>_bucket.json      ← one per detected fault
        │       ├── metrics/
        │       │   └── <fault_id>_metrics.json     ← input for Phase 2
        │       ├── pipeline_summary.json
        │       └── pipeline.log
        │
        ├── aggregation/                            ← Phase 2 output
        │   └── aggregation.json                    ← CertificationScorecard
        │
        ├── cert-builder/                           ← Phase 3 output
        │   └── certification.json                  ← input for Phase 4 + schema-validated
        │
        └── certification/                          ← Phase 4 output
            ├── <doc_id>.html                       ← rendered report
            └── <doc_id>.pdf                        ← paginated A4 PDF
```

Override the root via `WORKSPACE_DIR` (Phase 0+1) and `CERT_WORKSPACE_DIR` (Phase 2+3+4).
Inside the container both are remapped to `/app/workspace` and `/app/workspace/cert`.

---

## Configuration

### Global pipeline config — `configs/configs.json`

Every value prefixed with `ENV_` is resolved from the environment at load time by
`ConfigLoader` (`utils/load_config.py`). Three model entries are exposed to the pipeline:

| Key | Purpose | Used by |
|---|---|---|
| `models.embedding_model` | Embeddings for MongoDB vector search and RAG | Phase 1 qualitative, Phase 2 retrieval |
| `models.gpt-4o` (`model_type: standard`) | Extraction + narrative model | Phase 1, Phase 3 |
| `models.gpt-5.2` (`model_type: reasoning`) | Reasoning model (o-series) | Phase 0, Phase 2 LLM Council |

`storage_connections.connection_str` (resolved from `AZURE_STORAGE_CONNECTION_STRING`) is
only used when `storage_config.type` is `blob_storage` or `hybrid`.

### Required environment variables

| Variable | Description |
|---|---|
| `MONGODB_CONNECTION_STRING` | Motor/PyMongo connection string (server fails fast on startup if missing) |
| `AZURE_OPENAI_ENDPOINT` / `_API_KEY` / `_API_VERSION` / `_CHAT_DEPLOYMENT_NAME` | Extraction model |
| `AZURE_OPENAI_GPT5_ENDPOINT` / `_API_KEY` / `_API_VERSION` / `_CHAT_DEPLOYMENT_NAME` | Reasoning model |
| `AZURE_EMBEDDING_ENDPOINT` / `_API_KEY` / `_API_VERSION` / `AZURE_EMBEDDING_MODEL` | Embedding model |

### Optional environment variables

| Variable | Default | Description |
|---|---|---|
| `MONGODB_DATABASE` | `agentcert` | Database name |
| `API_HOST` | `0.0.0.0` | Uvicorn bind host |
| `API_PORT` | `8000` | Uvicorn bind port |
| `WORKSPACE_DIR` | `workspace` | Phase 0+1 output root |
| `CERT_WORKSPACE_DIR` | `workspace/cert` | Phase 2+3+4 output root |
| `API_MAX_CONCURRENT_TASKS` | `4` | Max simultaneous Phase 0+1 runs |
| `API_MAX_CONCURRENT_CERT_TASKS` | `2` | Max simultaneous Phase 2+3+4 runs (heavier) |
| `API_TASK_COLLECTION` | `pipeline_tasks` | Mongo collection — bucketing tasks |
| `CERT_TASK_COLLECTION` | `certification_tasks` | Mongo collection — cert tasks |
| `CERT_METADATA_COLLECTION` | `certification_metadata` | One doc per completed cert run |
| `AGG_CATEGORY_COLLECTION` | `aggregated_category_metadata` | One row per fault category per cert |
| `AZURE_CONTENT_SAFETY_ENDPOINT` / `_API_KEY` | — | Required for RAI compliance checks |
| `AZURE_STORAGE_CONNECTION_STRING` | — | Required for `storage_config.type = "blob_storage"` |
| `LANGFUSE_HOST` / `_PUBLIC_KEY` / `_SECRET_KEY` | — | Required when `trace_source.type = "langfuse"` |
| `PYTHONPATH` | — | Must include repo root; set automatically in Docker (`/app`) |
| `CERTIFIER_IMAGE` | `certifier:latest` | Pull a published image instead of building locally |
| `CERTIFIER_MONGODB_URI` | derived | Override the Mongo URI from outside the compose file |

See [`.env.example`](.env.example) for the fully annotated list.

### Fault taxonomy

[`configs/fault_categories.json`](configs/fault_categories.json) maps each injected sub-fault
to one of three top-level categories. The aggregator uses this map to group flat per-run
metrics when `fault_category` is missing from a document:

| Category | Sub-faults |
|---|---|
| `application_fault` | `node-restart`, `pod-delete` |
| `network_fault` | `pod-dns-error`, `pod-network-corruption`, `pod-network-loss`, `pod-network-rate-limit` |
| `resource_fault` | `disk-fill`, `pod-autoscaler`, `pod-cpu-hog`, `pod-memory-hog` |

---

## MongoDB Storage Model

The certifier maintains five primary collections plus one GridFS bucket. All indexes are
created idempotently in the FastAPI lifespan; index-conflict errors (Mongo codes 85/86)
are silently ignored so repeated startups are safe.

| Collection | Document | Indexes |
|---|---|---|
| `pipeline_tasks` | One per Phase 0+1 submission | unique `task_id`; `(agent, exp, run)`; `(status, created_at desc)`; `created_at` |
| `certification_tasks` | One per Phase 2+3+4 submission | unique `cert_task_id`; `(agent, exp)`; `(status, created_at desc)`; `created_at` |
| `certification_metadata` | One per completed certification run | unique `certification_id`; `(agent, exp)`; `(agent, created_at desc)`; sparse `certification_run_id` |
| `aggregated_category_metadata` | One per fault-category per certification | composite unique `(certification_id, fault_category)`; `(agent, exp)`; `created_at desc` |
| `agent_run_metrics` | Per-fault extracted metrics (Phase 1) | configurable in `configs/configs.json` |
| `cert_reports` (GridFS bucket) | HTML/PDF reports when `storage_config.type = "mongodb"` | n/a |

Full schemas and per-API write flows are documented in
[`docs/mongodb-storage.md`](docs/mongodb-storage.md).

---

## Report Rendering — `cert_reporter`

The renderer is a LangGraph `StateGraph` with five nodes:

```
preprocess  →  charts  →  llm_enrich  →  html_renderer  →  pdf_renderer
```

| Node | Responsibility |
|---|---|
| `preprocess_node` | Loads and parses `certification.json`, normalises against the Pydantic schema |
| `charts_node` | Renders chart blocks via Altair → Vega → SVG (no client-side JS needed) |
| `llm_enrich_node` | Optional: rewrites section introductions using LangChain (`--enrich-llm`) |
| `html_renderer_node` | Jinja2 templates: `base.html` + `cover.html` + `sections/section.html` + 15 block templates |
| `pdf_renderer_node` | Playwright launches headless Chromium and prints A4 |

Two pipeline modes:

- **Static** — deterministic, no LLM required. Optional `--enrich-llm` adds section-intro
  rewrites.
- **Agentic** — LLM-driven domain detection (`agents/inspector.py`), report planning
  (`agents/planner.py`), and per-section enrichment (`agents/section_writer.py`).

Block templates currently supported (in [`cert_reporter/templates/blocks/`](cert_reporter/templates/blocks/)):

```
assessment, card, category_panel, chart, enumerated_item, _fallback,
fault_group, fault_pills, findings, heading, hypothesis_strip,
identity_card, interpretation_scale, notice, part_banner, scope_stats,
table, taxonomy_table, text
```

See [`cert_reporter/README.md`](cert_reporter/README.md), [`cert_reporter/ARCHITECTURE.md`](cert_reporter/ARCHITECTURE.md),
and [`cert_reporter/SCHEMA.md`](cert_reporter/SCHEMA.md) for the rendering pipeline in depth.

---

## Hypothesis Framework

[`hypothesis_framework/`](hypothesis_framework/) layers Statsmodels-based hypothesis testing on
top of aggregated metrics. It is wired into Phase 2 but can also be driven directly:

```bash
python -m hypothesis_framework.scripts.run_statistical_hypothesis \
    --metrics-dir workspace/<agent>/<exp>/fault-bucketing \
    --agent-id    <agent_id>
```

Notebooks under `hypothesis_framework/notebooks/` walk through individual tests
(parametric and non-parametric) against demo metrics in `hypothesis_framework/data/`.

---

## Testing

```bash
# Full suite
pytest

# Per-module
pytest fault_analyzer/tests/
pytest metrics_extractor/tests/
pytest aggregator/tests/
pytest cert_builder/tests/
pytest cert_reporter/tests/

# A single file, or a single test case
pytest fault_analyzer/tests/test_fault_bucketing.py
pytest fault_analyzer/tests/test_fault_bucketing.py::TestEventClassification::test_minimal_creation

# API smoke tests (requires the API server running on localhost:8000)
python test_api.py
```

The mock trace generator under [`mock_trace_generator/`](mock_trace_generator/) lets the
Phase-0 tests run without making real Azure OpenAI calls.

---

## Troubleshooting

**`MONGODB_CONNECTION_STRING` not set → server crashes on startup.**
The setting is mandatory — see `main/config/settings.py`. Set it in `.env` (or in the
monorepo-root `.env` when using compose).

**`pw.chromium.launch()` fails inside Docker.**
The runtime Dockerfile installs every shared library Playwright's bundled Chromium needs.
If you've extended the image, make sure `libnss3`, `libatk1.0-0`, `libgbm1`,
`libpango-1.0-0`, `libcairo2`, `libasound2`, etc. are still present. The browser binaries
themselves live at `/opt/playwright-browsers` (the `PLAYWRIGHT_BROWSERS_PATH` env var).

**`409 TASK_ALREADY_ACTIVE` on resubmit.**
A previous submission for the same `(agent, exp, run)` is still `PENDING` or `RUNNING`.
Poll its status and wait — or fail it out in `pipeline_tasks` / `certification_tasks`
before resubmitting.

**`400 METRICS_NOT_FOUND` on a cert submission.**
The local-mode pre-flight in
[`main/routers/aggregation_certification.py`](main/routers/aggregation_certification.py)
recursively globs for `*metrics.json` under `metrics_dir` and counts those whose top-level
or `quantitative.agent_id` matches your `agent_id`. Common causes: the Phase 0+1 job for
this experiment never completed, `agent_id` is misspelled, or `metrics_dir` points at the
wrong workspace root.

**LLM Council "reasoning model" calls fail with `temperature` errors.**
`AzureLLMClient` strips `temperature` automatically when
`models.<name>.model_type == "reasoning"` in `configs/configs.json`. If you swapped in an
o-series deployment, make sure its entry in the config has that key.

**Pipeline emits `ValidationError` at the end of Phase 3.**
This is intentional: the report assembler validates against `CertificationReport` and
fails the run rather than ship a malformed report. The traceback identifies the offending
field; fix the upstream builder (usually a narrative builder returning the wrong shape).

**Compose: certifier can't reach the host's Mongo.**
The compose file maps `host.docker.internal:host-gateway` via `extra_hosts` and uses
`directConnection=true` to bypass the shared replica set's `localhost:27017` advertised
host. If you're not using the shared monorepo Mongo, set `CERTIFIER_MONGODB_URI` to your
target URI.

---

## Documentation Index

| Document | Description |
|---|---|
| [`docs/api.md`](docs/api.md) | Full HTTP API reference — endpoints, request/response schemas, error codes, examples |
| [`docs/architecture.md`](docs/architecture.md) | End-to-end system design: pipeline phases, request flows, concurrency model, design decisions |
| [`docs/mongodb-storage.md`](docs/mongodb-storage.md) | All collections, schemas, indexes, and per-API write flows |
| [`docs/polling-api-redesign.md`](docs/polling-api-redesign.md) | Notes on the move to the current submit-then-poll job pattern |
| [`docs/api-changes-features-api-fixes.md`](docs/api-changes-features-api-fixes.md) | Change log of API-surface changes |
| [`cert_reporter/README.md`](cert_reporter/README.md) | Rendering pipeline quick start |
| [`cert_reporter/ARCHITECTURE.md`](cert_reporter/ARCHITECTURE.md) | Rendering pipeline internals |
| [`cert_reporter/SCHEMA.md`](cert_reporter/SCHEMA.md) | `certification.json` schema reference |
| [`CLAUDE.md`](CLAUDE.md) | Repository pointers for Claude Code |

---

## License

Proprietary — © AgentCert. See [LICENSE](LICENSE) for the full text.
