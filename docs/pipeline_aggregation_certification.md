# Pipeline: Aggregation + Certification (Phase 2 + 3)

This document covers every component involved in the **aggregation-certification
pipeline**: the REST endpoint that accepts the job, the metrics pre-flight
validation, the worker that drives it, the services it calls, and the full
lifecycle from per-fault metrics files to a 12-section certification report.

---

## Table of Contents

1. [What This Pipeline Does](#what-this-pipeline-does)
2. [End-to-End Flow Diagram](#end-to-end-flow-diagram)
3. [API Endpoint](#api-endpoint)
   - [Request Model](#request-model)
   - [Submit Flow (Router Logic)](#submit-flow-router-logic)
   - [Metrics Pre-flight Validation](#metrics-pre-flight-validation)
   - [Response](#response)
   - [Poll Endpoint](#poll-endpoint)
4. [Worker: `cert_task_runner`](#worker-cert_task_runner)
   - [Stage Machine](#stage-machine)
   - [Path Resolution & Traversal Guard](#path-resolution--traversal-guard)
   - [Error Classification](#error-classification)
   - [Metadata Fan-out](#metadata-fan-out)
5. [Service: `CertPipelineService`](#service-certpipelineservice)
   - [Phase 2 — Aggregation](#phase-2--aggregation)
   - [Phase 3 — Certification](#phase-3--certification)
   - [LLM Client Lifecycle](#llm-client-lifecycle)
6. [Filesystem Layout](#filesystem-layout)
7. [MongoDB Collections](#mongodb-collections)
   - [`certification_tasks`](#certification_tasks)
   - [`certification_metadata`](#certification_metadata)
   - [`aggregated_category_metadata`](#aggregated_category_metadata)
8. [Concurrency Model](#concurrency-model)
9. [Task Result Payload](#task-result-payload)
10. [Error Reference](#error-reference)
11. [CLI Usage](#cli-usage)
12. [Relationship to the Bucketing Pipeline](#relationship-to-the-bucketing-pipeline)

---

## What This Pipeline Does

Taking the `*_metrics.json` files produced by Phase 1 (one per fault per run),
this pipeline:

1. **Phase 2 — Aggregation** (`aggregator/`): Groups runs by fault category.
   Pure-Python statistics (mean, median, p95, success rates) are computed per
   category.  An **LLM Council** (k independent judges + meta-judge) synthesises
   qualitative summaries.  Output is a `CertificationScorecard` JSON.

2. **Phase 3 — Certification** (`cert_builder/`): Reads the scorecard and builds
   a 12-section `CertificationReport` JSON with concurrently-generated LLM
   narratives.  The report is validated against the `CertificationReport` Pydantic
   schema — if validation fails, the pipeline errors rather than emitting a
   malformed report.

After the pipeline succeeds, the worker writes two MongoDB documents:
- One `certification_metadata` record (per run summary + file paths).
- One `aggregated_category_metadata` record per fault category (scorecard row).

---

## End-to-End Flow Diagram

```
Client
  │
  │  POST /api/v1/aggregation-certification
  │  { agent_id, agent_name, experiment_id, certification_run_id,
  │    runs_per_fault, storage_config: { type: "local", metrics_dir: "..." } }
  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Router: aggregation_certification.py                               │
│                                                                     │
│  [1] Validate storage_config.type == "local"                        │
│        └─ 400 INVALID_REQUEST if not "local"                        │
│                                                                     │
│  [2] asyncio.to_thread(_discover_and_validate(metrics_dir, agent_id)│
│        Scans *metrics.json files → count docs matching agent_id     │
│        └─ 400 METRICS_NOT_FOUND if dir missing or no matches        │
│                                                                     │
│  [3] cert_session_svc.find_active_task(agent_id, experiment_id)     │
│        └─ 409 TASK_ALREADY_ACTIVE if PENDING/RUNNING found          │
│                                                                     │
│  [4] cert_session_svc.create_task(cert_task_id=UUID4, ...)          │
│        Inserts PENDING doc in certification_tasks                   │
│        └─ 500 MONGODB_ERROR on insert failure                       │
│                                                                     │
│  [5] background_tasks.add_task(run_cert_task, ...)                  │
│                                                                     │
│  [6] Return 202 { cert_task_id, poll_url }                          │
└────────────────────────────┬────────────────────────────────────────┘
                             │ (async, after response sent)
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Worker: cert_task_runner.run_cert_task                             │
│                                                                     │
│  set_started(cert_task_id)  →  status=RUNNING, stage=fetching_metrics
│                                                                     │
│  ┌── Resolve output dir ─────────────────────────────────────────┐  │
│  │  resolve_cert_output_dir(cert_workspace_dir, agent_id,        │  │
│  │                          experiment_id)                        │  │
│  │  → workspace/cert/{agent_id}/{experiment_id}/                 │  │
│  │  ValueError → set_failed("INVALID_REQUEST")  ─────────► FAILED│  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  async with cert_semaphore:                                         │
│    update_stage("running_pipeline")                                 │
│                                                                     │
│  ┌── Stage: running_pipeline ────────────────────────────────────┐  │
│  │  CertPipelineService.execute_pipeline(...)                    │  │
│  │                                                               │  │
│  │  ┌─ Phase 2: Aggregation ──────────────────────────────────┐  │  │
│  │  │  DirectoryQueryService(metrics_dir)                     │  │  │
│  │  │  query_runs_by_agent(agent_id)  →  agent_docs list      │  │  │
│  │  │  get_all_fault_categories(agent_id)  →  categories list │  │  │
│  │  │                                                         │  │  │
│  │  │  AggregationOrchestrator.aggregate_all(...)             │  │  │
│  │  │    Pure-Python stats per category (TTD/TTR/rates)       │  │  │
│  │  │    LLM Council: k judges + meta-judge synthesis         │  │  │
│  │  │    → CertificationScorecard dict                        │  │  │
│  │  │                                                         │  │  │
│  │  │  Write: aggregated_scorecard_output_{agent_id}.json     │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  │                                                               │  │
│  │  ┌─ Phase 3: Certification ────────────────────────────────┐  │  │
│  │  │  CertificationPipeline(input=scorecard, output=report)  │  │  │
│  │  │    5 narrative builders run concurrently (asyncio.gather)│  │  │
│  │  │    recommendations builder runs after limitations        │  │  │
│  │  │    validate against CertificationReport Pydantic schema  │  │  │
│  │  │    → CertificationReport dict                           │  │  │
│  │  │                                                         │  │  │
│  │  │  Write: certification_report_{agent_id}.json            │  │  │
│  │  │  Write: pipeline_summary.json                           │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  │                                                               │  │
│  │  Exception → set_failed(classify_cert_error(exc))  ──► FAILED │  │
│  │  Empty result → set_failed("METRICS_NOT_FOUND")  ─────► FAILED│  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  update_stage("storing_metadata")                                   │
│                                                                     │
│  ┌── Stage: storing_metadata ────────────────────────────────────┐  │
│  │  Read pipeline_summary.json                                  │  │
│  │  certification_id = UUID4()                                   │  │
│  │                                                               │  │
│  │  _write_certification_metadata(cert_meta_col, ...)           │  │
│  │    → insert one doc in certification_metadata                 │  │
│  │                                                               │  │
│  │  _write_aggregated_category_metadata(agg_cat_col, ...)       │  │
│  │    Read aggregated_scorecard_output_{agent_id}.json          │  │
│  │    → insert N docs in aggregated_category_metadata           │  │
│  │      (one per fault_category_scorecards entry)               │  │
│  │                                                               │  │
│  │  Exception → set_failed("STORAGE_ERROR")  ──────────► FAILED  │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  set_completed(cert_task_id, task_result)  →  status=COMPLETED      │
└─────────────────────────────────────────────────────────────────────┘

Client polls:  GET /api/v1/cert-tasks/{cert_task_id}
               → { status, stage, result, error, ... }
```

---

## API Endpoint

### Request Model

`POST /api/v1/aggregation-certification`

```json
{
  "agent_id":             "my-k8s-agent",
  "agent_name":           "My Kubernetes Agent v2.1",
  "experiment_id":        "exp-chaos-001",
  "certification_run_id": "v2.1.0-rc1",
  "runs_per_fault":       30,
  "storage_config": {
    "type":        "local",
    "metrics_dir": "/workspace/exp-001/run-42/metrics"
  }
}
```

**`AggregationCertificationRequest`** fields:

| Field | Type | Default | Constraints | Description |
|---|---|---|---|---|
| `agent_id` | string | *(required)* | 1–128 chars, no path separators | Agent being certified |
| `agent_name` | string | *(required)* | 1–256 chars | Human-readable agent name for the report |
| `experiment_id` | string | *(required)* | 1–128 chars, no path separators | Experiment namespace |
| `certification_run_id` | string | `""` | 0–128 chars | Optional caller-supplied run identifier (e.g. git SHA) |
| `runs_per_fault` | int | `30` | 1–1000 | Expected N runs per fault for statistical checks |
| `storage_config` | object | `{ type: "local" }` | must be `"local"` | Metrics directory location |

`storage_config.metrics_dir` is **optional**.  When omitted or set to `""`,
the router derives it as `workspace/{experiment_id}/` — the bucketing pipeline
writes `{run_id}/metrics/` directories under that path, so all runs for the
experiment are picked up automatically via the recursive glob.  Supply it
explicitly only when metrics live outside the default workspace.

`agent_id` and `experiment_id` are validated by `no_path_separators` — any
value containing `/`, `\`, or `..` is rejected with a 422.

---

### Submit Flow (Router Logic)

```
POST /api/v1/aggregation-certification
        │
        ▼
[1] Validate storage_config.type == "local"
        │ not "local" ──────────────────────────────► 400 INVALID_REQUEST
        │
        ▼
[2] asyncio.to_thread(_discover_and_validate(metrics_dir, agent_id))
        │ directory missing ─────────────────────────► 400 METRICS_NOT_FOUND
        │ no *metrics.json files ────────────────────► 400 METRICS_NOT_FOUND
        │ no docs match agent_id ────────────────────► 400 METRICS_NOT_FOUND
        │
        ▼
[3] cert_session_svc.find_active_task(agent_id, experiment_id)
        │ found ─────────────────────────────────────► 409 TASK_ALREADY_ACTIVE
        │ not found
        ▼
[4] cert_session_svc.create_task(cert_task_id=UUID4, ...)
        │ DB error ───────────────────────────────────► 500 MONGODB_ERROR
        │ success → PENDING doc inserted
        ▼
[5] background_tasks.add_task(run_cert_task, ...)
        │ scheduled after response is sent
        ▼
[6] Return 202 CertTaskAcceptedResponse { cert_task_id, poll_url }
```

---

### Metrics Pre-flight Validation

`_discover_and_validate(metrics_dir, agent_id)` runs in a thread
(`asyncio.to_thread`) because it does blocking filesystem I/O:

```
Path(metrics_dir).is_dir()
    False → MetricsValidationError("METRICS_NOT_FOUND", "does not exist or is not a directory")

glob("**/*metrics.json", recursive=True)
    empty → MetricsValidationError("METRICS_NOT_FOUND", "No *metrics.json files found")

for each file:
    json.loads(file.read_text())       # skip if JSONDecodeError or OSError
    docs = data if isinstance(data, list) else [data]
    count += sum(1 for d in docs if _extract_agent_id_from_doc(d) == agent_id)

count == 0 → MetricsValidationError("METRICS_NOT_FOUND", "No metrics documents found for agent_id")

return count  # number of matching documents
```

**`metrics_dir` is optional — the router derives it from `experiment_id` when omitted.**

The bucketing pipeline writes to `workspace/{experiment_id}/{run_id}/metrics/`.
The cert request carries `experiment_id`, so the router can derive the metrics
root as `workspace/{experiment_id}/` and the recursive glob inside
`DirectoryQueryService` handles the rest:

```
# router: aggregation_certification.py
if not body.storage_config.metrics_dir:
    body.storage_config.metrics_dir = str(
        settings.workspace_dir / body.experiment_id
    )
```

Supplying `metrics_dir` explicitly is still supported for cases where metrics
live outside the default workspace (e.g. a merged export directory).

Because `metrics_dir` can point at any directory (not just the workspace root),
the `agent_id` content-scan is still needed to filter documents — there is no
`agent_id` in the directory path, so the pipeline opens every `*metrics.json`
it finds and reads the value from inside the JSON.

`_extract_agent_id_from_doc` handles two JSON layouts that exist in practice:

```
Layout A — top-level (direct storage):
    { "agent_id": "my-k8s-agent", "fault_id": "...", ... }
                   ▲
                   doc.get("agent_id")

Layout B — nested under quantitative (Phase 1 TraceMetricsExtractor output):
    { "fault_id": "...", "quantitative": { "agent_id": "my-k8s-agent", ... }, ... }
                                                        ▲
                                          doc.get("quantitative", {}).get("agent_id")
```

Phase 1 produces Layout B — the `agent_id` is written by `TraceMetricsExtractor`
into the `quantitative` block.

> **Design note**: if `metrics_dir` were always derived from `experiment_id`
> (i.e. `workspace/{experiment_id}/*/metrics/`), the `agent_id` content-scan
> would still be needed to distinguish runs from different agents within the
> same experiment.  But `experiment_id` alone would be sufficient to locate all
> run directories, making the `metrics_dir` parameter derivable and optional.
> That simplification is not yet implemented.

This pre-flight is a courtesy check — it catches obvious mistakes (wrong
directory, wrong `agent_id`) before any task is created, rather than letting
the error surface 10+ minutes into a certification run.

---

### Response

**202 Accepted:**

```json
{
  "cert_task_id": "7c4a8d64-3b22-4c98-ae41-f0c2e8a5b6d9",
  "poll_url":     "/api/v1/cert-tasks/7c4a8d64-3b22-4c98-ae41-f0c2e8a5b6d9"
}
```

**400 Bad Request** (metrics not found):

```json
{
  "status":     "error",
  "error_code": "METRICS_NOT_FOUND",
  "message":    "No metrics documents found for agent_id='my-k8s-agent' in '/workspace/exp-001/run-42/metrics'",
  "details": {
    "failed_stage": "metrics_validation",
    "error":        "No metrics documents found for agent_id='my-k8s-agent' ..."
  }
}
```

**409 Conflict** (duplicate active task):

```json
{
  "status":     "error",
  "error_code": "TASK_ALREADY_ACTIVE",
  "message":    "A certification task is already RUNNING for my-k8s-agent/exp-001",
  "details": {
    "cert_task_id": "7c4a8d64-...",
    "status":       "RUNNING",
    "stage":        "running_pipeline"
  }
}
```

---

### Poll Endpoint

`GET /api/v1/cert-tasks/{cert_task_id}`

Returns the raw MongoDB task document (minus `_id`).  Poll until `status` is
`"COMPLETED"` or `"FAILED"`.

**On completion:**
```json
{
  "cert_task_id": "7c4a8d64-...",
  "status":       "COMPLETED",
  "stage":        "done",
  "result": {
    "total_documents":       90,
    "total_fault_categories": 3,
    "fault_categories":       ["network", "storage", "compute"],
    "certification_id":       "a1b2c3d4-...",
    "storage_paths": {
      "aggregated_scorecard":   "/workspace/cert/my-k8s-agent/exp-001/aggregated_scorecard_output_my-k8s-agent.json",
      "certification_report":   "/workspace/cert/my-k8s-agent/exp-001/certification_report_my-k8s-agent.json",
      "summary":                "/workspace/cert/my-k8s-agent/exp-001/pipeline_summary.json"
    },
    "processing_time_seconds": 142.7
  }
}
```

**On failure:**
```json
{
  "cert_task_id": "7c4a8d64-...",
  "status":       "FAILED",
  "stage":        "running_pipeline",
  "error": {
    "error_code":   "AGGREGATION_FAILED",
    "message":      "Council aggregation error: LLM timeout",
    "failed_stage": "running_pipeline",
    "detail":       "Traceback ..."
  }
}
```

---

## Worker: `cert_task_runner`

### Stage Machine

```
set_started(cert_task_id)
    │
    │  PENDING → RUNNING
    │  stage = "fetching_metrics"
    ▼
resolve_cert_output_dir(cert_workspace_dir, agent_id, experiment_id)
    │  creates workspace/cert/{agent_id}/{experiment_id}/
    │  ValueError → set_failed("INVALID_REQUEST")  ──────────► FAILED
    │
    ▼
async with cert_semaphore:
    │
    ▼
update_stage(cert_task_id, "running_pipeline")
    │  stage = "running_pipeline"
    │
    ▼
cert_pipeline_svc.execute_pipeline(
    metrics_dir, output_dir, agent_id, agent_name,
    certification_run_id, runs_per_fault, config
)
    │  Exception → set_failed(classify_cert_error(exc))  ─────► FAILED
    │  empty result → set_failed("METRICS_NOT_FOUND")  ────────► FAILED
    │
    ▼
update_stage(cert_task_id, "storing_metadata")
    │  stage = "storing_metadata"
    │
    ▼
asyncio.to_thread(_read_json, cert_output_dir/pipeline_summary.json)
    │
    ▼
certification_id = UUID4()
    │
    ▼
_write_certification_metadata(cert_meta_col, certification_id, ...)
    │  → insert one doc in certification_metadata
    │
    ▼
_write_aggregated_category_metadata(agg_cat_col, certification_id, ...)
    │  reads aggregated_scorecard_output_{agent_id}.json in thread
    │  → insert_many(docs) in aggregated_category_metadata
    │  Exception → set_failed("STORAGE_ERROR")  ───────────────► FAILED
    │
    ▼
set_completed(cert_task_id, task_result)
    │  RUNNING → COMPLETED
    │  stage = "done"
```

### Path Resolution & Traversal Guard

`resolve_cert_output_dir` in [cert_task_runner.py](../main/workers/cert_task_runner.py)
mirrors the bucketing worker's guard:

```python
for segment in (agent_id, experiment_id):
    if "/" in segment or "\\" in segment or ".." in segment:
        raise ValueError(f"Path segment contains illegal characters: {segment!r}")
path = cert_workspace_dir / agent_id / experiment_id
path.mkdir(parents=True, exist_ok=True)
```

The Pydantic validator on the request model (`no_path_separators`) catches this
earlier, but the worker re-checks as defence-in-depth.

### Error Classification

`classify_cert_error(exc)` maps an untyped pipeline exception to a structured
error code using keyword matching on the lowercased message:

```
"aggregat" or "council" or "scorecard" in msg  →  AGGREGATION_FAILED
"certif"   or "cert_builder" or "report" in msg →  CERT_GENERATION_FAILED
"storage"  or isinstance(exc, OSError)          →  STORAGE_ERROR
(default)                                        →  PIPELINE_FAILED
```

This mapping is intentionally lightweight — the pipeline modules do not expose
typed exception hierarchies, so string matching is the practical approach.

### Metadata Fan-out

After the pipeline completes, the worker writes two kinds of MongoDB documents
using a shared `certification_id` UUID as the linking key:

```
certification_id = UUID4()
    │
    ├──► certification_metadata collection
    │      One document per certification run:
    │      { certification_id, cert_task_id, agent_id, agent_name,
    │        experiment_id, certification_run_id, status, created_at,
    │        storage_paths, summary, processing_time_seconds }
    │
    └──► aggregated_category_metadata collection
           One document per fault_category_scorecards entry:
           { fault_category, certification_id, agent_id, experiment_id,
             total_runs, faults_tested, numeric_metrics, derived_metrics,
             created_at }
```

The fan-out is driven by reading the aggregated scorecard file from disk:

```python
aggregated_scorecard = await asyncio.to_thread(_read_json, scorecard_path)
docs = []
for sc in aggregated_scorecard.get("fault_category_scorecards", []):
    docs.append({
        "fault_category":  sc["fault_category"],
        "certification_id": certification_id,
        "agent_id":        agent_id,
        ...
    })
await agg_cat_col.insert_many(docs)
```

---

## Service: `CertPipelineService`

`CertPipelineService.execute_pipeline(metrics_dir, output_dir, agent_id,
agent_name, certification_run_id, runs_per_fault, debug, config)` orchestrates
Phase 2 then Phase 3.

### Phase 2 — Aggregation

```
DirectoryQueryService(metrics_dir)
    │  file-based query backend
    │  glob("**/*metrics.json", recursive=True) — no agent_id in path
    │  agent_id is matched from JSON content, not from the directory path
    │  (bucketing writes to workspace/{exp_id}/{run_id}/metrics/ — no agent_id
    │   in the path; agent_id lives inside doc["quantitative"]["agent_id"])
    │
    ▼
query_service.query_runs_by_agent(agent_id)
    │  _load_all_docs() → glob all *metrics.json files
    │  _filter_by_agent(docs, agent_id)
    │    → keeps docs where _extract_agent_id(doc) == agent_id
    │    → checks doc.get("agent_id") or doc["quantitative"]["agent_id"]
    │  returns list of matching metric documents
    │  empty → log error + return {}
    │
    ▼
query_service.get_all_fault_categories(agent_id=agent_id)
    │  same load + filter, then extracts unique fault_category values
    │  returns list of unique fault category strings
    │  empty → log error + return {}
    │
    ▼
AggregationOrchestrator(
    llm_client=llm_client,
    query_service=query_service,
    db_client=None,           ← file-only; no MongoDB storage for scorecard
)
    │
    ▼
orchestrator.aggregate_all(
    agent_id=agent_id,
    agent_name=agent_name,
    certification_run_id=certification_run_id,
    runs_per_fault=runs_per_fault,
    store_results=False,
)
    │  For each fault category:
    │    1. Load all runs for the category
    │    2. Pure-Python numeric stats:
    │         TTD: mean, median, p95, min, max, stddev
    │         TTR: mean, median, p95, min, max, stddev
    │         detection/mitigation/rai/security success rates
    │    3. LLM Council for qualitative synthesis:
    │         k independent judge calls (concurrent)
    │         meta-judge call → consensus narrative
    │  → CertificationScorecard dict
    │
    ▼
_save_json(aggregated_scorecard, output_path/aggregated_scorecard_output_{agent_id}.json)

_print_aggregation_summary(aggregated_scorecard, agent_id, agent_name)
    │  logs per-category stats at INFO level
```

**Key design principle**: All numeric aggregation is pure Python — no LLM
arithmetic.  This makes statistics fully reproducible and deterministic.  LLM
calls are used only for qualitative narrative synthesis.

### Phase 3 — Certification

```
CertificationPipeline(
    input_path=scorecard_path,    ← aggregated_scorecard_output_{agent_id}.json
    output_path=report_path,      ← certification_report_{agent_id}.json
    debug=debug,
)
    │
    ▼
cert_pipeline.run()
    │  Reads scorecard JSON
    │
    │  Runs 5 narrative builders concurrently via asyncio.gather:
    │    - executive_summary_builder
    │    - methodology_builder
    │    - fault_analysis_builder
    │    - performance_metrics_builder
    │    - compliance_builder
    │
    │  Then runs sequentially (explicit dependency):
    │    - limitations_builder
    │    - recommendations_builder  ← depends on limitations output
    │
    │  Validates final dict against CertificationReport Pydantic schema
    │    ValidationError → pipeline errors (does not emit malformed report)
    │
    ▼
→ CertificationReport dict (12 sections)
```

The `CertificationReport` has 12 sections covering executive summary,
methodology, per-category fault analysis, aggregate performance metrics,
RAI/security compliance, limitations, and recommendations.

### LLM Client Lifecycle

The `AzureLLMClient` is created once at the start of `execute_pipeline` and
closed in a `finally` block:

```python
llm_client = AzureLLMClient(config=config)
try:
    # ... Phase 2 + Phase 3 ...
finally:
    if llm_client:
        await llm_client.close()   # releases connection pool even on exception
```

This guarantees that the connection pool is always returned, even if Phase 2 or
Phase 3 raises an exception.

---

## Filesystem Layout

After a successful run, the cert workspace looks like:

```
workspace/cert/
└── {agent_id}/
    └── {experiment_id}/
        ├── aggregated_scorecard_output_{agent_id}.json   ← Phase 2 output
        ├── certification_report_{agent_id}.json          ← Phase 3 output
        └── pipeline_summary.json                         ← lightweight summary
```

`pipeline_summary.json` structure:
```json
{
  "agent_id":                 "my-k8s-agent",
  "agent_name":               "My Kubernetes Agent v2.1",
  "certification_run_id":     "v2.1.0-rc1",
  "metrics_dir":              "/workspace/exp-001/run-42/metrics",
  "total_documents":          90,
  "total_fault_categories":   3,
  "fault_categories":         ["network", "storage", "compute"],
  "aggregated_scorecard_path": "/workspace/cert/my-k8s-agent/exp-001/aggregated_scorecard_output_my-k8s-agent.json",
  "certification_report_path": "/workspace/cert/my-k8s-agent/exp-001/certification_report_my-k8s-agent.json"
}
```

---

## MongoDB Collections

### `certification_tasks`

One document per submitted certification job.

**Document shape:**

```json
{
  "cert_task_id":         "7c4a8d64-3b22-4c98-ae41-f0c2e8a5b6d9",
  "agent_id":             "my-k8s-agent",
  "agent_name":           "My Kubernetes Agent v2.1",
  "experiment_id":        "exp-chaos-001",
  "certification_run_id": "v2.1.0-rc1",
  "status":               "COMPLETED",
  "stage":                "done",
  "created_at":           "2024-01-15T11:00:00.000Z",
  "updated_at":           "2024-01-15T11:02:22.000Z",
  "started_at":           "2024-01-15T11:00:01.000Z",
  "completed_at":         "2024-01-15T11:02:22.000Z",
  "request":              { ... full request snapshot ... },
  "result":               { ... task result payload ... },
  "error":                null
}
```

**Indexes:**

| Name | Keys | Unique | Purpose |
|---|---|---|---|
| `idx_cert_task_id_unique` | `cert_task_id` | Yes | Primary task lookup by UUID |
| `idx_cert_agent_exp` | `agent_id`, `experiment_id` | No | Duplicate submission guard |
| `idx_cert_status_created` | `status`, `created_at` (desc) | No | Status-filtered recent-first queries |
| `idx_cert_created_at` | `created_at` | No | TTL / date-range scans |

**State machine transitions:**

```
create_task()
    insert { status: "PENDING", stage: "pending" }

set_started()
    filter: { status: "PENDING" }
    update: { status: "RUNNING", stage: "fetching_metrics" }
    $currentDate: { started_at, updated_at }

update_stage(stage)
    filter: { cert_task_id }
    update: { stage: stage }
    $currentDate: { updated_at }

set_completed(result)
    filter: { status: "RUNNING" }          ← only from RUNNING
    update: { status: "COMPLETED", stage: "done", result: result }
    $currentDate: { completed_at, updated_at }
    raises ValueError if matched_count == 0

set_failed(error_code, message, failed_stage, detail)
    filter: { status: { $in: ["PENDING","RUNNING"] } }
    update: { status: "FAILED", error: { ... } }
    $currentDate: { completed_at, updated_at }
```

---

### `certification_metadata`

One document per successfully completed certification run.

**Document shape:**

```json
{
  "certification_id":     "a1b2c3d4-5e6f-7890-abcd-ef1234567890",
  "cert_task_id":         "7c4a8d64-...",
  "agent_id":             "my-k8s-agent",
  "agent_name":           "My Kubernetes Agent v2.1",
  "experiment_id":        "exp-chaos-001",
  "certification_run_id": "v2.1.0-rc1",
  "status":               "success",
  "created_at":           "2024-01-15T11:02:22.000Z",
  "storage_paths": {
    "aggregated_scorecard": "/workspace/cert/my-k8s-agent/exp-001/aggregated_scorecard_output_my-k8s-agent.json",
    "certification_report": "/workspace/cert/my-k8s-agent/exp-001/certification_report_my-k8s-agent.json",
    "summary":              "/workspace/cert/my-k8s-agent/exp-001/pipeline_summary.json"
  },
  "summary": {
    "total_documents":       90,
    "total_fault_categories": 3,
    "fault_categories":       ["network", "storage", "compute"]
  },
  "processing_time_seconds": 142.7,
  "error_message":            null
}
```

**Indexes:**

| Name | Keys | Unique | Notes |
|---|---|---|---|
| `idx_certmeta_id_unique` | `certification_id` | Yes | Primary lookup |
| `idx_certmeta_agent_exp` | `agent_id`, `experiment_id` | No | Agent+experiment history queries |
| `idx_certmeta_agent_created` | `agent_id`, `created_at` (desc) | No | Latest cert for an agent |
| `idx_certmeta_run_id` | `certification_run_id` | No (sparse) | Optional run ID lookup; sparse=True because field is optional |

---

### `aggregated_category_metadata`

One document per fault category per certification run.  The `certification_id`
field links all category rows back to the `certification_metadata` record.

**Document shape:**

```json
{
  "fault_category":   "network",
  "certification_id": "a1b2c3d4-...",
  "agent_id":         "my-k8s-agent",
  "experiment_id":    "exp-chaos-001",
  "total_runs":       30,
  "faults_tested":    ["network-latency-001", "network-partition-002"],
  "numeric_metrics": {
    "time_to_detect": {
      "mean":   45.2,
      "median": 42.0,
      "p95":    88.5,
      "min":    12.0,
      "max":   110.3,
      "stddev": 18.7
    },
    "time_to_mitigate": {
      "mean":   210.4,
      "median": 195.0,
      "p95":   380.0,
      "min":    80.0,
      "max":   450.0,
      "stddev": 65.2
    }
  },
  "derived_metrics": {
    "fault_detection_success_rate":   0.93,
    "fault_mitigation_success_rate":  0.87,
    "rai_compliance_rate":            0.98,
    "security_compliance_rate":       1.0
  },
  "created_at": "2024-01-15T11:02:23.000Z"
}
```

**Indexes:**

| Name | Keys | Unique | Notes |
|---|---|---|---|
| `idx_aggcat_cert_fault_unique` | `certification_id`, `fault_category` | Yes | Prevents duplicate category rows per certification |
| `idx_aggcat_agent_exp` | `agent_id`, `experiment_id` | No | Agent+experiment history queries |
| `idx_aggcat_created_at` | `created_at` (desc) | No | Recency queries |

---

## Concurrency Model

```
HTTP requests (many)
      │
      ▼
FastAPI event loop  (one process)
      │
      ▼
background_tasks.add_task(run_cert_task, ...)
      │   tasks queue in PENDING state in MongoDB
      ▼
run_cert_task coroutine
      │   dir resolution runs freely (no I/O contention)
      │
      ▼
async with cert_semaphore:          ← app.state.cert_semaphore
      │   capacity = API_MAX_CONCURRENT_CERT_TASKS (default 2)
      │   blocks here if 2 cert pipeline runs are already active
      ▼
CertPipelineService.execute_pipeline()
      │   Phase 2: Pure-Python stats (CPU-bound but short)
      │            LLM Council calls (async HTTP)
      │   Phase 3: 5 narrative builders via asyncio.gather (concurrent LLM)
      │            2 sequential builders (limitations → recommendations)
      │   File I/O: asyncio.to_thread for scorecard/report writes
      ▼
release cert_semaphore
      │
      ▼
storing_metadata: file reads in asyncio.to_thread, Motor async inserts
```

The cert pipeline is capped at **2 concurrent runs** (versus 4 for bucketing)
because Phase 2+3 involve many concurrent LLM calls (Council + concurrent
narrative builders) and are significantly more memory-intensive.

The two semaphores are completely independent — bucketing and certification
pipelines can run simultaneously up to their respective limits.

---

## Task Result Payload

The `result` dict stored in the completed `certification_tasks` document:

```json
{
  "total_documents":       90,
  "total_fault_categories": 3,
  "fault_categories":       ["network", "storage", "compute"],
  "certification_id":       "a1b2c3d4-...",
  "storage_paths": {
    "aggregated_scorecard": "/workspace/cert/my-k8s-agent/exp-001/aggregated_scorecard_output_my-k8s-agent.json",
    "certification_report": "/workspace/cert/my-k8s-agent/exp-001/certification_report_my-k8s-agent.json",
    "summary":              "/workspace/cert/my-k8s-agent/exp-001/pipeline_summary.json"
  },
  "processing_time_seconds": 142.7
}
```

`total_documents` is the count of per-run metric documents loaded from
`metrics_dir`.  `processing_time_seconds` measures the semaphore-gated pipeline
section only (excludes metadata storage time).

---

## Error Reference

| Error Code | Origin | HTTP status | Trigger |
|---|---|---|---|
| `INVALID_REQUEST` | Router | 400 | `storage_config.type != "local"` or path traversal in output dir resolution |
| `METRICS_NOT_FOUND` | Router / Worker | 400 / task | Directory missing, no `*metrics.json` files, or no docs match `agent_id` |
| `TASK_ALREADY_ACTIVE` | Router | 409 | PENDING/RUNNING task exists for `(agent_id, experiment_id)` |
| `MONGODB_ERROR` | Router | 500 | `create_task` DB insert fails |
| `TASK_NOT_FOUND` | Router | 404 | `GET /cert-tasks/{id}` for unknown `cert_task_id` |
| `AGGREGATION_FAILED` | Worker | — | Exception mentioning "aggregat", "council", or "scorecard" |
| `CERT_GENERATION_FAILED` | Worker | — | Exception mentioning "certif", "cert_builder", or "report" |
| `STORAGE_ERROR` | Worker | — | OSError during pipeline or metadata write |
| `PIPELINE_FAILED` | Worker | — | Unclassified exception in `execute_pipeline` |

Worker error codes are stored in `task.error.error_code` — they are **not** HTTP
status codes (the 202 was already returned).

---

## CLI Usage

The CLI bypasses the HTTP layer and calls `CertPipelineService` directly.

```bash
# Via module
python -m main.cli.run_aggregation_and_certification_pipeline \
    --metrics-dir       /workspace/exp-001/run-42/metrics \
    --output-dir        /tmp/cert_out \
    --agent-id          my-k8s-agent \
    --agent-name        "My Kubernetes Agent v2.1" \
    --certification-run-id v2.1.0-rc1 \
    --runs-per-fault    30 \
    --debug

# Arguments
--metrics-dir           Directory with *metrics.json files from Phase 1 (required)
--output-dir            Root output directory for scorecard + report (required)
--agent-id              Agent identifier matching the metrics documents (required)
--agent-name            Human-readable agent name for the report (required)
--certification-run-id  Optional identifier for this run (default: "")
--runs-per-fault        Expected N runs per fault (default: 30)
--debug                 Retain intermediate outputs for inspection
```

Outputs are written to:
```
/tmp/cert_out/
├── aggregated_scorecard_output_{agent_id}.json   ← Phase 2 output
├── certification_report_{agent_id}.json           ← Phase 3 output
└── pipeline_summary.json
```

Note: the CLI does **not** write to MongoDB (`certification_metadata` and
`aggregated_category_metadata`).  That step is performed only by the API worker.

---

## Relationship to the Bucketing Pipeline

```
Bucketing + Extraction pipeline              Aggregation + Certification pipeline
────────────────────────────────             ──────────────────────────────────────
Input:  raw Langfuse trace JSON       →      Input:  *_metrics.json files
                                             (from Phase 1 output)
Output: *_metrics.json per fault      →      Output: aggregated_scorecard.json
        workspace/{exp}/{run}/metrics/               certification_report.json
                                                     certification_metadata (MongoDB)
                                                     aggregated_category_metadata (MongoDB)
```

The two pipelines are **decoupled by the filesystem**: the bucketing pipeline
writes `*_metrics.json` files to `workspace/{experiment_id}/{run_id}/metrics/`,
and the certification pipeline reads from whatever `metrics_dir` is supplied in
the request.  This means:

- Multiple bucketing runs (different `run_id`s) can contribute metrics to one
  certification run by pointing all their metrics to the same `metrics_dir`.
- The certification pipeline can be re-run with different `certification_run_id`
  values over the same metrics without re-running the expensive bucketing phase.
- The pipelines can be submitted concurrently (they use independent semaphores
  and independent MongoDB collections).
