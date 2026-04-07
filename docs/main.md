# `main/` — AgentCert REST API Layer

This document covers every component inside `main/`: what it does, how the
pieces fit together, the full task lifecycle, and everything a developer needs
to extend or debug the API.

---

## Table of Contents

1. [Overview](#overview)
2. [Directory Layout](#directory-layout)
3. [Startup & Lifespan (`main.py`)](#startup--lifespan-mainpy)
4. [Configuration (`config/settings.py`)](#configuration-configsettingspy)
5. [Request Models (`models/`)](#request-models-models)
6. [Routers (`routers/`)](#routers-routers)
7. [Services (`services/`)](#services-services)
8. [Workers (`workers/`)](#workers-workers)
9. [CLI Entry Points (`cli/`)](#cli-entry-points-cli)
10. [Task Lifecycle & State Machine](#task-lifecycle--state-machine)
11. [MongoDB Collections](#mongodb-collections)
12. [Concurrency Model](#concurrency-model)
13. [Error Codes Reference](#error-codes-reference)
14. [Environment Variables](#environment-variables)
15. [Adding a New Pipeline](#adding-a-new-pipeline)

---

## Overview

`main/` is the **FastAPI application layer** that wraps the four-phase certifier
pipeline behind a REST API.  It provides two asynchronous job endpoints:

| Endpoint | Pipeline | Phases |
|---|---|---|
| `POST /api/v1/bucketing-extraction` | Fault bucketing → Metrics extraction | 0 + 1 |
| `POST /api/v1/aggregation-certification` | Aggregation → Certification report | 2 + 3 |

Both endpoints follow the same **async job pattern**:

1. Client `POST`s a request → server validates, persists a task doc, returns `202 Accepted` with a `task_id`.
2. A background coroutine runs the heavy pipeline work (LLM calls, filesystem I/O).
3. Client polls `GET /api/v1/tasks/{task_id}` (or `/cert-tasks/{id}`) until `status` is `COMPLETED` or `FAILED`.

---

## Directory Layout

```
main/
├── main.py                   # FastAPI app factory + lifespan (MongoDB init)
├── config/
│   └── settings.py           # Env-var-backed Settings dataclass + singleton
├── models/
│   ├── bucket_requests.py    # Pydantic request model for bucketing-extraction
│   ├── bucket_responses.py   # Pydantic response models for bucketing-extraction
│   ├── cert_requests.py      # Pydantic request model for aggregation-certification
│   └── cert_responses.py     # Pydantic response models for aggregation-certification
├── routers/
│   ├── bucketing_extraction.py      # POST /bucketing-extraction, GET /tasks/{id}
│   └── aggregation_certification.py # POST /aggregation-certification, GET /cert-tasks/{id}
├── services/
│   ├── session_service.py    # MongoDB CRUD + state-machine transitions for tasks
│   ├── trace_service.py      # Trace acquisition (file copy or Langfuse fetch)
│   └── pipeline_service.py   # Orchestration services for Phase 0+1 and Phase 2+3
├── workers/
│   ├── bucket_task_runner.py # Background coroutine for bucketing-extraction task
│   └── cert_task_runner.py   # Background coroutine for aggregation-certification task
└── cli/
    ├── run_bucketing_and_extraction_pipeline.py      # CLI for Phase 0+1
    └── run_aggregation_and_certification_pipeline.py # CLI for Phase 2+3
```

---

## Startup & Lifespan (`main.py`)

`main.py` defines the FastAPI `lifespan` context manager that runs **once on
startup** and **once on shutdown**:

```
Startup order
─────────────
1. ConfigLoader.load_config()        → resolves ENV_ vars from configs/configs.json
2. AsyncIOMotorClient(mongodb_uri)   → opens async MongoDB connection pool
3. _ensure_indexes(pipeline_tasks)   → creates / idempotently verifies 4 indexes
4. _ensure_cert_task_indexes(cert_tasks)
5. _ensure_cert_metadata_indexes(cert_metadata)
6. _ensure_agg_category_indexes(aggregated_category_metadata)
7. asyncio.Semaphore × 2             → caps concurrent pipeline executions
8. workspace dirs created            → workspace/ and workspace/cert/
```

All app-level state (DB collection handles, settings, semaphores) is stored on
`app.state` and injected into request handlers via FastAPI's dependency system.

### Index idempotency

`_apply_indexes` (called by all four `_ensure_*_indexes` functions) silently
swallows MongoDB `OperationFailure` codes **85** (`IndexOptionsConflict`) and
**86** (`IndexKeySpecsConflict`).  These mean an index already exists under a
different name — safe to ignore on repeated restarts.  Any other `OperationFailure`
is re-raised immediately.

---

## Configuration (`config/settings.py`)

`Settings` is a `@dataclass` whose fields each call `os.environ[…]` or
`os.getenv(…, default)` in their `default_factory`.  This means env-var reads
happen **at instantiation time**, not at import time — making it safe to patch
the environment in tests before calling `get_settings()`.

`get_settings()` returns a **process-wide singleton**.  Call it once in
`lifespan` and attach the result to `app.state.settings`; do not call it
inside hot request paths.

| Field | Env var | Default | Description |
|---|---|---|---|
| `mongodb_uri` | `MONGODB_CONNECTION_STRING` | *(required)* | Motor connection string |
| `mongodb_database` | `MONGODB_DATABASE` | `agentcert` | Database name |
| `task_collection` | `API_TASK_COLLECTION` | `pipeline_tasks` | Bucketing task docs |
| `workspace_dir` | `WORKSPACE_DIR` | `workspace/` | Per-run output root |
| `max_concurrent_tasks` | `API_MAX_CONCURRENT_TASKS` | `4` | Bucketing semaphore size |
| `host` | `API_HOST` | `0.0.0.0` | Uvicorn bind host |
| `port` | `API_PORT` | `8000` | Uvicorn bind port |
| `cert_task_collection` | `CERT_TASK_COLLECTION` | `certification_tasks` | Cert task docs |
| `cert_metadata_collection` | `CERT_METADATA_COLLECTION` | `certification_metadata` | Per-run cert results |
| `agg_category_collection` | `AGG_CATEGORY_COLLECTION` | `aggregated_category_metadata` | Per-category scorecard rows |
| `cert_workspace_dir` | `CERT_WORKSPACE_DIR` | `workspace/cert/` | Cert output root |
| `max_concurrent_cert_tasks` | `API_MAX_CONCURRENT_CERT_TASKS` | `2` | Cert semaphore size |

---

## Request Models (`models/`)

### Bucketing-Extraction

**`BucketingExtractionRequest`** (`bucket_requests.py`)

```json
{
  "agent_id": "my-agent",
  "experiment_id": "exp-001",
  "run_id": "run-42",
  "trace_source": {
    "type": "file",
    "file_path": "/data/traces/run42.json"
  },
  "llm_batch_size": 5,
  "storage_config": { "type": "local" }
}
```

`trace_source` is a **discriminated union** on the `type` field:

- `"file"` → `FileTraceSource`: path to a JSON array already on the server.
- `"langfuse"` → `LangfuseTraceSource`: fetches observations from a live
  Langfuse instance using the provided API keys.

`experiment_id` and `run_id` are validated against path-separator characters
(`/`, `\`, `..`) to prevent directory traversal.

The Langfuse `secret_key` is **stripped** from the MongoDB request snapshot
before persistence (see `bucketing_extraction.py` router).

**`TaskAcceptedResponse`** / **`TaskStatusResponse`** (`bucket_responses.py`)

`TaskAcceptedResponse` contains `task_id` and `poll_url`.
`TaskStatusResponse` mirrors the full MongoDB task document (used by the poll endpoint).

---

### Aggregation-Certification

**`AggregationCertificationRequest`** (`cert_requests.py`)

```json
{
  "agent_id": "my-agent",
  "agent_name": "My Kubernetes Agent v2",
  "experiment_id": "exp-001",
  "certification_run_id": "v2.1.0",
  "runs_per_fault": 30,
  "storage_config": {
    "type": "local",
    "metrics_dir": "/data/workspace/exp-001/run-42/metrics"
  }
}
```

`storage_config.metrics_dir` must be a directory containing `*metrics.json`
files from Phase 1.  The router validates this **before** creating a task
(see Metrics Pre-flight in [Routers](#routers-routers)).

**`CertTaskAcceptedResponse`** / **`CertTaskStatusResponse`** (`cert_responses.py`)

Mirror `TaskAcceptedResponse` / `TaskStatusResponse` but for the cert pipeline.

---

## Routers (`routers/`)

### `bucketing_extraction.py`

| Route | Method | Description |
|---|---|---|
| `/api/v1/bucketing-extraction` | POST | Submit a fault bucketing + metrics extraction job |
| `/api/v1/tasks/{task_id}` | GET | Poll task status |

**Submit flow:**
1. `find_active_task(experiment_id, run_id)` — 409 if already active.
2. Strip Langfuse `secret_key` from snapshot.
3. `session_svc.create_task(...)` — insert PENDING doc.
4. `background_tasks.add_task(run_task, ...)` — enqueue worker.
5. Return `202` with `task_id` + `poll_url`.

---

### `aggregation_certification.py`

| Route | Method | Description |
|---|---|---|
| `/api/v1/aggregation-certification` | POST | Submit an aggregation + certification job |
| `/api/v1/cert-tasks/{cert_task_id}` | GET | Poll certification task status |

**Submit flow:**
1. Validate `storage_config.type == "local"` (400 otherwise).
2. **Metrics pre-flight** (in thread): `_discover_and_validate(metrics_dir, agent_id)` —
   scans `*metrics.json` files, raises `MetricsValidationError` (400) if none match.
3. `find_active_task(agent_id, experiment_id)` — 409 if already active.
4. `cert_session_svc.create_task(...)` — insert PENDING doc (500 on DB error).
5. `background_tasks.add_task(run_cert_task, ...)` — enqueue worker.
6. Return `202` with `cert_task_id` + `poll_url`.

**`MetricsValidationError`** is a typed exception class (not a string-encoded
error code) so the router handler can cleanly extract `exc.error_code`.

---

## Services (`services/`)

### `session_service.py`

Contains two independent classes with identical APIs:

| Class | Collection | Used by |
|---|---|---|
| `SessionService` | `pipeline_tasks` | Bucketing-extraction pipeline |
| `CertSessionService` | `certification_tasks` | Aggregation-certification pipeline |

Both implement the same state-machine methods:

```
create_task()     → insert PENDING document
set_started()     → PENDING → RUNNING   (initial stage set)
update_stage()    → update stage label (no status change)
set_completed()   → RUNNING → COMPLETED (raises ValueError if not RUNNING)
set_failed()      → PENDING|RUNNING → FAILED
get_task()        → fetch by ID
find_active_task()→ find PENDING|RUNNING for a key pair
```

All `update_one` calls include a **status guard** in the filter so concurrent
writes cannot double-advance a task (optimistic concurrency without transactions).

---

### `trace_service.py`

`TraceService.acquire_trace(trace_source, dest_dir)` writes `raw_trace.json`
to `dest_dir` and returns `(path, observation_count)`.

**File source**: copies the file with `shutil.copy2` (preserves metadata) inside
`asyncio.to_thread`.

**Langfuse source**: calls `_fetch_langfuse_observations` in a thread (the
Langfuse SDK is synchronous).  Paginates through the trace list API up to
`max_pages`, then fetches up to 500 observations per trace.  Normalises the
result with `_format_observations`:

- Computes `depth` for each observation by walking up the parent chain (memoised).
- Normalises timestamps to `YYYY-MM-DDTHH:MM:SS.mmmZ` UTC.
- Serialises `input`/`output`/`metadata` as JSON strings if they are dicts.
- Sorts by `(depth, startTime)` so parents always precede children.

All public errors raise `TraceIngestionError(error_code, message)` with one of:
`TRACE_NOT_FOUND`, `TRACE_PARSE_ERROR`, `LANGFUSE_FETCH_ERROR`.

---

### `pipeline_service.py`

Contains two service classes:

#### `BucketPipelineService.execute_pipeline(...)`

Runs Phase 0+1:

```
1. FaultBucketingPipeline.run()          → produces fault buckets dict
2. For each bucket:
   a. Write events to *_trace.json (temp)
   b. _build_fault_config_from_bucket()  → assemble fault config dict
   c. Write fault config to *_fault_config.json (temp)
   d. TraceMetricsExtractor.extract_metrics_async() → ExtractionResult
   e. Write *_metrics.json
3. Write pipeline_summary.json
4. Return list of per-fault result dicts
```

`_build_fault_config_from_bucket` normalises bucket metadata into the schema
expected by `TraceMetricsExtractor`, promoting top-level `ideal_course_of_action`
and `ideal_tool_usage_trajectory` fields into `ground_truth`.

#### `CertPipelineService.execute_pipeline(...)`

Runs Phase 2+3:

```
1. DirectoryQueryService(metrics_dir)         → file-based query backend
2. query_service.query_runs_by_agent(agent_id)→ load all per-run docs
3. AggregationOrchestrator.aggregate_all()    → CertificationScorecard dict
4. Save aggregated_scorecard_output_{agent_id}.json
5. CertificationPipeline.run()               → CertificationReport dict
6. Save certification_report_{agent_id}.json + pipeline_summary.json
7. Return report dict
```

The `AzureLLMClient` is always closed in a `finally` block to release the
connection pool even on exception.

---

## Workers (`workers/`)

Workers are `async def` coroutines passed to FastAPI's `BackgroundTasks`.
They drive a task through its stages and call the appropriate `session_svc`
methods to record transitions and errors.  **They never raise** — all exceptions
are caught and written to the task document as `FAILED`.

### `bucket_task_runner.run_task`

```
set_started(task_id)
  │
  ▼ Stage: acquiring_trace
trace_svc.acquire_trace(trace_source, run_dir/traces/)
  │ on TraceIngestionError → set_failed(error_code)
  ▼ Stage: running_pipeline
async with semaphore:
  pipeline_svc.execute_pipeline(...)
  _build_result(...)
set_completed(task_id, result)
```

`_resolve_run_dir` creates `workspace/{experiment_id}/{run_id}/` and validates
both path segments against traversal characters (defence-in-depth on top of
Pydantic validation).

`_build_result` assembles the final task result dict from the pipeline output,
mapping `fault_detected == "Yes"` to `status: "closed"` and everything else
to `status: "open"`.

---

### `cert_task_runner.run_cert_task`

```
set_started(cert_task_id)
  │
  ▼ resolve_cert_output_dir(cert_workspace_dir, agent_id, experiment_id)
  │ on ValueError → set_failed("INVALID_REQUEST")
  ▼ Stage: running_pipeline  (inside cert_semaphore)
cert_pipeline_svc.execute_pipeline(...)
  │ on Exception  → set_failed(classify_cert_error(exc))
  │ empty result  → set_failed("METRICS_NOT_FOUND")
  ▼ Stage: storing_metadata
_write_certification_metadata(cert_meta_col, ...)
_write_aggregated_category_metadata(agg_cat_col, ...)
  │ on Exception  → set_failed("STORAGE_ERROR")
  ▼
set_completed(cert_task_id, task_result)
```

`classify_cert_error` maps exception messages to structured error codes:
`AGGREGATION_FAILED`, `CERT_GENERATION_FAILED`, `STORAGE_ERROR`, `PIPELINE_FAILED`.

`_write_aggregated_category_metadata` reads the scorecard JSON from disk and
fans out one MongoDB document per `fault_category_scorecards` entry.

---

## CLI Entry Points (`cli/`)

Both CLIs bypass the HTTP layer and call the service classes directly.  Useful
for local development, one-off runs, and testing without a running server.

### `run_bucketing_and_extraction_pipeline.py`

```bash
python -m main.cli.run_bucketing_and_extraction_pipeline \
    --trace-file /data/traces/run.json \
    --output-dir /tmp/output \
    [--batch-size 10] [--store]
```

### `run_aggregation_and_certification_pipeline.py`

```bash
python -m main.cli.run_aggregation_and_certification_pipeline \
    --metrics-dir /tmp/output/metrics \
    --output-dir /tmp/cert_out \
    --agent-id my-agent \
    --agent-name "My Agent v2" \
    [--certification-run-id v2.1.0] [--runs-per-fault 30] [--debug]
```

---

## Task Lifecycle & State Machine

```
                    ┌─────────────────────────────────────────────┐
                    │              Task States                     │
                    │                                             │
         POST /...  │  create_task()                              │
  Client ──────────►│  PENDING ──► RUNNING ──► COMPLETED          │
                    │     │           │                           │
                    │     └───────────┴──────► FAILED             │
                    └─────────────────────────────────────────────┘
```

### Bucketing-Extraction stages

| Status | Stage | Description |
|---|---|---|
| PENDING | `pending` | Task created, not yet picked up by worker |
| RUNNING | `acquiring_trace` | Copying / fetching raw trace to workspace |
| RUNNING | `running_pipeline` | Running Phase 0 (bucketing) + Phase 1 (extraction) |
| COMPLETED | `done` | All faults extracted; result written to task doc |
| FAILED | *(stage at failure)* | Error recorded in `task.error` |

### Aggregation-Certification stages

| Status | Stage | Description |
|---|---|---|
| PENDING | `pending` | Task created, not yet picked up by worker |
| RUNNING | `fetching_metrics` | Worker started; output dir resolved |
| RUNNING | `running_pipeline` | Running Phase 2 (aggregation) + Phase 3 (certification) |
| RUNNING | `storing_metadata` | Writing certification_metadata + aggregated_category_metadata |
| COMPLETED | `done` | Report written; result in task doc |
| FAILED | *(stage at failure)* | Error recorded in `task.error` |

### Polling

```
GET /api/v1/tasks/{task_id}
GET /api/v1/cert-tasks/{cert_task_id}
```

Returns the raw MongoDB task document (minus `_id`).  When `status == "COMPLETED"`,
the `data` field contains the pipeline result.  When `status == "FAILED"`, the
`error` field contains `{ error_code, message, failed_stage, detail }`.

---

## MongoDB Collections

### `pipeline_tasks`

| Field | Type | Description |
|---|---|---|
| `task_id` | string (UUID) | Unique task identifier |
| `agent_id` | string | From request |
| `experiment_id` | string | From request |
| `run_id` | string | From request |
| `status` | enum | PENDING / RUNNING / COMPLETED / FAILED |
| `stage` | string | Current pipeline stage label |
| `created_at` | datetime | UTC insertion time |
| `updated_at` | datetime | UTC last-modified time |
| `started_at` | datetime? | Set when worker picks up the task |
| `completed_at` | datetime? | Set on terminal transition |
| `request` | object | Sanitised request snapshot (no secrets) |
| `result` | object? | Pipeline result on COMPLETED |
| `error` | object? | Error details on FAILED |

Indexes: `idx_task_id_unique`, `idx_agent_exp_run`, `idx_status_created`, `idx_created_at`

---

### `certification_tasks`

Same shape as `pipeline_tasks` but uses `cert_task_id` instead of `task_id` and
adds `agent_name` and `certification_run_id`.

Indexes: `idx_cert_task_id_unique`, `idx_cert_agent_exp`, `idx_cert_status_created`, `idx_cert_created_at`

---

### `certification_metadata`

One document per successfully completed certification run.

| Field | Type | Description |
|---|---|---|
| `certification_id` | string (UUID) | Links to aggregated_category_metadata rows |
| `cert_task_id` | string (UUID) | Back-reference to the task |
| `agent_id` / `agent_name` | string | From request |
| `experiment_id` | string | From request |
| `certification_run_id` | string | Optional caller-supplied identifier |
| `status` | string | Always `"success"` |
| `created_at` | datetime | UTC write time |
| `storage_paths` | object | Absolute paths to scorecard, report, summary files |
| `summary` | object | `total_documents`, `total_fault_categories`, `fault_categories` |
| `processing_time_seconds` | float | Wall-clock time for Phase 2+3 |

Indexes: `idx_certmeta_id_unique`, `idx_certmeta_agent_exp`, `idx_certmeta_agent_created`, `idx_certmeta_run_id` (sparse)

---

### `aggregated_category_metadata`

One document per fault category per certification run.

| Field | Type | Description |
|---|---|---|
| `fault_category` | string | e.g. `"network"`, `"storage"` |
| `certification_id` | string (UUID) | Links to certification_metadata |
| `agent_id` / `experiment_id` | string | From request |
| `total_runs` | int | Number of runs for this category |
| `faults_tested` | list[string] | Fault IDs tested in this category |
| `numeric_metrics` | object | TTD / TTR stats (mean, median, p95, …) |
| `derived_metrics` | object | Detection / mitigation / RAI / security rates |
| `created_at` | datetime | UTC write time |

Indexes: `idx_aggcat_cert_fault_unique`, `idx_aggcat_agent_exp`, `idx_aggcat_created_at`

---

## Concurrency Model

```
HTTP request
     │
     ▼
FastAPI handler (async, event loop)
     │  creates PENDING task in MongoDB
     │  background_tasks.add_task(run_*)  ← scheduled after response is sent
     ▼
202 Accepted returned to client

     ┆ (background)
     ▼
run_task / run_cert_task
     │
     ▼
async with semaphore:            ← gates entry to the heavy pipeline section
    pipeline_svc.execute_pipeline(...)
                                 ← LLM calls are awaited (Motor / httpx / asyncio)
                                 ← File I/O runs in asyncio.to_thread(...)
```

Two independent semaphores prevent resource exhaustion:

| Semaphore | Default size | Controls |
|---|---|---|
| `app.state.semaphore` | `API_MAX_CONCURRENT_TASKS` (4) | Bucketing-extraction runs |
| `app.state.cert_semaphore` | `API_MAX_CONCURRENT_CERT_TASKS` (2) | Aggregation-certification runs |

Tasks can be submitted freely (they queue in PENDING state); only the heavy
compute is gated by the semaphore.

---

## Error Codes Reference

| Code | HTTP status | Source | Meaning |
|---|---|---|---|
| `TASK_ALREADY_ACTIVE` | 409 | Router | A task for this (experiment_id, run_id) or (agent_id, experiment_id) is still running |
| `METRICS_NOT_FOUND` | 400 | Router / Worker | No `*metrics.json` files matching the agent_id were found |
| `INVALID_REQUEST` | 400 | Router / Worker | Bad storage type or illegal path characters |
| `MONGODB_ERROR` | 500 | Router | Task session could not be created |
| `TASK_NOT_FOUND` | 404 | Router | Poll for an unknown task_id |
| `TRACE_NOT_FOUND` | — | Worker | File missing or unreadable; Langfuse returned no traces |
| `TRACE_PARSE_ERROR` | — | Worker | Trace JSON is not a non-empty array of objects |
| `LANGFUSE_FETCH_ERROR` | — | Worker | Langfuse API call failed or bad timestamp |
| `PIPELINE_FAILED` | — | Worker | Unclassified pipeline exception |
| `AGGREGATION_FAILED` | — | Worker | Exception in aggregation or LLM Council step |
| `CERT_GENERATION_FAILED` | — | Worker | Exception in certification report builder |
| `STORAGE_ERROR` | — | Worker | File write or MongoDB insert failed post-pipeline |

Worker error codes appear in the task document's `error.error_code` field, not
as HTTP status codes (since the response was already sent as 202).

---

## Environment Variables

See `.env.example` at the repository root for the full list.  Required
variables (no default):

- `MONGODB_CONNECTION_STRING` — the server will raise `KeyError` on startup if absent.

All other variables have sensible defaults documented in [Configuration](#configuration-configsettingspy).

Variables used by the certifier pipeline (Phase 0–3) are resolved via
`ConfigLoader` from `configs/configs.json`; they use the `ENV_` prefix
convention described in `CLAUDE.md`.

---

## Adding a New Pipeline

To add a third pipeline (e.g. a re-scoring pipeline), follow this checklist:

1. **Models**: add `models/rescore_requests.py` and `models/rescore_responses.py`.
2. **Session service**: add a `RescoreSessionService` class in `session_service.py`
   (follow the `CertSessionService` pattern).
3. **Pipeline service**: add a `RescorePipelineService` class in `pipeline_service.py`.
4. **Worker**: add `workers/rescore_task_runner.py` with a `run_rescore_task` coroutine.
5. **Router**: add `routers/rescore.py` with POST + GET endpoints; register in `main.py`.
6. **MongoDB**: add a new collection name field in `Settings`, create + index in `lifespan`,
   attach to `app.state`.
7. **CLI** (optional): add `cli/run_rescore_pipeline.py`.
