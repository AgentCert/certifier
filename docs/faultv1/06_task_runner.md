# 06 — Task Runner & Concurrency Model

## Responsibility

`main/workers/task_runner.py` contains the single async coroutine `run_task()`. It is the
background worker that executes all pipeline stages for one submitted task. It has no HTTP
concerns — it only reads from services, updates session state, and calls the pipeline.

---

## Concurrency Architecture

```
uvicorn ASGI process
│
├─ asyncio event loop (single thread)
│   │
│   ├─ Request handler (coroutine)
│   │   └─ POST /bucketing-extraction
│   │       ├─ awaits session_service.create_task()     [motor: non-blocking]
│   │       ├─ schedules run_task() as BackgroundTask
│   │       └─ returns 202 immediately
│   │
│   └─ BackgroundTask pool (coroutines, not threads)
│       ├─ run_task(task_1)  ──►  awaits semaphore.acquire()
│       ├─ run_task(task_2)  ──►  awaits semaphore.acquire()
│       └─ run_task(task_3)  ──►  awaits semaphore.acquire()  (blocks if semaphore=0)
│
└─ ThreadPoolExecutor (asyncio default, used via asyncio.to_thread)
    ├─ Langfuse SDK calls (synchronous)          → run in thread
    ├─ Local file I/O (shutil.copy, json.load)   → run in thread
    └─ run_pipeline() (async but spawns sub-tasks) → awaited directly on event loop
```

### Key Points

**BackgroundTasks are coroutines, not threads.** FastAPI's `BackgroundTasks.add_task()` accepts
both sync functions (run in thread pool) and async functions (awaited on the event loop). Since
`run_task` is async, it runs on the event loop. All blocking operations inside it **must** be
delegated to `asyncio.to_thread()`.

**`run_pipeline()` is already async.** The existing `run_pipeline` function in
`run_bucketing_and_extraction_pipeline.py` is `async def` and uses `await` internally for LLM
calls. It can be awaited directly from `run_task` with no thread delegation needed.

**Semaphore limits concurrent LLM load.** The semaphore (`API_MAX_CONCURRENT_TASKS`, default 4)
caps how many tasks simultaneously execute LLM-calling pipeline code. Tasks beyond this cap
remain in `PENDING` state, holding the semaphore's wait queue, until a slot opens.

> **Critical**: The semaphore is acquired *inside* `run_task`, after `set_started()` is
> called and status is `RUNNING`. This means a task transitions to `RUNNING` as soon as its
> coroutine starts, even if it's waiting for the semaphore. Callers polling the GET endpoint
> will see `RUNNING` + `stage="trace_fetch"` during the wait period. This is acceptable for
> iteration 1. In iteration 2, introduce a `QUEUED` status between `PENDING` and `RUNNING` to
> make the wait explicit.

---

## Stage Progression

```
run_task(task_id, request, session_svc, pipeline_svc, trace_svc, settings)
│
├── await session_svc.set_started(task_id)
│     → status: PENDING → RUNNING, stage: "trace_fetch"
│
├── [STAGE: trace_fetch]  ────────────────────────────────────────────────
│   await trace_svc.acquire_trace(request.trace_source, traces_dir)
│     → type="file":     asyncio.to_thread(copy_file) → raw_trace.json
│     → type="langfuse": asyncio.to_thread(fetch_and_format) → raw_trace.json
│   on TraceIngestionError:
│     → set_failed(task_id, "TRACE_NOT_FOUND" | "LANGFUSE_FETCH_ERROR" | "TRACE_PARSE_ERROR", ...)
│     → return
│
├── await session_svc.update_stage(task_id, "validation")
│
├── [STAGE: validation]  ─────────────────────────────────────────────────
│   Verify raw_trace.json exists and is a non-empty list
│   Count total_observations
│   on failure:
│     → set_failed(task_id, "TRACE_PARSE_ERROR", ...)
│     → return
│
├── await session_svc.update_stage(task_id, "bucketing")
│
├── [STAGE: bucketing + metrics_extraction]  ────────────────────────────
│   async with semaphore:
│     start_time = time.monotonic()
│     results = await pipeline_svc.execute_pipeline(
│         trace_file=str(raw_trace_path),
│         output_dir=str(run_output_dir),
│         batch_size=request.llm_batch_size,
│         store_to_mongodb=(storage_type in ("mongodb","hybrid")),
│         config=app_config,
│     )
│     elapsed = time.monotonic() - start_time
│   on any Exception:
│     → set_failed(task_id, classify_error(exc), ...)
│     → return
│
│   Note: run_pipeline() covers both bucketing and extraction in a single call.
│   Stage is set to "bucketing" before the call; no mid-pipeline stage update is
│   possible in iteration 1 (pipeline is opaque). Iteration 2 can split the call.
│
├── await session_svc.update_stage(task_id, "storage")
│
├── [STAGE: storage]  ────────────────────────────────────────────────────
│   Read pipeline_summary.json from run_output_dir
│   Build result dict:
│     - total_observations  (from validation step)
│     - total_faults_detected = len(results)
│     - faults list from results (fault_id, fault_name, severity, status, timestamps)
│     - storage_paths (relative paths under workspace/)
│     - token_usage (from pipeline_summary.json bucketing_tokens + extraction_tokens)
│     - processing_time_seconds = elapsed
│   on IOError (cannot read summary):
│     → set_failed(task_id, "STORAGE_ERROR", ...)
│     → return
│
└── await session_svc.set_completed(task_id, result_dict)
      → status: RUNNING → COMPLETED, stage: "done"
```

---

## `pipeline_service.execute_pipeline` Signature

```python
# main/services/pipeline_service.py

from run_bucketing_and_extraction_pipeline import run_pipeline  # existing, unchanged

async def execute_pipeline(
    trace_file: str,
    output_dir: str,
    batch_size: int,
    store_to_mongodb: bool,
    config: dict,
) -> list[dict]:
    """
    Thin adapter over run_pipeline().
    Returns the list of per-fault result dicts from run_pipeline().
    Any exception propagates to the caller (task_runner).
    """
    return await run_pipeline(
        trace_file=trace_file,
        output_dir=output_dir,
        batch_size=batch_size,
        store_to_mongodb=store_to_mongodb,
        config=config,
    )
```

The `config` arg is the resolved dict from `ConfigLoader.load_config()` (stored in `app.state.config`).
Passing it explicitly avoids `run_pipeline` re-reading `configs.json` from disk on every call
(which it already guards with a fallback, but explicit is better).

---

## Error Classification

```python
def classify_pipeline_error(exc: Exception, stage: str) -> str:
    """Map an exception to a structured error_code."""
    msg = str(exc).lower()
    if stage == "bucketing":
        return "BUCKETING_FAILED"
    if stage == "metrics_extraction":
        return "METRICS_EXTRACTION_FAILED"
    if "storage" in msg or isinstance(exc, OSError):
        return "STORAGE_ERROR"
    return "PIPELINE_FAILED"
```

The full traceback (via `traceback.format_exc()`) is always included in the `detail` field so
engineers can diagnose failures from the GET endpoint alone, without needing server log access.

---

## Workspace Path Resolution

```python
def resolve_run_dir(workspace_dir: Path, experiment_id: str, run_id: str) -> Path:
    """
    Returns workspace/{experiment_id}/{run_id}/.
    Creates directory tree if it does not exist.
    Raises ValueError if experiment_id or run_id contain path separators.
    """
    for segment in (experiment_id, run_id):
        if "/" in segment or "\\" in segment or ".." in segment:
            raise ValueError(f"Path segment contains illegal characters: {segment!r}")
    path = workspace_dir / experiment_id / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path
```

The path separator check prevents directory traversal — since `experiment_id` and `run_id` come
from the API request, they could otherwise escape the workspace root. `agent_id` is stored in the
task document but does not appear in the filesystem path.

---

## Result Dict Built from `pipeline_summary.json`

`run_pipeline()` writes `pipeline_summary.json` to `output_dir`. Its structure:

```json
{
  "trace_file": "raw_trace.json",
  "run_id": "run_001",
  "total_faults": 3,
  "faults_extracted": 3,
  "bucketing_tokens": {"input": 4250, "output": 1100, "total": 5350},
  "extraction_tokens": {"input": 8500, "output": 2200, "total": 10700},
  "fault_results": [
    {"fault_id": "pod-delete", "fault_name": "pod-delete", "mongodb_document_id": null}
  ]
}
```

The task runner merges this summary with the `results` list returned by `run_pipeline()` to build
the `result.faults` array. Each fault entry in the stored result contains:

```json
{
  "fault_id": "pod-delete",
  "fault_name": "pod-delete",
  "severity": "critical",
  "status": "closed",
  "detected_at": "2026-04-07T10:00:02Z",
  "mitigated_at": "2026-04-07T10:00:40Z"
}
```

`severity`, `status`, `detected_at`, and `mitigated_at` come from `results[i]["quantitative"]`
fields. If the quantitative model lacks these fields (they are nullable), the values are `null`
in the stored document.

> **Critical gap**: `run_pipeline()` does not currently return `severity`, `status`,
> `detected_at`, or `mitigated_at` at the top level of each result dict. These live inside
> `results[i]["quantitative"]` as `injected_fault_category`, `fault_detected`,
> `agent_fault_detection_time`, and `agent_fault_mitigation_time`. The task runner must map:
>
> | Task result field | Source in quantitative dict |
> |---|---|
> | `severity` | `injected_fault_category` |
> | `status` | `"closed"` if `fault_detected == "Yes"` else `"open"` |
> | `detected_at` | `agent_fault_detection_time` |
> | `mitigated_at` | `agent_fault_mitigation_time` |

---

## Graceful Shutdown Behaviour

FastAPI awaits all pending `BackgroundTasks` before the lifespan context exits (on SIGTERM/SIGINT).
This means:

- Tasks in `PENDING` state (waiting for semaphore) will proceed and complete.
- Tasks actively running the pipeline (`RUNNING`) will complete before shutdown.
- If completion takes longer than uvicorn's `--timeout-graceful-shutdown` (default: 5 s),
  uvicorn forcefully terminates the process. Surviving tasks remain `RUNNING` in MongoDB.

**Mitigation**: Set `--timeout-graceful-shutdown 300` (5 min) in the start command. Add a
startup recovery sweep in iteration 2 to detect and fail stale `RUNNING` tasks.
