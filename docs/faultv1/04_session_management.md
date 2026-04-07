# 04 — Session Management

## Responsibility

`main/services/session_service.py` owns all reads and writes to the `pipeline_tasks` MongoDB
collection. Every other component interacts with task state exclusively through this service.
No other module accesses `pipeline_tasks` directly.

---

## Task Lifecycle — State Machine

```
                  ┌─────────┐
   POST received  │         │
  ─────────────► │ PENDING  │
                  │         │
                  └────┬────┘
                       │  background worker acquires semaphore
                       ▼
                  ┌─────────┐
                  │         │
                  │ RUNNING  │
                  │         │
                  └────┬────┘
                  /         \
       pipeline succeeds    any exception
              │                   │
              ▼                   ▼
        ┌──────────┐        ┌────────┐
        │ COMPLETED │        │ FAILED │
        └──────────┘        └────────┘
```

Terminal states are `COMPLETED` and `FAILED`. Once a task enters a terminal state its document
is never updated again.

Valid transitions:

| From | To | Trigger |
|---|---|---|
| — | PENDING | `create_task()` called by POST handler |
| PENDING | RUNNING | `set_started()` called by task runner |
| RUNNING | COMPLETED | `set_completed()` called by task runner on success |
| RUNNING | FAILED | `set_failed()` called by task runner on exception |

No other transitions are valid. `session_service` must enforce this: `set_completed` and
`set_failed` must filter on `{"task_id": ..., "status": "RUNNING"}` and raise if no document
matches (guards against double-writes on a race).

---

## PipelineTask Document Schema

```json
{
  "_id": "ObjectId",
  "task_id": "uuid-v4-string",

  "agent_id": "agent_v2_4_1",
  "experiment_id": "exp_001",
  "run_id": "run_001",

  "status": "PENDING | RUNNING | COMPLETED | FAILED",
  "stage":  "pending | trace_fetch | validation | bucketing | metrics_extraction | storage | done",

  "created_at":   "2026-04-07T10:00:00.000Z",
  "updated_at":   "2026-04-07T10:00:00.000Z",
  "started_at":   "2026-04-07T10:00:05.000Z | null",
  "completed_at": "2026-04-07T10:01:02.000Z | null",

  "request": {
    "trace_source": {
      "type": "langfuse | file",
      "file_path": "string | null",
      "base_url": "string | null",
      "from_timestamp": "ISO-8601 | null"
    },
    "llm_batch_size": 5,
    "storage_config": {
      "type": "blob_storage | mongodb | hybrid",
      "container_name": "cert-artifacts"
    }
  },

  "result": {
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
      "traces_dir":       "workspace/exp_001/run_001/traces/",
      "fault_buckets_dir":"workspace/exp_001/run_001/fault_buckets/",
      "metrics_dir":      "workspace/exp_001/run_001/metrics/",
      "summary":          "workspace/exp_001/run_001/pipeline_summary.json",
      "log":              "workspace/exp_001/run_001/pipeline.log"
    },
    "token_usage": {
      "bucketing_input_tokens":    4250,
      "bucketing_output_tokens":   1100,
      "extraction_input_tokens":   8500,
      "extraction_output_tokens":  2200,
      "total_tokens":              16050
    },
    "processing_time_seconds": 72.4
  },

  "error": {
    "error_code":   "BUCKETING_FAILED",
    "message":      "LLM classifier returned empty response on batch 3",
    "failed_stage": "bucketing",
    "detail":       "Traceback (most recent call last): ..."
  }
}
```

### Field Notes

- `result` is `null` until `set_completed()` writes it.
- `error` is `null` unless `set_failed()` writes it.
- `stage` advances independently of `status`; it carries the last stage that was entered, making
  it useful for progress display.
- `request.trace_source.secret_key` is **not stored** in the document. Credentials are
  use-once at fetch time and must not be persisted.
- `storage_paths` uses relative paths from `certifier/` root for portability. Absolute paths
  depend on the deployment environment.

---

## MongoDB Indexes

Created at startup in `main/main.py` (see `03_app_startup.md`):

```
pipeline_tasks:
  idx_task_id_unique          {task_id: 1}  unique
  idx_agent_exp_run           {agent_id: 1, experiment_id: 1, run_id: 1}
  idx_status_created          {status: 1, created_at: -1}
  idx_created_at              {created_at: 1}
```

The compound index on `(experiment_id, run_id)` supports the duplicate-submission
check: `find_one({experiment_id, run_id, status: {$in: ["PENDING","RUNNING"]}})`.
`agent_id` is stored in the document for querying/reporting but is not part of the workspace
path or the uniqueness key.

---

## Service Interface

```python
# main/services/session_service.py

from motor.motor_asyncio import AsyncIOMotorCollection
from datetime import datetime, timezone
from typing import Optional

class SessionService:
    def __init__(self, collection: AsyncIOMotorCollection):
        self._col = collection

    async def create_task(
        self,
        task_id: str,
        agent_id: str,
        experiment_id: str,
        run_id: str,
        request_snapshot: dict,   # serialised BucketingExtractionRequest (no secret_key)
    ) -> None:
        """Insert a new PENDING task document. Raises DuplicateKeyError on task_id collision."""

    async def set_started(self, task_id: str) -> None:
        """Transition PENDING → RUNNING. Sets started_at and stage='trace_fetch'."""

    async def update_stage(self, task_id: str, stage: str) -> None:
        """Update stage and updated_at. Called between pipeline steps."""

    async def set_completed(self, task_id: str, result: dict) -> None:
        """
        Transition RUNNING → COMPLETED.
        Writes result dict and completed_at.
        Raises ValueError if document is not in RUNNING state (guard against double-write).
        """

    async def set_failed(
        self,
        task_id: str,
        error_code: str,
        message: str,
        failed_stage: str,
        detail: str,
    ) -> None:
        """
        Transition RUNNING → FAILED.
        Writes error dict and completed_at.
        Safe to call even if status is PENDING (handles early failures before set_started).
        """

    async def get_task(self, task_id: str) -> Optional[dict]:
        """Return the full task document or None if not found."""

    async def find_active_task(
        self, experiment_id: str, run_id: str
    ) -> Optional[dict]:
        """
        Return a PENDING or RUNNING task for this (experiment_id, run_id) pair.
        Used to reject duplicate submissions that would collide on the workspace path.
        """
```

### All writes use `$set` + `$currentDate`

```python
# Example: set_started
await self._col.update_one(
    {"task_id": task_id, "status": "PENDING"},
    {
        "$set": {"status": "RUNNING", "stage": "trace_fetch"},
        "$currentDate": {"started_at": True, "updated_at": True},
    },
)
```

Using `$currentDate` (server-side) instead of `datetime.utcnow()` (client-side) avoids clock
skew issues when multiple API instances run against the same MongoDB.

---

## Duplicate Submission Guard

Before creating a task the POST handler must call `find_active_task()`. If an active task exists
for the same `(experiment_id, run_id)` pair, return HTTP 409 Conflict:

```json
{
  "status": "error",
  "error_code": "TASK_ALREADY_ACTIVE",
  "message": "A pipeline task is already running for this run",
  "details": {
    "task_id": "existing-task-uuid",
    "status": "RUNNING",
    "stage": "bucketing"
  }
}
```

This guard prevents the workspace directory collision described in `00_overview.md` (constraint 3).
