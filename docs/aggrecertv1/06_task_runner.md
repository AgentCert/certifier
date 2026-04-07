# 06 — Task Runner & Concurrency Model

## Responsibility

`main/workers/cert_task_runner.py` contains the single async coroutine `run_cert_task()`.
It is the background worker that executes all pipeline stages for one submitted certification
task. It has no HTTP concerns — it reads from services, updates session state, calls the
pipeline, and writes MongoDB metadata on completion.

---

## Concurrency Architecture

```
uvicorn ASGI process
│
├─ asyncio event loop (single thread)
│   │
│   ├─ Request handler (coroutine)
│   │   └─ POST /aggregation-certification
│   │       ├─ awaits cert_session_service.create_task()    [motor: non-blocking]
│   │       ├─ schedules run_cert_task() as BackgroundTask
│   │       └─ returns 202 immediately
│   │
│   └─ BackgroundTask pool (coroutines, not threads)
│       ├─ run_cert_task(task_1)  ──►  awaits cert_semaphore.acquire()
│       └─ run_cert_task(task_2)  ──►  awaits cert_semaphore.acquire()  (blocks if full)
│
└─ ThreadPoolExecutor (asyncio default, used via asyncio.to_thread)
    └─ metrics file I/O (json.load on *metrics.json files)
```

**Key points:**

- `run_cert_task` is `async` — runs on the event loop, not in a thread.
- `run_pipeline()` in `run_aggregation_and_certification_pipeline.py` is `async def` and uses
  `await` internally for all LLM calls. It is awaited directly from `run_cert_task`; no thread
  delegation needed.
- The dedicated `cert_semaphore` (`max_concurrent_cert_tasks`, default 2) is separate from
  the faultv1 semaphore to prevent Phase 2+3 workloads from starving Phase 0+1 tasks.
- Tasks acquiring the cert_semaphore show `status=RUNNING`, `stage=fetching_metrics` while
  waiting. This is acceptable for iteration 1.

---

## Stage Progression

```
run_cert_task(cert_task_id, request, cert_session_svc, cert_pipeline_svc, cert_semaphore,
              cert_meta_col, agg_cat_col, settings, app_config)
│
├── await cert_session_svc.set_started(cert_task_id)
│     → status: PENDING → RUNNING, stage: "fetching_metrics"
│
├── [STAGE: fetching_metrics]  ─────────────────────────────────────────────
│   async with cert_semaphore:
│     start_time = time.monotonic()
│     results = await cert_pipeline_svc.execute_pipeline(
│         metrics_dir=request.storage_config.metrics_dir,
│         output_dir=str(cert_output_dir),
│         agent_id=request.agent_id,
│         agent_name=request.agent_name,
│         certification_run_id=request.certification_run_id,
│         runs_per_fault=request.runs_per_fault,
│         config=app_config,
│     )
│     elapsed = time.monotonic() - start_time
│   Note: run_pipeline() internally calls DirectoryQueryService, AggregationOrchestrator,
│         and CertificationPipeline. The stage is set to "fetching_metrics" before the call;
│         "running_pipeline" is set inside the pipeline (not observable externally).
│         In iteration 1 the entire pipeline is an opaque single call.
│
│   on empty return (no metrics found for agent_id):
│     → set_failed(cert_task_id, "METRICS_NOT_FOUND", ...)
│     → return
│
│   on any Exception:
│     → set_failed(cert_task_id, classify_cert_error(exc), ...)
│     → return
│
├── await cert_session_svc.update_stage(cert_task_id, "storing_metadata")
│
├── [STAGE: storing_metadata]  ─────────────────────────────────────────────
│   certification_id = str(uuid.uuid4())
│   Read pipeline_summary.json from cert_output_dir
│   Write CertificationMetadata document to cert_meta_col
│   Write N AggregatedCategoryMetadata documents to agg_cat_col
│     (one per fault_category in aggregated scorecard)
│   on MotorError / IOError:
│     → set_failed(cert_task_id, "STORAGE_ERROR", ...)
│     → return
│
└── await cert_session_svc.set_completed(cert_task_id, result_dict)
      → status: RUNNING → COMPLETED, stage: "done"
```

### Stage update inside the semaphore block

The `run_pipeline()` call handles both aggregation and certification internally. In iteration 1,
we cannot update stage from `fetching_metrics` to `running_pipeline` mid-pipeline because the
call is opaque. The stage visible to pollers during the entire pipeline run is `fetching_metrics`.
Iteration 2 can split the call or add an internal callback to update stage.

---

## `cert_pipeline_service.execute_pipeline` Signature

```python
# main/services/cert_pipeline_service.py

from run_aggregation_and_certification_pipeline import run_pipeline

async def execute_pipeline(
    metrics_dir: str,
    output_dir: str,
    agent_id: str,
    agent_name: str,
    certification_run_id: str,
    runs_per_fault: int,
    config: dict,
) -> dict:
    """
    Thin adapter over run_pipeline() from run_aggregation_and_certification_pipeline.py.
    Returns the final certification report dict (or {} on empty aggregation).
    Any exception propagates to the caller (cert_task_runner).
    """
    return await run_pipeline(
        metrics_dir=metrics_dir,
        output_dir=output_dir,
        agent_id=agent_id,
        agent_name=agent_name,
        certification_run_id=certification_run_id,
        runs_per_fault=runs_per_fault,
        config=config,
    )
```

`run_pipeline()` returns the final certification report dict on success, or `{}` if no metrics
were found for the `agent_id`. The cert task runner checks for the empty-dict case and calls
`set_failed(METRICS_NOT_FOUND)` rather than marking the task completed with empty data.

---

## Error Classification

```python
def classify_cert_error(exc: Exception) -> str:
    """Map an exception to a structured error_code."""
    msg = str(exc).lower()
    if "aggregat" in msg or "council" in msg or "scorecard" in msg:
        return "AGGREGATION_FAILED"
    if "certif" in msg or "cert_builder" in msg or "report" in msg:
        return "CERT_GENERATION_FAILED"
    if "storage" in msg or isinstance(exc, OSError):
        return "STORAGE_ERROR"
    return "PIPELINE_FAILED"
```

The full traceback (via `traceback.format_exc()`) is always included in the `detail` field.

---

## Workspace Path Resolution

```python
def resolve_cert_output_dir(
    cert_workspace_dir: Path, agent_id: str, experiment_id: str
) -> Path:
    """
    Returns workspace/cert/{agent_id}/{experiment_id}/.
    Creates directory tree if it does not exist.
    Raises ValueError if agent_id or experiment_id contain path separators.
    """
    for segment in (agent_id, experiment_id):
        if "/" in segment or "\\" in segment or ".." in segment:
            raise ValueError(f"Path segment contains illegal characters: {segment!r}")
    path = cert_workspace_dir / agent_id / experiment_id
    path.mkdir(parents=True, exist_ok=True)
    return path
```

The path separator check prevents directory traversal. Both `agent_id` and `experiment_id`
come from the API request and must be sanitised before use as filesystem path components.

---

## Result Dict Built from `pipeline_summary.json`

`run_pipeline()` writes `pipeline_summary.json` to `output_dir`. Its structure:

```json
{
  "agent_id": "agent_v2_4_1",
  "agent_name": "Agent V2.4.1",
  "certification_run_id": "cert_run_001",
  "metrics_dir": "/absolute/path/to/metrics",
  "total_documents": 120,
  "total_fault_categories": 3,
  "fault_categories": ["compute", "network", "storage"],
  "aggregated_scorecard_path": "workspace/cert/agent_v2_4_1/exp_001/aggregated_scorecard_output_agent_v2_4_1.json",
  "certification_report_path": "workspace/cert/agent_v2_4_1/exp_001/certification_report_agent_v2_4_1.json"
}
```

The cert task runner reads this file after the pipeline completes and assembles the `result`
dict stored in the `certification_tasks` document:

```json
{
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
}
```

`certification_id` is generated by the cert task runner — it links the task document to the
downstream `certification_metadata` and `aggregated_category_metadata` MongoDB documents.

---

## MongoDB Metadata Writes (storing_metadata stage)

After `run_pipeline()` completes, the cert task runner writes two types of MongoDB documents:

### 1. CertificationMetadata (one per cert task)

```python
cert_meta_doc = {
    "certification_id": certification_id,
    "cert_task_id": cert_task_id,
    "agent_id": agent_id,
    "agent_name": agent_name,
    "experiment_id": experiment_id,
    "certification_run_id": certification_run_id,
    "status": "success",
    "created_at": datetime.now(timezone.utc),
    "storage_paths": {
        "aggregated_scorecard": str(scorecard_path),
        "certification_report": str(report_path),
        "summary": str(summary_path),
    },
    "summary": {
        "total_documents": total_documents,
        "total_fault_categories": total_fault_categories,
        "fault_categories": fault_categories,
    },
    "processing_time_seconds": elapsed,
    "error_message": None,
}
await cert_meta_col.insert_one(cert_meta_doc)
```

### 2. AggregatedCategoryMetadata (one per fault category)

These are read from the `aggregated_scorecard_output_{agent_id}.json` file after the pipeline
completes. One document per `fault_category_scorecard` entry in the scorecard:

```python
for sc in aggregated_scorecard.get("fault_category_scorecards", []):
    agg_cat_doc = {
        "fault_category": sc["fault_category"],
        "certification_id": certification_id,
        "agent_id": agent_id,
        "experiment_id": experiment_id,
        "total_runs": sc.get("total_runs", 0),
        "faults_tested": sc.get("faults_tested", []),
        "numeric_metrics": sc.get("numeric_metrics", {}),
        "derived_metrics": sc.get("derived_metrics", {}),
        "created_at": datetime.now(timezone.utc),
    }
    await agg_cat_col.insert_one(agg_cat_doc)
```

Both writes run sequentially after pipeline completion. A failure in either write triggers
`set_failed("STORAGE_ERROR", ...)` — the pipeline artifacts are already on disk, so the
failure is non-destructive and the files can be recovered manually.

---

## Graceful Shutdown Behaviour

Identical to faultv1. FastAPI awaits all pending `BackgroundTasks` before the lifespan context
exits. Phase 2+3 runs can take 5–15 minutes; set `--timeout-graceful-shutdown 600` (10 min)
for production deployments handling large experiments.
