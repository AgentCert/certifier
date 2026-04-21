import asyncio
import glob
import json
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from main.models.cert_requests import AggregationCertificationRequest
from main.models.cert_responses import CertTaskAcceptedResponse
from main.services.pipeline_service import CertPipelineService
from main.services.session_service import CertSessionService
from main.workers.cert_task_runner import run_cert_task

router = APIRouter()


# ── Custom exception for metrics pre-flight validation ────────────────────

class MetricsValidationError(Exception):
    """Raised by ``_discover_and_validate`` when the metrics directory is missing
    or contains no documents matching the requested *agent_id*.

    Using a typed exception (instead of embedding the error code in the message
    string) keeps the router handler clean and avoids fragile string parsing.
    """

    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(detail)
        self.error_code = error_code


# ── Dependency factories ──────────────────────────────────────────────────

def _cert_session_svc(request: Request) -> CertSessionService:
    return CertSessionService(request.app.state.cert_tasks_col)


def _cert_pipeline_svc() -> CertPipelineService:
    return CertPipelineService()


# ── Metrics pre-flight helpers ────────────────────────────────────────────

def _extract_agent_id_from_doc(doc: dict) -> str | None:
    """Mirror of ``DirectoryQueryService._extract_agent_id()`` in the aggregator module.

    Checks both the top-level ``agent_id`` field and the nested
    ``quantitative.agent_id`` path so that both storage layouts are handled.
    """
    return doc.get("agent_id") or doc.get("quantitative", {}).get("agent_id")


def _discover_and_validate(metrics_dir: str, agent_id: str) -> int:
    """Synchronously discover ``*metrics.json`` files and count documents for *agent_id*.

    Runs in a thread (via ``asyncio.to_thread``) because it performs filesystem I/O.

    Returns:
        Number of matching metric documents found.

    Raises:
        MetricsValidationError: If the directory does not exist, contains no
            ``*metrics.json`` files, or none of them match *agent_id*.
    """
    path = Path(metrics_dir)
    if not path.is_dir():
        raise MetricsValidationError(
            "METRICS_NOT_FOUND",
            f"{metrics_dir} does not exist or is not a directory",
        )

    # Recursively find all files ending in *metrics.json
    files = sorted(glob.glob(str(path / "**" / "*metrics.json"), recursive=True))
    if not files:
        raise MetricsValidationError(
            "METRICS_NOT_FOUND",
            f"No *metrics.json files found in '{metrics_dir}'",
        )

    count = 0
    for filepath in files:
        try:
            data = json.loads(Path(filepath).read_text(encoding="utf-8"))
            # Each file may contain either a single document or a list of documents
            docs = data if isinstance(data, list) else [data]
            count += sum(
                1 for d in docs
                if isinstance(d, dict) and _extract_agent_id_from_doc(d) == agent_id
            )
        except (json.JSONDecodeError, OSError):
            # Skip unreadable / malformed files rather than aborting the pre-flight
            continue

    if count == 0:
        raise MetricsValidationError(
            "METRICS_NOT_FOUND",
            f"No metrics documents found for agent_id='{agent_id}' in '{metrics_dir}'",
        )
    return count


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.post(
    "/aggregation-certification",
    status_code=202,
    response_model=CertTaskAcceptedResponse,
)
async def submit_aggregation_certification(
    body: AggregationCertificationRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    cert_session_svc: CertSessionService = Depends(_cert_session_svc),
    cert_pipeline_svc: CertPipelineService = Depends(_cert_pipeline_svc),
):
    # 1. Validate storage type — only "local" is supported in iteration 1
    if body.storage_config.type != "local":
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error_code": "INVALID_REQUEST",
                "message": (
                    f"storage_config.type '{body.storage_config.type}' is not supported "
                    "in iteration 1. Use 'local'."
                ),
                "details": {
                    "failed_stage": "validation",
                    "error": f"storage_type={body.storage_config.type}",
                },
            },
        )

    # 1b. Resolve metrics_dir: if the caller did not supply one, derive it from
    #     the bucketing workspace layout: workspace/{experiment_id}/
    #     The DirectoryQueryService glob recurses into every {run_id}/metrics/
    #     subdirectory under that path, so all runs for the experiment are picked up.
    if not body.storage_config.metrics_dir:
        settings = request.app.state.settings
        body.storage_config.metrics_dir = str(
            settings.workspace_dir / body.agent_id / body.experiment_id / "fault-bucketing"
        )

    # 2. Metrics pre-flight: directory existence + agent_id match
    #    Runs in a thread because _discover_and_validate does blocking filesystem I/O
    try:
        await asyncio.to_thread(
            _discover_and_validate, body.storage_config.metrics_dir, body.agent_id
        )
    except MetricsValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error_code": exc.error_code,
                "message": str(exc),
                "details": {"failed_stage": "metrics_validation", "error": str(exc)},
            },
        )

    # 3. Duplicate submission guard: reject if a task for this (agent, experiment) is already active
    existing = await cert_session_svc.find_active_task(body.agent_id, body.experiment_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail={
                "status": "error",
                "error_code": "TASK_ALREADY_ACTIVE",
                "message": (
                    f"A certification task is already {existing['status']} "
                    f"for {body.agent_id}/{body.experiment_id}"
                ),
                "details": {
                    "cert_task_id": existing["cert_task_id"],
                    "status": existing["status"],
                    "stage": existing["stage"],
                },
            },
        )

    cert_task_id = str(uuid.uuid4())

    # 4. Persist the task session before dispatching the background worker,
    #    so that the poll endpoint returns 200 immediately after this handler returns.
    try:
        await cert_session_svc.create_task(
            cert_task_id=cert_task_id,
            agent_id=body.agent_id,
            agent_name=body.agent_name,
            experiment_id=body.experiment_id,
            certification_run_id=body.certification_run_id,
            request_snapshot=body.model_dump(),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "error_code": "MONGODB_ERROR",
                "message": "Failed to create certification task session",
                "details": {"failed_stage": "session_create", "error": str(exc)},
            },
        )

    # 5. Dispatch background worker — runs after this response is sent
    background_tasks.add_task(
        run_cert_task,
        cert_task_id=cert_task_id,
        request=body,
        cert_session_svc=cert_session_svc,
        cert_pipeline_svc=cert_pipeline_svc,
        cert_semaphore=request.app.state.cert_semaphore,
        cert_meta_col=request.app.state.cert_meta_col,
        agg_cat_col=request.app.state.agg_cat_col,
        settings=request.app.state.settings,
        app_config=request.app.state.config,
    )

    return CertTaskAcceptedResponse(
        cert_task_id=cert_task_id,
        poll_url=f"/api/v1/cert-tasks?experiment_id={body.experiment_id}",
    )


@router.get("/cert-tasks")
async def get_cert_task_status(
    experiment_id: str = Query(..., description="Experiment ID supplied at submission"),
    cert_session_svc: CertSessionService = Depends(_cert_session_svc),
):
    """Poll the status of the most-recent aggregation-certification task for an experiment."""
    task = await cert_session_svc.get_task_by_experiment(experiment_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={
                "status": "error",
                "error_code": "TASK_NOT_FOUND",
                "message": f"No certification task found for experiment_id={experiment_id}",
            },
        )
    return task
