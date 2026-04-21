import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from main.models.bucket_requests import BucketingExtractionRequest
from main.models.bucket_responses import TaskAcceptedResponse
from main.services.pipeline_service import BucketPipelineService
from main.services.session_service import SessionService
from main.services.trace_service import TraceService
from main.workers.bucket_task_runner import run_task

router = APIRouter()


# ── Dependency factories ──────────────────────────────────────────────────────
# FastAPI resolves these at request time and injects the result into endpoint
# parameters.  Using factories (instead of module-level singletons) ensures
# each request gets a fresh service instance bound to the current DB collection.

def _session_svc(request: Request) -> SessionService:
    return SessionService(request.app.state.task_col)


def _trace_svc() -> TraceService:
    return TraceService()


def _pipeline_svc() -> BucketPipelineService:
    return BucketPipelineService()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/bucketing-extraction", status_code=202, response_model=TaskAcceptedResponse)
async def submit_bucketing_extraction(
    body: BucketingExtractionRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    session_svc: SessionService = Depends(_session_svc),
    trace_svc: TraceService = Depends(_trace_svc),
    pipeline_svc: BucketPipelineService = Depends(_pipeline_svc),
):
    """Accept a bucketing-extraction job and enqueue it as a background task.

    Returns HTTP 202 with a ``task_id`` immediately; clients poll
    ``GET /api/v1/tasks/{task_id}`` for status.

    Raises:
        HTTP 409: If a task for this ``(experiment_id, run_id)`` is already active.
    """
    # Reject duplicate active submissions for the same (experiment_id, run_id) workspace path
    existing = await session_svc.find_active_task(body.agent_id, body.experiment_id, body.run_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail={
                "status": "error",
                "error_code": "TASK_ALREADY_ACTIVE",
                "message": (
                    f"A pipeline task is already {existing['status']} "
                    f"for {body.agent_id}/{body.experiment_id}/{body.run_id}"
                ),
                "details": {
                    "task_id": existing["task_id"],
                    "status": existing["status"],
                    "stage": existing["stage"],
                },
            },
        )

    task_id = str(uuid.uuid4())

    # Persist the task document BEFORE dispatching the background worker so that
    # the poll endpoint can return the task immediately after this response is sent.
    await session_svc.create_task(
        task_id=task_id,
        agent_id=body.agent_id,
        experiment_id=body.experiment_id,
        run_id=body.run_id,
        request_snapshot=body.model_dump(),
    )

    background_tasks.add_task(
        run_task,
        task_id=task_id,
        request=body,
        session_svc=session_svc,
        trace_svc=trace_svc,
        pipeline_svc=pipeline_svc,
        semaphore=request.app.state.semaphore,
        settings=request.app.state.settings,
        app_config=request.app.state.config,
    )

    return TaskAcceptedResponse(
        task_id=task_id,
        poll_url=f"/api/v1/tasks?experiment_id={body.experiment_id}&experiment_run_id={body.run_id}",
    )


@router.get("/tasks")
async def get_task_status(
    experiment_id: str = Query(..., description="Experiment ID supplied at submission"),
    experiment_run_id: str = Query(..., description="Run ID supplied at submission"),
    session_svc: SessionService = Depends(_session_svc),
):
    """Poll the status of the most-recent bucketing-extraction task for an experiment run."""
    task = await session_svc.get_task_by_run(experiment_id, experiment_run_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={
                "status": "error",
                "error_code": "TASK_NOT_FOUND",
                "message": f"No task found for experiment_id={experiment_id} experiment_run_id={experiment_run_id}",
            },
        )
    return task
