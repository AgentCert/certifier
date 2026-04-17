# API Reference — AgentCert Certifier

Base URL: `http://localhost:8000`  
Interactive docs (Swagger UI): `http://localhost:8000/docs`

All endpoints are under `/api/v1/`. Both pipelines follow an **async job pattern**:

```
POST /api/v1/<endpoint>   →  202 Accepted  { task_id, poll_url }
GET  /api/v1/<poll_url>   →  task document with status / result / error
```

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/bucketing-extraction` | Submit a fault bucketing + metrics extraction job (Phase 0+1) |
| `GET` | `/api/v1/tasks/{task_id}` | Poll Phase 0+1 task status |
| `POST` | `/api/v1/aggregation-certification` | Submit an aggregation + certification job (Phase 2+3) |
| `GET` | `/api/v1/cert-tasks/{cert_task_id}` | Poll Phase 2+3 task status |

---

## POST /api/v1/bucketing-extraction

Accepts a trace source, runs fault bucketing (Phase 0) and metrics extraction (Phase 1), and returns a `task_id` immediately. The pipeline runs in the background.

### Request body

```jsonc
{
  "agent_id":      "string",   // required, 1–128 chars, no path separators
  "experiment_id": "string",   // required, 1–128 chars
  "run_id":        "string",   // required, 1–128 chars — unique per agent run
  "trace_source":  { ... },    // required — see Trace Sources below
  "llm_batch_size": 5,         // optional, 1–50, default 5
  "storage_config": {          // optional
    "type": "local"            // "local" | "mongodb" | "hybrid" | "blob_storage"
  }
}
```

`storage_config.type` controls where extracted metrics are persisted after Phase 1:
- `"local"` (default) — filesystem only (`workspace/{agent_id}/{experiment_id}/fault-bucketing/{run_id}/metrics/`)
- `"mongodb"` — MongoDB `agent_run_metrics` collection only
- `"hybrid"` — both filesystem and MongoDB
- `"blob_storage"` — Azure Blob Storage (requires `AZURE_STORAGE_CONNECTION_STRING`)

### Trace Sources

**File (server-side path):**
```jsonc
{
  "type": "file",
  "file_path": "/absolute/path/to/trace.json"
}
```

**Langfuse (fetched live):**
```jsonc
{
  "type": "langfuse",
  "base_url":   "https://cloud.langfuse.com",
  "public_key": "pk-...",
  "secret_key": "sk-...",              // stripped before persisting to MongoDB
  "from_timestamp": "2024-01-01T00:00:00Z",  // ISO-8601; fetch traces after this
  "page_size": 100,                    // optional, 1–500, default 100
  "max_pages": 20,                     // optional, 1–100, default 20
  "include_observations": true         // optional, default true
}
```

### Response — 202 Accepted

```jsonc
{
  "status":   "accepted",
  "task_id":  "550e8400-e29b-41d4-a716-446655440000",
  "poll_url": "/api/v1/tasks/550e8400-e29b-41d4-a716-446655440000"
}
```

### Error responses

| Code | `error_code` | Cause |
|---|---|---|
| 409 | `TASK_ALREADY_ACTIVE` | A PENDING or RUNNING task already exists for this `(agent_id, experiment_id, run_id)` |

---

## GET /api/v1/tasks/{task_id}

Poll the status of a bucketing-extraction task.

### Response — 200 OK

```jsonc
{
  "task_id":       "550e8400-...",
  "agent_id":      "my-agent",
  "experiment_id": "exp-001",
  "run_id":        "run-42",
  "status":        "PENDING | RUNNING | COMPLETED | FAILED",
  "stage":         "pending | acquiring_trace | running_pipeline | done",
  "created_at":    "2024-01-15T10:00:00Z",
  "updated_at":    "2024-01-15T10:01:30Z",
  "started_at":    "2024-01-15T10:00:05Z",
  "completed_at":  "2024-01-15T10:01:30Z",
  "request":       { /* original request snapshot — secret_key redacted */ },

  // Set when status == "COMPLETED"
  "result": {
    "total_observations": 142,
    "total_faults_detected": 3,
    "faults": [
      {
        "fault_id":    "fault-abc123",
        "fault_name":  "pod-cpu-hog",
        "severity":    "compute",
        "status":      "closed | open",
        "detected_at": "2024-01-15T10:00:30Z",
        "mitigated_at":"2024-01-15T10:01:00Z"
      }
    ],
    "storage_paths": {
      "traces_dir":      "workspace/my-agent/exp-001/fault-bucketing/run-42/traces/",
      "fault_buckets_dir":"workspace/my-agent/exp-001/fault-bucketing/run-42/fault_buckets/",
      "metrics_dir":     "workspace/my-agent/exp-001/fault-bucketing/run-42/metrics/",
      "summary":         "workspace/my-agent/exp-001/fault-bucketing/run-42/pipeline_summary.json",
      "log":             "workspace/my-agent/exp-001/fault-bucketing/run-42/pipeline.log"
    },
    "token_usage": {
      "bucketing_input_tokens":  1200,
      "bucketing_output_tokens": 400,
      "extraction_input_tokens": 3500,
      "extraction_output_tokens":800,
      "total_tokens":            5900
    },
    "processing_time_seconds": 87.4
  },

  // Set when status == "FAILED"
  "error": {
    "error_code":   "TRACE_NOT_FOUND | PIPELINE_FAILED | STORAGE_ERROR",
    "message":      "human-readable message",
    "failed_stage": "acquiring_trace | running_pipeline",
    "detail":       "full traceback"
  }
}
```

### Stage progression

```
pending  →  acquiring_trace  →  running_pipeline  →  done (COMPLETED)
                                                  ↘  FAILED
```

### Error responses

| Code | `error_code` | Cause |
|---|---|---|
| 404 | `TASK_NOT_FOUND` | No task with this `task_id` |

---

## POST /api/v1/aggregation-certification

Aggregates metrics across N runs (Phase 2) and builds a 12-section certification report (Phase 3). Takes as input the directory written by Phase 0+1 jobs.

### Request body

```jsonc
{
  "agent_id":             "string",   // required, 1–128 chars
  "agent_name":           "string",   // required, 1–256 chars — written into the report
  "experiment_id":        "string",   // required, 1–128 chars
  "certification_run_id": "string",   // optional — e.g. git SHA; stored in MongoDB
  "runs_per_fault":       30,         // optional, 1–1000, default 30
  "storage_config": {                 // optional
    "type":        "local",           // only "local" supported
    "metrics_dir": ""                 // optional — auto-derived if omitted
  }
}
```

**`metrics_dir` auto-derivation**: when `storage_config.metrics_dir` is empty (the default), the router derives it as:
```
workspace/{agent_id}/{experiment_id}/fault-bucketing/
```
This picks up every `*metrics.json` file across all `{run_id}` subdirectories for the experiment.

Supply `metrics_dir` explicitly only when the metrics files live outside the default workspace.

### Response — 202 Accepted

```jsonc
{
  "status":       "accepted",
  "cert_task_id": "7c4a8d64-...",
  "poll_url":     "/api/v1/cert-tasks/7c4a8d64-..."
}
```

### Error responses

| Code | `error_code` | Cause |
|---|---|---|
| 400 | `INVALID_REQUEST` | `storage_config.type` is not `"local"` |
| 400 | `METRICS_NOT_FOUND` | `metrics_dir` does not exist or contains no files matching `agent_id` |
| 409 | `TASK_ALREADY_ACTIVE` | A PENDING or RUNNING cert task exists for this `(agent_id, experiment_id)` |
| 500 | `MONGODB_ERROR` | Failed to create the task session document |

---

## GET /api/v1/cert-tasks/{cert_task_id}

Poll the status of an aggregation-certification task.

### Response — 200 OK

```jsonc
{
  "cert_task_id":         "7c4a8d64-...",
  "agent_id":             "my-agent",
  "agent_name":           "My Agent v1.0",
  "experiment_id":        "exp-001",
  "certification_run_id": "v1.0.0",
  "status":               "PENDING | RUNNING | COMPLETED | FAILED",
  "stage":                "pending | fetching_metrics | running_pipeline | storing_metadata | done",
  "created_at":           "2024-01-15T12:00:00Z",
  "updated_at":           "2024-01-15T12:08:45Z",
  "started_at":           "2024-01-15T12:00:10Z",
  "completed_at":         "2024-01-15T12:08:45Z",
  "request":              { /* original request snapshot */ },

  // Set when status == "COMPLETED"
  "result": {
    "total_documents":        90,
    "total_fault_categories": 3,
    "fault_categories":       ["compute", "network", "storage"],
    "certification_id":       "d290f1ee-...",   // UUID linking MongoDB docs
    "storage_paths": {
      "aggregated_scorecard": "workspace/cert/my-agent/exp-001/aggregation/aggregation.json",
      "certification_report": "workspace/cert/my-agent/exp-001/cert-builder/certification.json",
      "summary":              "workspace/cert/my-agent/exp-001/pipeline_summary.json"
    },
    "processing_time_seconds": 312.6
  },

  // Set when status == "FAILED"
  "error": {
    "error_code":   "AGGREGATION_FAILED | CERT_GENERATION_FAILED | STORAGE_ERROR | PIPELINE_FAILED | METRICS_NOT_FOUND",
    "message":      "human-readable message",
    "failed_stage": "fetching_metrics | running_pipeline | storing_metadata",
    "detail":       "full traceback"
  }
}
```

### Stage progression

```
pending  →  fetching_metrics  →  running_pipeline  →  storing_metadata  →  done (COMPLETED)
                                                                         ↘  FAILED
```

### Error responses

| Code | `error_code` | Cause |
|---|---|---|
| 404 | `TASK_NOT_FOUND` | No task with this `cert_task_id` |

---

## Complete Usage Example

```bash
# ── Step 1: Run Phase 0+1 for each agent run ──────────────────────────────────
TASK=$(curl -s -X POST http://localhost:8000/api/v1/bucketing-extraction \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id":      "k8s-agent-v2",
    "experiment_id": "chaos-exp-may",
    "run_id":        "run-001",
    "trace_source": {
      "type":      "file",
      "file_path": "/workspace/traces/run001.json"
    },
    "storage_config": { "type": "local" }
  }')

TASK_ID=$(echo $TASK | jq -r '.task_id')

# ── Step 2: Poll until COMPLETED ──────────────────────────────────────────────
while true; do
  STATUS=$(curl -s http://localhost:8000/api/v1/tasks/$TASK_ID | jq -r '.status')
  echo "Status: $STATUS"
  [ "$STATUS" = "COMPLETED" ] || [ "$STATUS" = "FAILED" ] && break
  sleep 10
done

# ── Step 3: (repeat Step 1 for run-002, run-003, ... run-N) ───────────────────

# ── Step 4: Submit Phase 2+3 after all runs are complete ─────────────────────
CERT=$(curl -s -X POST http://localhost:8000/api/v1/aggregation-certification \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id":             "k8s-agent-v2",
    "agent_name":           "K8s Agent v2.0",
    "experiment_id":        "chaos-exp-may",
    "certification_run_id": "v2.0.0-rc1",
    "runs_per_fault":       30
  }')

CERT_ID=$(echo $CERT | jq -r '.cert_task_id')

# ── Step 5: Poll until COMPLETED ──────────────────────────────────────────────
while true; do
  RESULT=$(curl -s http://localhost:8000/api/v1/cert-tasks/$CERT_ID)
  STATUS=$(echo $RESULT | jq -r '.status')
  echo "Status: $STATUS"
  [ "$STATUS" = "COMPLETED" ] || [ "$STATUS" = "FAILED" ] && break
  sleep 30
done

# ── Step 6: Read the certification report ────────────────────────────────────
REPORT_PATH=$(echo $RESULT | jq -r '.result.storage_paths.certification_report')
cat "$REPORT_PATH" | jq '.summary'
```

---

## Output File Layout

### Phase 0+1 output

Written to `workspace/{agent_id}/{experiment_id}/fault-bucketing/{run_id}/`:

```
traces/
└── raw_trace.json                     # copied/downloaded trace
fault_buckets/
└── {fault_id}_bucket.json             # one per detected fault
metrics/
├── {fault_id}_{run_id}_trace.json     # fault event slice
├── {fault_id}_{run_id}_fault_config.json
└── {fault_id}_{run_id}_metrics.json   # input for Phase 2+3
pipeline_summary.json                  # token counts, fault list
pipeline.log
```

### Phase 2+3 output

Written to `workspace/cert/{agent_id}/{experiment_id}/`:

```
aggregation/
└── aggregation.json                   # CertificationScorecard (per-category stats)
cert-builder/
└── certification.json                 # 12-section CertificationReport
pipeline_summary.json                  # agent, categories, paths
```

---

## Error Code Reference

| Error code | Meaning |
|---|---|
| `TASK_ALREADY_ACTIVE` | Duplicate submission for the same identifiers |
| `TASK_NOT_FOUND` | Poll for a non-existent task_id |
| `TRACE_NOT_FOUND` | File path does not exist or Langfuse fetch failed |
| `PIPELINE_FAILED` | Unclassified error inside Phase 0 or Phase 1 |
| `STORAGE_ERROR` | Filesystem write or MongoDB write failure |
| `METRICS_NOT_FOUND` | No `*metrics.json` files matched the given `agent_id` |
| `AGGREGATION_FAILED` | Error inside Phase 2 (aggregator / LLM Council) |
| `CERT_GENERATION_FAILED` | Error inside Phase 3 (certification report builder) |
| `INVALID_REQUEST` | Unsupported `storage_config.type` or illegal path segment |
| `MONGODB_ERROR` | Failed to create the task session document |
