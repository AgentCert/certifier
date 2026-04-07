import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from main.models.requests import BucketingExtractionRequest
from main.models.responses import TaskAcceptedResponse
from main.services.pipeline_service import PipelineService
from main.services.session_service import SessionService
from main.services.trace_service import TraceService
from main.workers.task_runner import run_task

router = APIRouter()


# ── Dependency factories ──────────────────────────────────────────────────

def _session_svc(request: Request) -> SessionService:
    return SessionService(request.app.state.task_col)


def _trace_svc() -> TraceService:
    return TraceService()


def _pipeline_svc() -> PipelineService:
    return PipelineService()


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.post("/bucketing-extraction", status_code=202, response_model=TaskAcceptedResponse)
async def submit_bucketing_extraction(
    body: BucketingExtractionRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    session_svc: SessionService = Depends(_session_svc),
    trace_svc: TraceService = Depends(_trace_svc),
    pipeline_svc: PipelineService = Depends(_pipeline_svc),
):
    # Reject duplicate active submissions for the same workspace path
    existing = await session_svc.find_active_task(body.experiment_id, body.run_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail={
                "status": "error",
                "error_code": "TASK_ALREADY_ACTIVE",
                "message": (
                    f"A pipeline task is already {existing['status']} "
                    f"for {body.experiment_id}/{body.run_id}"
                ),
                "details": {
                    "task_id": existing["task_id"],
                    "status": existing["status"],
                    "stage": existing["stage"],
                },
            },
        )

    task_id = str(uuid.uuid4())

    # Strip Langfuse secret_key before persisting the request snapshot
    snapshot = body.model_dump()
    if snapshot.get("trace_source", {}).get("type") == "langfuse":
        snapshot["trace_source"].pop("secret_key", None)

    await session_svc.create_task(
        task_id=task_id,
        agent_id=body.agent_id,
        experiment_id=body.experiment_id,
        run_id=body.run_id,
        request_snapshot=snapshot,
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
        poll_url=f"/api/v1/tasks/{task_id}",
    )


@router.get("/tasks/{task_id}")
async def get_task_status(
    task_id: str,
    session_svc: SessionService = Depends(_session_svc),
):
    task = await session_svc.get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={
                "status": "error",
                "error_code": "TASK_NOT_FOUND",
                "message": f"No task found with id {task_id}",
            },
        )
    return task
