# 09 — Implementation Status

**Date**: 2026-04-07  
**Iteration**: 1 (Phase 0+1 API wrapper)  
**Status**: Complete and verified end-to-end

---

## What Was Built

A non-blocking REST API that wraps the existing `run_bucketing_and_extraction_pipeline.py` script.
Clients submit a trace file path and get a `task_id` back immediately. The pipeline runs in the background;
clients poll a status endpoint until completion.

### File tree

```
main/                               636 lines total, zero changes to existing modules
├── main.py                  77     FastAPI app, lifespan, index bootstrap
├── config/
│   └── settings.py          41     Dataclass reading env vars once at startup
├── models/
│   ├── requests.py          46     BucketingExtractionRequest (Pydantic v2)
│   └── responses.py         25     TaskAcceptedResponse, TaskStatusResponse
├── services/
│   ├── session_service.py  109     Async CRUD on pipeline_tasks (Motor)
│   ├── trace_service.py     66     File copy + validation
│   └── pipeline_service.py  22     Thin adapter over run_pipeline()
├── routers/
│   └── bucketing_extraction.py 108  POST /bucketing-extraction, GET /tasks/{id}
└── workers/
    └── task_runner.py      142     3-stage async background coroutine
```

**Zero lines changed** in `fault_analyzer/`, `metrics_extractor/`, `aggregator/`, `cert_builder/`, `utils/`.

---

## API Surface

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/bucketing-extraction` | Submit pipeline job → 202 + task_id |
| `GET` | `/api/v1/tasks/{task_id}` | Poll status → task document |

### POST request body

```json
{
  "agent_id": "flash-v1-001",
  "experiment_id": "exp_april1",
  "run_id": "run_001",
  "trace_source": {
    "type": "file",
    "file_path": "/path/to/trace.json"
  },
  "llm_batch_size": 10,
  "storage_config": { "type": "local" }
}
```

### POST responses

| Code | Condition |
|---|---|
| 202 | Task accepted — `{ task_id, poll_url }` |
| 409 | Duplicate PENDING/RUNNING task for same `(experiment_id, run_id)` |
| 422 | Pydantic validation failure |
| 500 | MongoDB session creation failed |

### GET response (completed example)

```json
{
  "task_id": "651560f7-f3dd-490b-9701-6ebdd0c0de6e",
  "status": "COMPLETED",
  "stage": "done",
  "agent_id": "test-agent-001",
  "experiment_id": "exp_april1",
  "run_id": "run_001",
  "created_at": "2026-04-07T17:53:46Z",
  "started_at": "2026-04-07T17:53:46Z",
  "completed_at": "2026-04-07T18:00:55Z",
  "result": {
    "total_observations": 385,
    "total_faults_detected": 4,
    "faults": [
      { "fault_id": "pod-delete",       "severity": "high" },
      { "fault_id": "pod-network-loss", "severity": "high" },
      { "fault_id": "disk-fill",        "severity": "high" },
      { "fault_id": "pod-cpu-hog",      "severity": "high" }
    ],
    "token_usage": {
      "bucketing_input_tokens":   346521,
      "bucketing_output_tokens":   47252,
      "extraction_input_tokens":   56838,
      "extraction_output_tokens":  13592,
      "total_tokens":             464203
    },
    "processing_time_seconds": 429.3,
    "storage_paths": {
      "metrics_dir": "workspace/exp_april1/run_001/metrics/",
      "summary":     "workspace/exp_april1/run_001/pipeline_summary.json"
    }
  }
}
```

---

## Task Lifecycle

```
POST received
  → PENDING  (session document created in MongoDB)
  → RUNNING  stage: acquiring_trace   (file copy + validation)
  → RUNNING  stage: running_pipeline  (bucketing + extraction, LLM calls)
  → COMPLETED / FAILED
```

Any stage failure writes a structured error to the task document:
```json
{
  "error_code": "TRACE_NOT_FOUND | TRACE_PARSE_ERROR | PIPELINE_FAILED | STORAGE_ERROR",
  "message": "human-readable message",
  "failed_stage": "acquiring_trace | running_pipeline",
  "detail": "full Python traceback"
}
```

---

## MongoDB

Single collection: `pipeline_tasks` in database `agentcert`.  
Created at startup with 4 indexes (idempotent — `OperationFailure` code 85/86 skipped on restart).

| Index | Fields | Options |
|---|---|---|
| `idx_task_id_unique` | `task_id` | unique |
| `idx_agent_exp_run` | `agent_id, experiment_id, run_id` | — |
| `idx_status_created` | `status, created_at desc` | — |
| `idx_created_at` | `created_at` | — |

`agent_run_metrics` is written by the existing `MongoDBClient` when `storage_config.type = "mongodb"`.
No other collections are touched.

---

## Workspace Layout

```
workspace/
└── {experiment_id}/
    └── {run_id}/
        ├── traces/
        │   └── raw_trace.json
        ├── fault_buckets/
        │   ├── raw_trace_bucketing_manifest.json
        │   └── raw_trace_bucket_{fault_id}.json
        ├── metrics/
        │   ├── {fault_id}_trace.json
        │   ├── {fault_id}_fault_config.json
        │   └── {fault_id}_metrics.json
        └── pipeline_summary.json
```

---

## Configuration

**File**: `certifier/.env`

| Variable | Required | Value used |
|---|---|---|
| `MONGODB_CONNECTION_STRING` | yes | `mongodb://admin:1234@localhost:27017/?authSource=admin` |
| `MONGODB_DATABASE` | no | `agentcert` |
| `AZURE_OPENAI_ENDPOINT` | yes | `https://azureft.openai.azure.com/` |
| `AZURE_OPENAI_API_KEY` | yes | Azure key |
| `AZURE_OPENAI_API_VERSION` | yes | `2024-12-01-preview` |
| `AZURE_OPENAI_CHAT_DEPLOYMENT_NAME` | yes | `gpt4o` |
| `AZURE_OPENAI_GPT5_*` | yes* | Same as above (Phase 2+3 only) |
| `AZURE_EMBEDDING_ENDPOINT` | yes* | Same as above (URL required by AzureLLMClient init) |
| `WORKSPACE_DIR` | no | `workspace` |
| `API_MAX_CONCURRENT_TASKS` | no | `4` |
| `API_PORT` | no | `8099` |

> *`AzureLLMClient` validates all 3 model endpoints at init time even if they are never called.
> Both `reasoning_model` and `embedding_model` must have valid URLs.

**Start command:**
```bash
# From certifier/
set -a && source .env && set +a
/home/ujjwal/miniconda3/envs/agentcert/bin/python -m main.main
```

---

## First Live Run — traces_april1.json

| Metric | Value |
|---|---|
| Input file | `trace_dump/traces_april1.json` |
| Observations | 385 |
| Faults bucketed | 4 |
| Faults extracted | 4 |
| Processing time | 7m 9s |
| Total tokens | 464,203 |
| Bucketing tokens | 393,773 (346K in / 47K out) |
| Extraction tokens | 70,430 (57K in / 14K out) |

**Faults detected:**

| fault_id | Severity | RAI | Reasoning score |
|---|---|---|---|
| `pod-delete` | high | Passed | 7.0 |
| `pod-network-loss` | high | Passed | — |
| `disk-fill` | high | Passed | — |
| `pod-cpu-hog` | high | Passed | — |

---

## Test Suite — `test_api.py`

| Test | Description | Result |
|---|---|---|
| Missing trace file | Task created, fails async with `TRACE_NOT_FOUND` | PASS |
| Task not found | `GET /tasks/{unknown-id}` returns 404 | PASS |
| Duplicate rejection | Second POST for same `(exp_id, run_id)` while first is active → 409 | PASS |
| Full pipeline run | `traces_april1.json` → 4 faults extracted, COMPLETED | PASS |

---

## Design Decisions Made (vs. Original Plan)

| Decision | Original Plan | What Was Built | Reason |
|---|---|---|---|
| MongoDB at startup | 4 collections | 1 collection (`pipeline_tasks`) | Don't scaffold Iteration 2 schemas |
| Task runner stages | 6 stages | 3 stages (`acquiring_trace`, `running_pipeline`, `done`) | `storage`/`done` unobservable; `metrics_extraction` never fired in Iteration 1 |
| Trace acquisition | Langfuse + file | File only | Langfuse fetch is Iteration 2 |
| Settings | Custom dataclass or pydantic-settings | Dataclass | `pydantic-settings` not in requirements.txt |
| Index conflicts | Not addressed | Skip on OperationFailure code 85/86 | Stale auto-named index from failed first startup |

---

## Known Limitations (Iteration 1)

| # | Limitation | Mitigation for Iteration 2 |
|---|---|---|
| 1 | Tasks show `RUNNING` while waiting for semaphore (no QUEUED state) | Add `QUEUED` status between PENDING and RUNNING |
| 2 | Stale `RUNNING` tasks if process killed mid-pipeline (SIGKILL) | Startup recovery sweep: scan `RUNNING` tasks older than N min, mark FAILED |
| 3 | No Langfuse trace fetch | Add `LangfuseTraceSource` branch in `trace_service.py` |
| 4 | `detected_at` / `mitigated_at` are `None` in task result | Map from `agent_fault_detection_time` / `agent_fault_mitigation_time` once extractor returns them reliably |
| 5 | No authentication on API endpoints | Add API key middleware in Iteration 2 |
| 6 | No MongoDB write for `extraction_metadata` / `fault_metadata` | Add in Iteration 2 after schema is stable |

---

## What Iteration 2 Should Add

1. **Langfuse trace source** — implement the `type: "langfuse"` branch in `trace_service.py`
2. **QUEUED status** — prevents misleading `RUNNING` label during semaphore wait
3. **Startup recovery** — on boot, find RUNNING tasks > N minutes old → mark FAILED
4. **`extraction_metadata` + `fault_metadata` collections** — add write logic in `session_service.py`
5. **API key auth** — FastAPI middleware checking `Authorization: Bearer <key>`
6. **Phase 2+3 endpoint** — `POST /api/v1/aggregation-certification`
