# 05 — API Endpoints

## Router

`main/routers/bucketing_extraction.py` mounts under prefix `/api/v1` (set in `main/main.py`).
It exposes two endpoints:

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/bucketing-extraction` | Submit a pipeline task; returns task_id immediately |
| GET | `/api/v1/tasks/{task_id}` | Poll task status |

The router contains **only** HTTP handler logic: request validation, service dispatch, and
response serialisation. No pipeline, MongoDB, or file I/O logic lives here.

---

## POST `/api/v1/bucketing-extraction`

### Request Body (`application/json`)

```json
{
  "agent_id": "agent_v2_4_1",
  "experiment_id": "exp_001",
  "run_id": "run_001",

  "trace_source": {
    "type": "langfuse",
    "base_url": "http://100.78.130.20:3001",
    "public_key": "pk-lf-78b3d210-9695-41b3-8d29-d0b1ddd1ff4d",
    "secret_key": "sk-lf-ad2112b2-6421-4ac1-abea-51d3dda54902",
    "from_timestamp": "2026-04-07T05:00:00Z",
    "page_size": 100,
    "max_pages": 20,
    "include_observations": true
  },

  "llm_batch_size": 5,

  "storage_config": {
    "type": "local",
    "container_name": "cert-artifacts"
  }
}
```

#### Alternative — file source

```json
{
  "agent_id": "agent_v2_4_1",
  "experiment_id": "exp_001",
  "run_id": "run_001",

  "trace_source": {
    "type": "file",
    "file_path": "/srv/projects/mas/mars/agent-cert/certifier/workspace/traces/trace.json"
  },

  "llm_batch_size": 5,

  "storage_config": {
    "type": "local",
    "container_name": ""
  }
}
```

### Request Field Reference

#### Top-level

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `agent_id` | string | yes | — | Agent identifier; stored in task document for querying |
| `experiment_id` | string | yes | — | Experiment identifier; used to scope workspace path |
| `run_id` | string | yes | — | Run identifier; used to scope workspace path |
| `trace_source` | object | yes | — | Discriminated union — see below |
| `llm_batch_size` | int (1–50) | no | 5 | Events per LLM classification batch in Phase 0 |
| `storage_config` | object | yes | — | Storage backend config — iteration 1 only supports `local` |

#### `trace_source` when `type = "file"`

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | `"file"` | yes | Selects file-read mode |
| `file_path` | string | yes | Absolute path on the server; must exist and be readable |

#### `trace_source` when `type = "langfuse"`

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `type` | `"langfuse"` | yes | — | Selects Langfuse-fetch mode |
| `base_url` | string (HTTP URL) | yes | — | Langfuse server base URL |
| `public_key` | string | yes | — | Langfuse project public key |
| `secret_key` | string | yes | — | Langfuse project secret key (not stored in DB) |
| `from_timestamp` | string (ISO-8601 UTC) | yes | — | Lower bound for trace fetch |
| `page_size` | int (1–500) | no | 100 | Observations per page |
| `max_pages` | int (1–100) | no | 20 | Hard cap on pages fetched |
| `include_observations` | bool | no | true | Whether to fetch spans per trace |

#### `storage_config`

| Field | Type | Required | Values | Description |
|---|---|---|---|---|
| `type` | string | yes | `local` (iteration 1), `blob_storage`, `mongodb`, `hybrid` | Backend for artifact storage |
| `container_name` | string | no | — | Azure Blob container or MongoDB DB name (ignored in iteration 1) |

### Validation Rules (enforced by Pydantic model)

1. `agent_id`, `experiment_id`, `run_id` — non-empty strings, max 128 chars. `experiment_id` and `run_id` must not contain path separators (`/`, `\`, `..`) as they are used to build the workspace path.
2. `trace_source.type` — must be `"file"` or `"langfuse"`; no other values accepted.
3. When `type = "file"`: `file_path` must be provided and non-empty.
4. When `type = "langfuse"`: `base_url`, `public_key`, `secret_key`, `from_timestamp` are all required.
5. `llm_batch_size` — integer in range [1, 50].
6. `storage_config.type` — must be one of the four allowed values.

Pydantic validation failures return FastAPI's default 422 Unprocessable Entity response.

### Handler Logic

```
POST /api/v1/bucketing-extraction
│
├── 1. Pydantic model validation (automatic, returns 422 on failure)
│
├── 2. Duplicate submission check
│      session_service.find_active_task(experiment_id, run_id)
│      → if found: return 409 Conflict (see §session_management duplicate guard)
│
├── 3. Generate task_id = str(uuid.uuid4())
│
├── 4. Create task session
│      session_service.create_task(task_id, agent_id, experiment_id, run_id, request_snapshot)
│      request_snapshot strips secret_key before storing
│
├── 5. Dispatch background task
│      background_tasks.add_task(run_task, task_id, request, app_state)
│
└── 6. Return 202 Accepted
```

### Response — 202 Accepted

```json
{
  "status": "accepted",
  "task_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "poll_url": "/api/v1/tasks/3fa85f64-5717-4562-b3fc-2c963f66afa6"
}
```

### Response — 409 Conflict (duplicate active task)

```json
{
  "status": "error",
  "error_code": "TASK_ALREADY_ACTIVE",
  "message": "A pipeline task is already RUNNING for exp_001/run_001",
  "details": {
    "task_id": "existing-uuid",
    "status": "RUNNING",
    "stage": "bucketing"
  }
}
```

### Response — 422 Unprocessable Entity (Pydantic validation)

FastAPI standard format:

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "trace_source", "secret_key"],
      "msg": "Field required",
      "input": {}
    }
  ]
}
```

### Response — 500 Internal Server Error (session creation failed)

```json
{
  "status": "error",
  "error_code": "MONGODB_ERROR",
  "message": "Failed to create task session",
  "details": {
    "failed_stage": "session_create",
    "error": "..."
  }
}
```

---

## GET `/api/v1/tasks/{task_id}`

### Path Parameter

| Param | Type | Description |
|---|---|---|
| `task_id` | UUID string | The `task_id` returned by the POST endpoint |

### Handler Logic

```
GET /api/v1/tasks/{task_id}
│
├── 1. session_service.get_task(task_id)
│      → if None: return 404
│
└── 2. Serialise document to TaskStatusResponse and return 200
```

### Response Shape by Status

#### In-progress (PENDING or RUNNING)

```json
{
  "task_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "RUNNING",
  "stage": "bucketing",
  "agent_id": "agent_v2_4_1",
  "experiment_id": "exp_001",
  "run_id": "run_001",
  "created_at": "2026-04-07T10:00:00.000Z",
  "updated_at": "2026-04-07T10:00:18.000Z",
  "started_at": "2026-04-07T10:00:05.000Z",
  "completed_at": null,
  "data": null,
  "error": null
}
```

#### Completed

```json
{
  "task_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "COMPLETED",
  "stage": "done",
  "agent_id": "agent_v2_4_1",
  "experiment_id": "exp_001",
  "run_id": "run_001",
  "created_at": "2026-04-07T10:00:00.000Z",
  "updated_at": "2026-04-07T10:01:02.000Z",
  "started_at": "2026-04-07T10:00:05.000Z",
  "completed_at": "2026-04-07T10:01:02.000Z",
  "data": {
    "total_observations": 1523,
    "total_faults_detected": 3,
    "faults": [
      {
        "fault_id": "pod-delete",
        "fault_name": "pod-delete",
        "severity": "critical",
        "status": "closed",
        "detected_at": "2026-04-07T10:00:02Z",
        "mitigated_at": "2026-04-07T10:00:40Z"
      }
    ],
    "storage_paths": {
      "traces_dir": "workspace/exp_001/run_001/traces/",
      "fault_buckets_dir": "workspace/exp_001/run_001/fault_buckets/",
      "metrics_dir": "workspace/exp_001/run_001/metrics/",
      "summary": "workspace/exp_001/run_001/pipeline_summary.json",
      "log": "workspace/exp_001/run_001/pipeline.log"
    },
    "token_usage": {
      "bucketing_input_tokens": 4250,
      "bucketing_output_tokens": 1100,
      "extraction_input_tokens": 8500,
      "extraction_output_tokens": 2200,
      "total_tokens": 16050
    },
    "processing_time_seconds": 57.3
  },
  "error": null
}
```

#### Failed

```json
{
  "task_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "FAILED",
  "stage": "bucketing",
  "agent_id": "agent_v2_4_1",
  "experiment_id": "exp_001",
  "run_id": "run_001",
  "created_at": "2026-04-07T10:00:00.000Z",
  "updated_at": "2026-04-07T10:00:45.000Z",
  "started_at": "2026-04-07T10:00:05.000Z",
  "completed_at": "2026-04-07T10:00:45.000Z",
  "data": null,
  "error": {
    "error_code": "BUCKETING_FAILED",
    "message": "LLM classifier returned empty response on batch 3",
    "failed_stage": "bucketing",
    "detail": "Traceback ..."
  }
}
```

### Response — 404 Not Found

```json
{
  "status": "error",
  "error_code": "TASK_NOT_FOUND",
  "message": "No task found with id 3fa85f64-5717-4562-b3fc-2c963f66afa6"
}
```

---

## Error Code Reference

| Code | HTTP | Stage | Cause |
|---|---|---|---|
| `INVALID_REQUEST` | 422 | — | Pydantic validation failure (FastAPI default shape) |
| `TASK_ALREADY_ACTIVE` | 409 | — | Duplicate PENDING/RUNNING task for same (agent, exp, run) |
| `MONGODB_ERROR` | 500 | session_create | Session document insert failed at POST time |
| `TASK_NOT_FOUND` | 404 | — | `task_id` not in `pipeline_tasks` collection |
| `TRACE_NOT_FOUND` | — | trace_fetch | Local file not found at `file_path` (recorded in task error) |
| `TRACE_PARSE_ERROR` | — | validation | Trace JSON invalid or wrong format (recorded in task error) |
| `LANGFUSE_FETCH_ERROR` | — | trace_fetch | Langfuse SDK raised an exception (recorded in task error) |
| `BUCKETING_FAILED` | — | bucketing | `FaultBucketingPipeline` raised an exception |
| `METRICS_EXTRACTION_FAILED` | — | metrics_extraction | `TraceMetricsExtractor` raised an exception |
| `STORAGE_ERROR` | — | storage | Cannot write to `workspace/` directory |

> Note: error codes after `MONGODB_ERROR` are **async** — they are recorded inside the
> `PipelineTask` document, not returned as HTTP status codes. The POST always returns 202 once
> the task is queued. The GET endpoint surfaces async errors via `status=FAILED` + `error` field.

---

## Polling Strategy (Client Guidance)

The caller must poll `GET /api/v1/tasks/{task_id}` until `status` is `COMPLETED` or `FAILED`.

Recommended back-off:

```
initial_delay = 5s
max_delay = 30s
multiplier = 1.5
timeout = 10min
```

Typical pipeline durations:
- Small trace (< 200 observations): 30–60 s
- Medium trace (200–500 observations): 1–3 min
- Large trace (> 500 observations): 3–8 min

These are approximate; LLM availability and batch size dominate actual latency.
