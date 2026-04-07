import asyncio
import json
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List

from main.config.settings import Settings
from main.models.bucket_requests import BucketingExtractionRequest
from main.services.pipeline_service import BucketPipelineService
from main.services.session_service import SessionService
from main.services.trace_service import TraceIngestionError, TraceService


def _resolve_run_dir(workspace_dir: Path, experiment_id: str, run_id: str) -> Path:
    """Return ``workspace/{experiment_id}/{run_id}/``, creating it if needed.

    Validates each path segment against directory-traversal characters so that
    user-supplied IDs cannot escape the workspace root, even if the Pydantic
    validators were somehow bypassed upstream.
    """
    for segment in (experiment_id, run_id):
        if "/" in segment or "\\" in segment or ".." in segment:
            raise ValueError(f"Illegal path segment: {segment!r}")
    path = workspace_dir / experiment_id / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


async def run_task(
    task_id: str,
    request: BucketingExtractionRequest,
    session_svc: SessionService,
    trace_svc: TraceService,
    pipeline_svc: BucketPipelineService,
    semaphore: asyncio.Semaphore,
    settings: Settings,
    app_config: dict,
) -> None:
    """Background coroutine that drives a single bucketing-extraction task through its stages.

    Stage flow:
        PENDING → RUNNING / acquiring_trace
                → RUNNING / running_pipeline
                → COMPLETED  (on success)
                → FAILED      (on any error)

    The function never raises — all exceptions are caught and recorded as FAILED.
    """
    # Mark the task as started before any I/O so the poll endpoint reflects RUNNING immediately
    await session_svc.set_started(task_id)

    # ── Stage 1: acquiring_trace ──────────────────────────────────────────────
    # Download / copy the raw trace to the per-run workspace directory
    try:
        run_dir = _resolve_run_dir(
            settings.workspace_dir, request.experiment_id, request.run_id
        )
        trace_path, total_observations = await trace_svc.acquire_trace(
            request.trace_source, run_dir / "traces"
        )
    except TraceIngestionError as exc:
        # Structured error with a known error_code from TraceService
        await session_svc.set_failed(
            task_id, exc.error_code, str(exc), "acquiring_trace",
            traceback.format_exc(),
        )
        return
    except Exception as exc:
        # Unexpected errors during path resolution or file copy
        await session_svc.set_failed(
            task_id, "TRACE_NOT_FOUND", str(exc), "acquiring_trace",
            traceback.format_exc(),
        )
        return

    await session_svc.update_stage(task_id, "running_pipeline")

    # ── Stage 2: running_pipeline ─────────────────────────────────────────────
    # The semaphore limits simultaneous heavy pipeline executions across all tasks
    storage_type = request.storage_config.type
    try:
        async with semaphore:
            start = time.monotonic()
            results = await pipeline_svc.execute_pipeline(
                trace_file=str(trace_path),
                output_dir=str(run_dir),
                batch_size=request.llm_batch_size,
                # Write extracted metrics to MongoDB only when storage includes it
                store_to_mongodb=(storage_type in ("mongodb", "hybrid")),
                config=app_config,
            )
            elapsed = time.monotonic() - start
    except Exception as exc:
        await session_svc.set_failed(
            task_id, "PIPELINE_FAILED", str(exc), "running_pipeline",
            traceback.format_exc(),
        )
        return

    # Read the JSON summary file written by the pipeline and build the task result dict
    try:
        summary = await asyncio.to_thread(
            _read_json, str(run_dir / "pipeline_summary.json")
        )
        result = _build_result(results, summary, total_observations, run_dir, elapsed)
    except Exception as exc:
        await session_svc.set_failed(
            task_id, "STORAGE_ERROR", str(exc), "running_pipeline",
            traceback.format_exc(),
        )
        return

    await session_svc.set_completed(task_id, result)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_result(
    results: List[Dict[str, Any]],
    summary: dict,
    total_observations: int,
    run_dir: Path,
    elapsed: float,
) -> dict:
    """Assemble the task result payload stored in MongoDB on completion."""
    faults = []
    for r in results:
        q = r.get("quantitative", {})
        faults.append({
            "fault_id": r["fault_id"],
            "fault_name": r.get("fault_name", r["fault_id"]),
            "severity": q.get("injected_fault_category"),
            # Treat "detected" as closed; anything else (including None) as open
            "status": "closed" if q.get("fault_detected") == "Yes" else "open",
            "detected_at": q.get("agent_fault_detection_time"),
            "mitigated_at": q.get("agent_fault_mitigation_time"),
        })

    bucketing = summary.get("bucketing_tokens", {})
    extraction = summary.get("extraction_tokens", {})

    return {
        "total_observations": total_observations,
        "total_faults_detected": len(results),
        "faults": faults,
        "storage_paths": {
            "traces_dir": str(run_dir / "traces") + "/",
            "fault_buckets_dir": str(run_dir / "fault_buckets") + "/",
            "metrics_dir": str(run_dir / "metrics") + "/",
            "summary": str(run_dir / "pipeline_summary.json"),
            "log": str(run_dir / "pipeline.log"),
        },
        "token_usage": {
            "bucketing_input_tokens": bucketing.get("input", 0),
            "bucketing_output_tokens": bucketing.get("output", 0),
            "extraction_input_tokens": extraction.get("input", 0),
            "extraction_output_tokens": extraction.get("output", 0),
            "total_tokens": bucketing.get("total", 0) + extraction.get("total", 0),
        },
        "processing_time_seconds": round(elapsed, 1),
    }
