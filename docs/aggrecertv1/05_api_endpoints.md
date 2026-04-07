# 05 — API Endpoints

## Router

`main/routers/aggregation_certification.py` mounts under prefix `/api/v1` (set in `main/main.py`).
It exposes two endpoints:

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/aggregation-certification` | Submit a cert task; returns cert_task_id immediately |
| GET | `/api/v1/cert-tasks/{cert_task_id}` | Poll cert task status |

The router contains **only** HTTP handler logic: request validation, service dispatch, and
response serialisation. No pipeline, MongoDB, or file I/O logic lives here.

---

## POST `/api/v1/aggregation-certification`

### Request Body (`application/json`)

```json
{
  "agent_id": "agent_v2_4_1",
  "agent_name": "Agent V2.4.1",
  "experiment_id": "exp_001",
  "certification_run_id": "cert_run_001",
  "runs_per_fault": 30,
  "storage_config": {
    "type": "local",
    "metrics_dir": "/srv/projects/mas/mars/agent-cert/certifier/workspace/exp_001/run_001/metrics",
    "container_name": ""
  }
}
```

### Request Field Reference

#### Top-level

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `agent_id` | string | yes | — | Agent identifier; used to filter metrics and scope workspace |
| `agent_name` | string | yes | — | Human-readable agent name for certification scorecard |
| `experiment_id` | string | yes | — | Experiment identifier; used to scope workspace path and query metrics |
| `certification_run_id` | string | no | `""` | Optional identifier for this certification run (metadata only) |
| `runs_per_fault` | int (1–1000) | no | `30` | Expected runs per fault; used by aggregator for completeness checks |
| `storage_config` | object | yes | — | Storage backend config — iteration 1 only supports `"local"` |

#### `storage_config` when `type = "local"` (iteration 1)

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | `"local"` | yes | Selects local-directory metrics fetch mode |
| `metrics_dir` | string | yes | Absolute path on the server to a directory containing `*metrics.json` files |
| `container_name` | string | no | Ignored in iteration 1; reserved for blob storage |

#### `storage_config` when `type = "mongodb"` or `"blob_storage"` or `"hybrid"`

Not supported in iteration 1. Returns HTTP 400 `INVALID_REQUEST`.

### Validation Rules (enforced by Pydantic model + handler)

1. `agent_id`, `agent_name`, `experiment_id` — non-empty strings, max 128 chars.
   `agent_id` and `experiment_id` must not contain path separators (`/`, `\`, `..`) as
   they are used to build the workspace path.
2. `storage_config.type` — must be one of `"local"`, `"mongodb"`, `"blob_storage"`, `"hybrid"`.
   Only `"local"` is accepted in iteration 1; others return HTTP 400.
3. When `type = "local"`: `metrics_dir` must be provided and non-empty.
4. `runs_per_fault` — integer in range [1, 1000].

Pydantic validation failures return FastAPI's default 422 Unprocessable Entity response.

### Handler Logic

```
POST /api/v1/aggregation-certification
│
├── 1. Pydantic model validation (automatic, returns 422 on failure)
│
├── 2. storage_config.type check
│      if not "local": return 400 INVALID_REQUEST
│
├── 3. metrics_dir existence check
│      asyncio.to_thread: Path(metrics_dir).is_dir()
│      if False: return 400 METRICS_NOT_FOUND
│
├── 4. Metrics file discovery + agent_id filter
│      asyncio.to_thread: discover_metrics_files(metrics_dir)
│      asyncio.to_thread: validate_agent_metrics(files, agent_id)
│      if 0 matching docs: return 400 METRICS_NOT_FOUND
│
├── 5. Duplicate submission check
│      cert_session_service.find_active_task(agent_id, experiment_id)
│      if found: return 409 TASK_ALREADY_ACTIVE
│
├── 6. Generate cert_task_id = str(uuid.uuid4())
│
├── 7. Create task session
│      cert_session_service.create_task(cert_task_id, agent_id, agent_name,
│                                       experiment_id, certification_run_id,
│                                       request_snapshot)
│
├── 8. Dispatch background task
│      background_tasks.add_task(run_cert_task, cert_task_id, request, app_state)
│
└── 9. Return 202 Accepted
```

### Response — 202 Accepted

```json
{
  "status": "accepted",
  "cert_task_id": "7c3a9f12-4b8e-41d6-a2f7-1c9e6d5b3a08",
  "poll_url": "/api/v1/cert-tasks/7c3a9f12-4b8e-41d6-a2f7-1c9e6d5b3a08"
}
```

### Response — 400 Bad Request (unsupported storage type)

```json
{
  "status": "error",
  "error_code": "INVALID_REQUEST",
  "message": "storage_config.type 'mongodb' is not supported in iteration 1. Use 'local'.",
  "details": {
    "failed_stage": "validation",
    "error": "storage_type=mongodb"
  }
}
```

### Response — 400 Bad Request (metrics not found)

```json
{
  "status": "error",
  "error_code": "METRICS_NOT_FOUND",
  "message": "No metrics documents found for agent_id='agent_v2_4_1' in '/path/to/metrics'",
  "details": {
    "failed_stage": "metrics_validation",
    "error": "agent_id filter returned 0 matches"
  }
}
```

### Response — 409 Conflict (duplicate active task)

```json
{
  "status": "error",
  "error_code": "TASK_ALREADY_ACTIVE",
  "message": "A certification task is already RUNNING for agent_v2_4_1/exp_001",
  "details": {
    "cert_task_id": "existing-uuid",
    "status": "RUNNING",
    "stage": "running_pipeline"
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
      "loc": ["body", "agent_name"],
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
  "message": "Failed to create certification task session",
  "details": {
    "failed_stage": "session_create",
    "error": "..."
  }
}
```

---

## GET `/api/v1/cert-tasks/{cert_task_id}`

### Path Parameter

| Param | Type | Description |
|---|---|---|
| `cert_task_id` | UUID string | The `cert_task_id` returned by the POST endpoint |

### Handler Logic

```
GET /api/v1/cert-tasks/{cert_task_id}
│
├── 1. cert_session_service.get_task(cert_task_id)
│      if None: return 404
│
└── 2. Serialise document to CertTaskStatusResponse and return 200
```

### Response Shape by Status

#### In-progress (PENDING or RUNNING)

```json
{
  "cert_task_id": "7c3a9f12-4b8e-41d6-a2f7-1c9e6d5b3a08",
  "status": "RUNNING",
  "stage": "running_pipeline",
  "agent_id": "agent_v2_4_1",
  "agent_name": "Agent V2.4.1",
  "experiment_id": "exp_001",
  "certification_run_id": "cert_run_001",
  "created_at": "2026-04-07T11:00:00.000Z",
  "updated_at": "2026-04-07T11:00:35.000Z",
  "started_at": "2026-04-07T11:00:02.000Z",
  "completed_at": null,
  "data": null,
  "error": null
}
```

#### Completed

```json
{
  "cert_task_id": "7c3a9f12-4b8e-41d6-a2f7-1c9e6d5b3a08",
  "status": "COMPLETED",
  "stage": "done",
  "agent_id": "agent_v2_4_1",
  "agent_name": "Agent V2.4.1",
  "experiment_id": "exp_001",
  "certification_run_id": "cert_run_001",
  "created_at": "2026-04-07T11:00:00.000Z",
  "updated_at": "2026-04-07T11:04:15.000Z",
  "started_at": "2026-04-07T11:00:02.000Z",
  "completed_at": "2026-04-07T11:04:15.000Z",
  "data": {
    "total_documents": 120,
    "total_fault_categories": 3,
    "fault_categories": ["compute", "network", "storage"],
    "certification_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "storage_paths": {
      "aggregated_scorecard": "workspace/cert/agent_v2_4_1/exp_001/aggregated_scorecard_output_agent_v2_4_1.json",
      "certification_report": "workspace/cert/agent_v2_4_1/exp_001/certification_report_agent_v2_4_1.json",
      "summary": "workspace/cert/agent_v2_4_1/exp_001/pipeline_summary.json"
    },
    "processing_time_seconds": 243.7
  },
  "error": null
}
```

#### Failed

```json
{
  "cert_task_id": "7c3a9f12-4b8e-41d6-a2f7-1c9e6d5b3a08",
  "status": "FAILED",
  "stage": "running_pipeline",
  "agent_id": "agent_v2_4_1",
  "agent_name": "Agent V2.4.1",
  "experiment_id": "exp_001",
  "certification_run_id": "cert_run_001",
  "created_at": "2026-04-07T11:00:00.000Z",
  "updated_at": "2026-04-07T11:02:10.000Z",
  "started_at": "2026-04-07T11:00:02.000Z",
  "completed_at": "2026-04-07T11:02:10.000Z",
  "data": null,
  "error": {
    "error_code": "AGGREGATION_FAILED",
    "message": "LLM Council returned empty consensus for fault category 'compute'",
    "failed_stage": "running_pipeline",
    "detail": "Traceback (most recent call last): ..."
  }
}
```

### Response — 404 Not Found

```json
{
  "status": "error",
  "error_code": "TASK_NOT_FOUND",
  "message": "No certification task found with id 7c3a9f12-4b8e-41d6-a2f7-1c9e6d5b3a08"
}
```

---

## Error Code Reference

| Code | HTTP | Stage | Cause |
|---|---|---|---|
| `INVALID_REQUEST` | 422 | — | Pydantic validation failure (FastAPI default shape) |
| `INVALID_REQUEST` | 400 | validation | `storage_config.type` not supported in iteration 1 |
| `METRICS_NOT_FOUND` | 400 | metrics_validation | `metrics_dir` does not exist or contains no matching documents |
| `TASK_ALREADY_ACTIVE` | 409 | — | Duplicate PENDING/RUNNING task for same `(agent_id, experiment_id)` |
| `MONGODB_ERROR` | 500 | session_create | Session document insert failed at POST time |
| `TASK_NOT_FOUND` | 404 | — | `cert_task_id` not in `certification_tasks` collection |
| `AGGREGATION_FAILED` | — | running_pipeline | `AggregationOrchestrator` raised an exception (recorded in task error) |
| `CERT_GENERATION_FAILED` | — | running_pipeline | `CertificationPipeline` raised an exception (recorded in task error) |
| `STORAGE_ERROR` | — | storing_metadata | Cannot write to `workspace/cert/` or MongoDB metadata write failed |

> Note: error codes after `MONGODB_ERROR` are **async** — they are recorded inside the
> `CertificationTask` document, not returned as HTTP status codes. The POST always returns 202
> once the task is queued. The GET endpoint surfaces async errors via `status=FAILED` + `error` field.

---

## Polling Strategy (Client Guidance)

Poll `GET /api/v1/cert-tasks/{cert_task_id}` until `status` is `COMPLETED` or `FAILED`.

Recommended back-off:

```
initial_delay = 10s
max_delay = 60s
multiplier = 1.5
timeout = 20min
```

Typical certification pipeline durations:
- Small experiment (1 fault category, 10 runs): 1–2 min
- Medium experiment (3 fault categories, 30 runs): 3–6 min
- Large experiment (5+ fault categories, 30+ runs): 6–15 min

LLM Council synthesis (Phase 2) and cert_builder narrative generation (Phase 3) dominate
latency. Azure OpenAI throughput and concurrency limits are the primary external factor.
