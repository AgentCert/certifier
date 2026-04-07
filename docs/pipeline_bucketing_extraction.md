# Pipeline: Bucketing + Extraction (Phase 0 + 1)

This document covers every component involved in the **bucketing-extraction pipeline**:
the REST endpoint that accepts the job, the worker that drives it, the services it
calls, and the full lifecycle from raw Langfuse trace to per-fault metrics files.

---

## Table of Contents

1. [What This Pipeline Does](#what-this-pipeline-does)
2. [End-to-End Flow Diagram](#end-to-end-flow-diagram)
3. [API Endpoint](#api-endpoint)
   - [Request Model](#request-model)
   - [Trace Source Types](#trace-source-types)
   - [Submit Flow (Router Logic)](#submit-flow-router-logic)
   - [Response](#response)
   - [Poll Endpoint](#poll-endpoint)
4. [Worker: `bucket_task_runner`](#worker-bucket_task_runner)
   - [Stage Machine](#stage-machine)
   - [Path Resolution & Traversal Guard](#path-resolution--traversal-guard)
5. [Service: `TraceService`](#service-traceservice)
   - [File Source Path](#file-source-path)
   - [Langfuse Source Path](#langfuse-source-path)
   - [Observation Normalisation](#observation-normalisation)
   - [Validation](#validation)
6. [Service: `BucketPipelineService`](#service-bucketpipelineservice)
   - [Phase 0 — Fault Bucketing](#phase-0--fault-bucketing)
   - [Phase 1 — Metrics Extraction](#phase-1--metrics-extraction)
   - [Fault Config Normalisation](#fault-config-normalisation)
   - [Summary File](#summary-file)
7. [Filesystem Layout](#filesystem-layout)
8. [MongoDB: `pipeline_tasks`](#mongodb-pipeline_tasks)
   - [Document Shape](#document-shape)
   - [Indexes](#indexes)
   - [State Machine Transitions](#state-machine-transitions)
9. [Concurrency Model](#concurrency-model)
10. [Task Result Payload](#task-result-payload)
11. [Error Reference](#error-reference)
12. [CLI Usage](#cli-usage)

---

## What This Pipeline Does

Given a raw Langfuse trace from an AI agent operating under fault injection, this
pipeline:

1. **Phase 0 — Fault Bucketing** (`fault_analyzer/`): An LLM classifies the
   interleaved trace events into per-fault lifecycle buckets — each bucket contains
   only the observations relevant to a single injected fault.

2. **Phase 1 — Metrics Extraction** (`metrics_extractor/`): For each bucket,
   quantitative metrics (TTD, TTR, token counts) and qualitative judgements are
   extracted, producing one `*_metrics.json` file per fault.

The output of this pipeline feeds directly into the
[Aggregation + Certification pipeline](pipeline_aggregation_certification.md).

---

## End-to-End Flow Diagram

```
Client
  │
  │  POST /api/v1/bucketing-extraction
  │  { agent_id, experiment_id, run_id, trace_source, ... }
  ▼
┌─────────────────────────────────────────────────────────────┐
│  Router: bucketing_extraction.py                            │
│                                                             │
│  1. find_active_task(experiment_id, run_id)                 │
│     └─ 409 TASK_ALREADY_ACTIVE if duplicate found          │
│                                                             │
│  2. Strip Langfuse secret_key from snapshot                 │
│                                                             │
│  3. session_svc.create_task(task_id, ...)                   │
│     └─ Inserts PENDING doc in pipeline_tasks                │
│                                                             │
│  4. background_tasks.add_task(run_task, ...)                │
│                                                             │
│  5. Return 202 { task_id, poll_url }                        │
└───────────────────────┬─────────────────────────────────────┘
                        │ (async, after response sent)
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Worker: bucket_task_runner.run_task                        │
│                                                             │
│  set_started(task_id)  →  status=RUNNING, stage=acquiring_trace
│                                                             │
│  ┌── Stage: acquiring_trace ──────────────────────────────┐ │
│  │  _resolve_run_dir(workspace/, experiment_id, run_id)   │ │
│  │  trace_svc.acquire_trace(trace_source, run_dir/traces/)│ │
│  │                                                        │ │
│  │  [file]                    [langfuse]                  │ │
│  │   shutil.copy2()            _fetch_langfuse_obs()      │ │
│  │   → raw_trace.json          _format_observations()     │ │
│  │                             → raw_trace.json           │ │
│  │                                                        │ │
│  │  _load_and_validate()  →  (path, observation_count)    │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  update_stage(task_id, "running_pipeline")                  │
│                                                             │
│  ┌── Stage: running_pipeline (inside semaphore) ──────────┐ │
│  │  BucketPipelineService.execute_pipeline()              │ │
│  │                                                        │ │
│  │  ┌─ Phase 0: Fault Bucketing ──────────────────────┐  │ │
│  │  │  FaultBucketingPipeline(trace_file, batch_size) │  │ │
│  │  │  LLM classifies events → fault buckets dict     │  │ │
│  │  │  Output: fault_buckets/{fault_id}_bucket.json   │  │ │
│  │  └────────────────────────────────────────────────┘  │ │
│  │                                                        │ │
│  │  ┌─ Phase 1: Metrics Extraction (per fault) ───────┐  │ │
│  │  │  For each fault_id in buckets:                  │  │ │
│  │  │    Write events → {safe_name}_trace.json (tmp)  │  │ │
│  │  │    _build_fault_config_from_bucket()            │  │ │
│  │  │    Write → {safe_name}_fault_config.json (tmp)  │  │ │
│  │  │    TraceMetricsExtractor.extract_metrics_async()│  │ │
│  │  │    Write → {safe_name}_metrics.json             │  │ │
│  │  └────────────────────────────────────────────────┘  │ │
│  │                                                        │ │
│  │  Write pipeline_summary.json                          │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  _build_result(results, summary, obs_count, run_dir, elapsed)
│  set_completed(task_id, result)  →  status=COMPLETED        │
└─────────────────────────────────────────────────────────────┘

Client polls:  GET /api/v1/tasks/{task_id}
               → { status, stage, result, error, ... }
```

---

## API Endpoint

### Request Model

`POST /api/v1/bucketing-extraction`

```json
{
  "agent_id":      "my-k8s-agent",
  "experiment_id": "exp-chaos-001",
  "run_id":        "run-42",
  "trace_source": {
    "type":      "file",
    "file_path": "/data/traces/run42.json"
  },
  "llm_batch_size": 5,
  "storage_config": { "type": "local" }
}
```

**`BucketingExtractionRequest`** fields:

| Field | Type | Default | Constraints | Description |
|---|---|---|---|---|
| `agent_id` | string | *(required)* | 1–128 chars | Identifies the agent being evaluated |
| `experiment_id` | string | *(required)* | 1–128 chars, no path separators | Experiment namespace |
| `run_id` | string | *(required)* | 1–128 chars, no path separators | One execution within the experiment |
| `trace_source` | union | *(required)* | discriminated on `type` | Where to get the raw trace |
| `llm_batch_size` | int | `5` | 1–50 | Events per LLM call in Phase 0 |
| `storage_config` | object | `{ type: "local" }` | see below | Where Phase 1 writes metrics |

`storage_config.type` options:

| Value | Behaviour |
|---|---|
| `"local"` | Write metrics only to filesystem |
| `"mongodb"` | Write metrics only to MongoDB |
| `"hybrid"` | Write to both filesystem and MongoDB |
| `"blob_storage"` | Reserved (filesystem fallback) |

`experiment_id` and `run_id` are validated by `no_path_separators` — any value
containing `/`, `\`, or `..` is rejected with a 422.  This prevents users from
crafting IDs that escape the workspace directory.

---

### Trace Source Types

**File source** (`type: "file"`):

```json
{
  "type":      "file",
  "file_path": "/data/traces/run42.json"
}
```

The file must already exist on the server.  It is copied with `shutil.copy2`
(preserving metadata) to `workspace/{experiment_id}/{run_id}/traces/raw_trace.json`.

**Langfuse source** (`type: "langfuse"`):

```json
{
  "type":                "langfuse",
  "base_url":            "https://cloud.langfuse.com",
  "public_key":          "pk-lf-...",
  "secret_key":          "sk-lf-...",
  "from_timestamp":      "2024-01-15T00:00:00Z",
  "page_size":           100,
  "max_pages":           20,
  "include_observations": true
}
```

| Field | Default | Range | Description |
|---|---|---|---|
| `from_timestamp` | *(required)* | ISO-8601 | Fetch traces created after this UTC time |
| `page_size` | `100` | 1–500 | Traces per Langfuse API page |
| `max_pages` | `20` | 1–100 | Maximum pages to paginate through |
| `include_observations` | `true` | — | Whether to fetch span-level observations |

The `secret_key` is **stripped** from the MongoDB request snapshot before
persistence — credentials are never stored at rest.

---

### Submit Flow (Router Logic)

```
POST /api/v1/bucketing-extraction
        │
        ▼
[1] find_active_task(experiment_id, run_id)
        │ found? ─────────────────► 409 TASK_ALREADY_ACTIVE
        │ not found
        ▼
[2] Strip secret_key from snapshot (if Langfuse source)
        │
        ▼
[3] session_svc.create_task(task_id=UUID4, ...)
        │ inserts PENDING doc in pipeline_tasks
        ▼
[4] background_tasks.add_task(run_task, task_id, ...)
        │ scheduled to run after response is returned
        ▼
[5] Return 202 TaskAcceptedResponse { task_id, poll_url }
```

The task document is persisted **before** the background task is dispatched.
This ensures the poll endpoint can return the task immediately if the client
polls within milliseconds of the 202 response.

---

### Response

**202 Accepted:**

```json
{
  "task_id":  "550e8400-e29b-41d4-a716-446655440000",
  "poll_url": "/api/v1/tasks/550e8400-e29b-41d4-a716-446655440000"
}
```

**409 Conflict** (duplicate active task):

```json
{
  "status":     "error",
  "error_code": "TASK_ALREADY_ACTIVE",
  "message":    "A pipeline task is already RUNNING for exp-001/run-42",
  "details": {
    "task_id": "550e...",
    "status":  "RUNNING",
    "stage":   "running_pipeline"
  }
}
```

---

### Poll Endpoint

`GET /api/v1/tasks/{task_id}`

Returns the raw MongoDB task document (minus `_id`).  Poll until `status` is
`"COMPLETED"` or `"FAILED"`.

**While running:**
```json
{ "task_id": "...", "status": "RUNNING", "stage": "running_pipeline", ... }
```

**On completion:**
```json
{
  "task_id": "...",
  "status": "COMPLETED",
  "stage": "done",
  "result": {
    "total_observations": 1240,
    "total_faults_detected": 3,
    "faults": [
      {
        "fault_id":     "network-latency-001",
        "fault_name":   "Network Latency Injection",
        "severity":     "network",
        "status":       "closed",
        "detected_at":  "2024-01-15T10:22:14.000Z",
        "mitigated_at": "2024-01-15T10:25:44.000Z"
      }
    ],
    "storage_paths": {
      "traces_dir":       "/workspace/exp-001/run-42/traces/",
      "fault_buckets_dir": "/workspace/exp-001/run-42/fault_buckets/",
      "metrics_dir":      "/workspace/exp-001/run-42/metrics/",
      "summary":          "/workspace/exp-001/run-42/pipeline_summary.json",
      "log":              "/workspace/exp-001/run-42/pipeline.log"
    },
    "token_usage": {
      "bucketing_input_tokens":   4500,
      "bucketing_output_tokens":  1200,
      "extraction_input_tokens":  6800,
      "extraction_output_tokens": 2100,
      "total_tokens":             14600
    },
    "processing_time_seconds": 47.3
  }
}
```

**On failure:**
```json
{
  "task_id": "...",
  "status": "FAILED",
  "stage": "acquiring_trace",
  "error": {
    "error_code":   "TRACE_NOT_FOUND",
    "message":      "Trace file not found: /data/traces/run42.json",
    "failed_stage": "acquiring_trace",
    "detail":       "Traceback (most recent call last):\n  ..."
  }
}
```

---

## Worker: `bucket_task_runner`

### Stage Machine

```
set_started(task_id)
    │
    │  PENDING → RUNNING
    │  stage = "acquiring_trace"
    ▼
_resolve_run_dir(workspace_dir, experiment_id, run_id)
    │  creates workspace/{experiment_id}/{run_id}/
    │
    ▼
trace_svc.acquire_trace(trace_source, run_dir/traces/)
    │  success → (trace_path, observation_count)
    │  TraceIngestionError → set_failed(error_code)  ──► FAILED
    │  other Exception → set_failed("TRACE_NOT_FOUND") ► FAILED
    │
    ▼
update_stage(task_id, "running_pipeline")
    │  stage = "running_pipeline"
    │
    ▼
async with semaphore:
    pipeline_svc.execute_pipeline(trace_file, output_dir, ...)
    │  Exception → set_failed("PIPELINE_FAILED")  ───────► FAILED
    │
    ▼
_read_json(run_dir/pipeline_summary.json)
    │  Exception → set_failed("STORAGE_ERROR")  ─────────► FAILED
    │
    ▼
_build_result(results, summary, obs_count, run_dir, elapsed)
    │
    ▼
set_completed(task_id, result)
    │  RUNNING → COMPLETED
    │  stage = "done"
```

### Path Resolution & Traversal Guard

`_resolve_run_dir` in [bucket_task_runner.py](../main/workers/bucket_task_runner.py)
performs a **defence-in-depth check** on top of Pydantic's `no_path_separators`
validator:

```python
for segment in (experiment_id, run_id):
    if "/" in segment or "\\" in segment or ".." in segment:
        raise ValueError(f"Illegal path segment: {segment!r}")
path = workspace_dir / experiment_id / run_id
path.mkdir(parents=True, exist_ok=True)
```

This means even if the Pydantic validator is somehow bypassed (e.g. a future
code path that constructs the request manually), the worker will still reject
path-traversal attempts.

---

## Service: `TraceService`

`TraceService.acquire_trace(trace_source, dest_dir)` writes
`dest_dir/raw_trace.json` and returns `(path, observation_count)`.

### File Source Path

```
asyncio.to_thread(shutil.copy2, file_path, str(dest))
    │
    ▼
_load_and_validate(str(dest))
    │  returns list of observation dicts
    ▼
(dest_path, len(data))
```

Errors raised:
- `TRACE_NOT_FOUND` — file missing (`FileNotFoundError`) or unreadable (`OSError`)
- `TRACE_PARSE_ERROR` — not a JSON array, empty, or entries lack `id` field

### Langfuse Source Path

```
asyncio.to_thread(
    _fetch_langfuse_observations,
    base_url, public_key, secret_key,
    from_timestamp, page_size, max_pages, include_observations
)
    │
    ├── _parse_iso_to_utc(from_timestamp)
    │     LANGFUSE_FETCH_ERROR if unparseable
    │
    ├── Langfuse(public_key, secret_key, host=base_url)
    │
    ├── _list_traces(client, from_utc, page_size, max_pages)
    │     paginates: page=1..max_pages, stops when resp.data empty
    │     or page >= resp.meta.total_pages
    │
    │     TRACE_NOT_FOUND if no traces found
    │
    └── for each trace:
          client.api.legacy.observations_v1.get_many(trace_id, limit=500)
          _format_observations(raw_obs)
    │
asyncio.to_thread(_write_json, observations, str(dest))
    │
_load_and_validate(str(dest))
```

### Observation Normalisation

`_format_observations` transforms raw Langfuse observation dicts:

```
raw Langfuse observation
    {
      "id": "obs-123",
      "type": "SPAN",
      "name": "tool_call",
      "start_time": datetime(2024, 1, 15, 10, 22, 14, tzinfo=UTC),
      "end_time": datetime(2024, 1, 15, 10, 22, 16, tzinfo=UTC),
      "parent_observation_id": "obs-100",
      "input": {"tool": "kubectl", "args": ["get", "pods"]},
      "output": {"result": "pod-1 Running"},
      "metadata": {"model": "gpt-4o"}
    }
        │
        ▼ _compute_depths() — memoised parent-chain walk
        │ depth_map["obs-123"] = 1 (obs-100 is root)
        │
        ▼ _fmt_ts() — normalise to "YYYY-MM-DDTHH:MM:SS.mmmZ"
        │
        ▼ _to_json_str() — dict/list → JSON string; None → None
        │
        ▼ sort by (depth, startTime)
        │
pipeline format
    {
      "id":        "obs-123",
      "type":      "SPAN",
      "name":      "tool_call",
      "startTime": "2024-01-15T10:22:14.000Z",
      "endTime":   "2024-01-15T10:22:16.000Z",
      "depth":     1,
      "input":     "{\"tool\": \"kubectl\", \"args\": [\"get\", \"pods\"]}",
      "output":    "{\"result\": \"pod-1 Running\"}",
      "metadata":  "{\"model\": \"gpt-4o\"}"
    }
```

The depth sort guarantees that parent spans always precede their children,
which the Phase 0 LLM expects when processing the event sequence.

### Validation

`_load_and_validate` checks:
1. File is valid JSON (`json.load` — raises `TRACE_PARSE_ERROR` on decode error)
2. Top-level value is a list (`isinstance(data, list)`)
3. List is non-empty
4. First element is a dict with an `"id"` field

---

## Service: `BucketPipelineService`

`BucketPipelineService.execute_pipeline(trace_file, output_dir, batch_size,
store_to_mongodb, config)` orchestrates Phase 0 then Phase 1 for every fault.

### Phase 0 — Fault Bucketing

```python
pipeline = FaultBucketingPipeline(
    trace_file_path=trace_file,
    output_dir=str(buckets_dir),   # → output_dir/fault_buckets/
    config=config,
    batch_size=batch_size,
)
buckets = await pipeline.run()
# buckets: dict[fault_id → FaultBucket]
```

`FaultBucketingPipeline` (in `fault_analyzer/`) sends the trace events to an
LLM in batches of `batch_size`.  The LLM classifies each event as belonging to
a specific fault lifecycle, producing one `FaultBucket` object per detected fault.
Each bucket holds the subset of trace events relevant to that fault plus metadata:
`fault_id`, `fault_name`, `severity`, `injection_timestamp`, etc.

If `buckets` is empty (no faults detected), Phase 1 is skipped and an empty
list is returned.

### Phase 1 — Metrics Extraction

For each `fault_id, bucket` in `buckets.items()`:

```
1. bucket.to_dict()  →  { events: [...], fault_id, fault_name, severity, ... }

2. Derive a filesystem-safe filename prefix:
   safe_name = f"{fault_id}_{run_id}".replace("/","_").replace(" ","_")

3. Write events to:
   metrics/{safe_name}_trace.json

4. _build_fault_config_from_bucket(bucket_dict)
   →  normalised fault config dict (see next section)

5. Write config to:
   metrics/{safe_name}_fault_config.json

6. extractor = TraceMetricsExtractor(config, fault_config_path)
   extraction_result = await extractor.extract_metrics_async(
       trace_tmp, store_to_mongodb=store_to_mongodb
   )

7. result_dict = {
       fault_id, run_id, fault_name,
       quantitative: ExtractionResult.quantitative.model_dump(),
       qualitative:  ExtractionResult.qualitative.model_dump(),
       token_usage:  ExtractionResult.token_usage.to_dict(),
   }

8. Write to:
   metrics/{safe_name}_metrics.json
```

If extraction fails for a fault (exception in step 6), the fault is logged and
**skipped** — the pipeline continues to extract remaining faults rather than
aborting the whole run.

### Fault Config Normalisation

`_build_fault_config_from_bucket` maps bucket metadata to the schema expected
by `TraceMetricsExtractor`:

```
Bucket metadata                         Fault config schema
──────────────────────────────────────────────────────────────
fault_id                          →  fault_id
fault_name                        →  fault_name
severity                          →  fault_category
experiment_id                     →  experiment_id
run_id                            →  run_id
injection_timestamp (or detected_at)  →  injection_timestamp
target_pod                        →  fault_configuration.target_service
namespace                         →  fault_configuration.target_namespace
ground_truth (dict)               →  ground_truth
  + top-level ideal_course_of_action    → ground_truth.ideal_course_of_action
  + top-level ideal_tool_usage_trajectory → ground_truth.ideal_tool_usage_trajectory
agent_id                          →  agent.agent_id
agent_name                        →  agent.agent_name
agent_version                     →  agent.agent_version
```

The promotion of `ideal_course_of_action` / `ideal_tool_usage_trajectory` handles
both storage layouts (top-level and nested under `ground_truth`).

### Summary File

After all faults are processed, `pipeline_summary.json` is written to `output_dir`:

```json
{
  "trace_file":       "run42.json",
  "run_id":           "run-42",
  "total_faults":     3,
  "faults_extracted": 3,
  "bucketing_tokens": {
    "input": 4500, "output": 1200, "total": 5700
  },
  "extraction_tokens": {
    "input": 6800, "output": 2100, "total": 8900
  },
  "fault_results": [
    {
      "fault_id":           "network-latency-001",
      "fault_name":         "Network Latency Injection",
      "mongodb_document_id": null
    }
  ]
}
```

`mongodb_document_id` is `null` when `storage_config.type == "local"`.

---

## Filesystem Layout

After a successful run, the workspace directory looks like:

```
workspace/
└── {experiment_id}/
    └── {run_id}/
        ├── traces/
        │   └── raw_trace.json          ← copy/fetch of original trace
        ├── fault_buckets/
        │   ├── {fault_id}_bucket.json  ← one file per fault (Phase 0 output)
        │   └── manifest.json
        ├── metrics/
        │   ├── {safe_name}_trace.json       ← per-fault event slice (temp)
        │   ├── {safe_name}_fault_config.json ← per-fault config (temp)
        │   └── {safe_name}_metrics.json     ← per-fault metrics (Phase 1 output)
        ├── pipeline_summary.json       ← token usage + fault list
        └── pipeline.log
```

The `_trace.json` and `_fault_config.json` files in `metrics/` are
temporary inputs consumed by `TraceMetricsExtractor` and are retained for
debugging.

---

## MongoDB: `pipeline_tasks`

### Document Shape

```json
{
  "task_id":       "550e8400-e29b-41d4-a716-446655440000",
  "agent_id":      "my-k8s-agent",
  "experiment_id": "exp-001",
  "run_id":        "run-42",
  "status":        "COMPLETED",
  "stage":         "done",
  "created_at":    "2024-01-15T10:20:00.000Z",
  "updated_at":    "2024-01-15T10:21:30.000Z",
  "started_at":    "2024-01-15T10:20:01.000Z",
  "completed_at":  "2024-01-15T10:21:30.000Z",
  "request": {
    "agent_id": "my-k8s-agent",
    "experiment_id": "exp-001",
    "run_id": "run-42",
    "trace_source": { "type": "file", "file_path": "/data/traces/run42.json" },
    "llm_batch_size": 5,
    "storage_config": { "type": "local", "container_name": "" }
  },
  "result": { ... },
  "error": null
}
```

When `status == "FAILED"`:
```json
{
  "error": {
    "error_code":   "TRACE_NOT_FOUND",
    "message":      "Trace file not found: /data/traces/run42.json",
    "failed_stage": "acquiring_trace",
    "detail":       "Traceback ..."
  }
}
```

### Indexes

| Name | Keys | Unique | Purpose |
|---|---|---|---|
| `idx_task_id_unique` | `task_id` | Yes | Primary task lookup by UUID |
| `idx_agent_exp_run` | `agent_id`, `experiment_id`, `run_id` | No | Duplicate submission guard |
| `idx_status_created` | `status`, `created_at` (desc) | No | Status-filtered recent-first queries |
| `idx_created_at` | `created_at` | No | TTL / date-range scans |

### State Machine Transitions

Each `update_one` call includes a **status guard** in its filter:

```
create_task()
    insert { status: "PENDING", stage: "pending" }

set_started()
    filter: { status: "PENDING" }          ← only from PENDING
    update: { status: "RUNNING", stage: "acquiring_trace" }
    $currentDate: { started_at, updated_at }

update_stage(stage)
    filter: { task_id }                    ← no status guard, stage-only update
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

The status guard on `set_completed` means that if two concurrent writes both
try to complete the same task (which should not happen, but defends against it),
only the first one succeeds; the second gets `matched_count == 0` and raises
`ValueError`.

---

## Concurrency Model

```
HTTP requests (many)
      │
      ▼
FastAPI event loop  (one process)
      │
      ▼
background_tasks.add_task(run_task, ...)
      │   tasks queue in PENDING state in MongoDB
      ▼
run_task coroutine
      │   stages 1–2 run freely (I/O is non-blocking)
      │
      ▼
async with semaphore:          ← app.state.semaphore
      │   capacity = API_MAX_CONCURRENT_TASKS (default 4)
      │   blocks here if 4 pipeline runs are already active
      ▼
BucketPipelineService.execute_pipeline()
      │   Phase 0: LLM calls (async HTTP via AzureLLMClient)
      │   Phase 1: LLM calls + file I/O in asyncio.to_thread
      ▼
release semaphore
```

Tasks can be submitted freely — they queue at `PENDING` in MongoDB.  The
semaphore only gates the heavy compute section.  This means:

- `PENDING` tasks accumulate if the system is under load.
- Exactly `API_MAX_CONCURRENT_TASKS` Phase-0+1 pipelines run simultaneously.
- File I/O inside the pipeline runs in `asyncio.to_thread` to avoid blocking
  the event loop.
- LLM calls use `async`/`await` throughout (Motor for MongoDB, httpx for
  Azure OpenAI).

---

## Task Result Payload

`_build_result` assembles the `result` dict stored in the completed task doc:

```
faults list: one entry per fault_id in the pipeline output
    fault_detected == "Yes"  →  status: "closed"
    anything else            →  status: "open"

storage_paths: absolute paths for run_dir/traces/, fault_buckets/, metrics/,
               pipeline_summary.json, pipeline.log

token_usage:
    bucketing_input  = pipeline.total_input_tokens
    bucketing_output = pipeline.total_output_tokens
    extraction_input  = sum(r.token_usage.input_tokens for r in results)
    extraction_output = sum(r.token_usage.output_tokens for r in results)
    total = bucketing_total + extraction_total

processing_time_seconds: wall-clock time for the semaphore-gated section only
```

---

## Error Reference

| Error Code | Origin | Trigger |
|---|---|---|
| `TASK_ALREADY_ACTIVE` | Router (409) | `find_active_task` returns a PENDING/RUNNING doc |
| `TRACE_NOT_FOUND` | Worker | File missing, unreadable, or Langfuse returned no traces |
| `TRACE_PARSE_ERROR` | Worker | Trace JSON is not a non-empty array of objects with `id` |
| `LANGFUSE_FETCH_ERROR` | Worker | Langfuse API call failed, bad credentials, or unparseable timestamp |
| `PIPELINE_FAILED` | Worker | Unclassified exception in `execute_pipeline` |
| `STORAGE_ERROR` | Worker | `pipeline_summary.json` unreadable or `_build_result` fails |
| `TASK_NOT_FOUND` | Router (404) | `GET /tasks/{id}` for unknown `task_id` |
| `MONGODB_ERROR` | Router (500) | `create_task` DB insert fails |

Worker error codes appear in `task.error.error_code`; they are **not** HTTP
status codes (the 202 was already returned).

---

## CLI Usage

The CLI bypasses the HTTP layer and calls `BucketPipelineService` directly.
Useful for local development and testing without a running server.

```bash
# Via module
python -m main.cli.run_bucketing_and_extraction_pipeline \
    --trace-file  /data/traces/run42.json \
    --output-dir  /tmp/output/exp-001/run-42 \
    --batch-size  10 \
    --store

# Arguments
--trace-file    Path to raw Langfuse trace JSON array
--output-dir    Root output directory (will be created if absent)
--batch-size    LLM events per batch for Phase 0 (default: 5)
--store         Flag: also write metrics to MongoDB
```

Outputs are written to:
```
/tmp/output/exp-001/run-42/
├── fault_buckets/
├── metrics/
└── pipeline_summary.json
```
