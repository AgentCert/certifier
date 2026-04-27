from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorCollection


# Task lifecycle state machine:
#
#   PENDING ──► RUNNING ──► COMPLETED
#      │            │
#      └────────────┴──► FAILED
#
# Transitions are enforced by matching on the current `status` field in each
# update_one filter, so concurrent writes cannot double-advance a task.


class SessionService:
    """Manages lifecycle state for bucketing-extraction pipeline tasks (pipeline_tasks collection)."""

    def __init__(self, collection: AsyncIOMotorCollection):
        self._col = collection

    async def create_task(
        self,
        task_id: str,
        agent_id: str,
        experiment_id: str,
        run_id: str,
        request_snapshot: dict,
    ) -> None:
        """Insert a new PENDING task document.

        Raises ``DuplicateKeyError`` if *task_id* already exists (the unique
        ``idx_task_id_unique`` index enforces this at the DB level).
        """
        now = datetime.now(timezone.utc)
        await self._col.insert_one({
            "task_id": task_id,
            "agent_id": agent_id,
            "experiment_id": experiment_id,
            "run_id": run_id,
            "status": "PENDING",
            "stage": "pending",
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
            "request": request_snapshot,
            "result": None,
            "error": None,
        })

    async def set_started(self, task_id: str) -> None:
        """Transition PENDING → RUNNING; sets the initial stage to *acquiring_trace*."""
        await self._col.update_one(
            # Guard: only advance from PENDING to prevent double-start races
            {"task_id": task_id, "status": "PENDING"},
            {
                "$set": {"status": "RUNNING", "stage": "acquiring_trace"},
                "$currentDate": {"started_at": True, "updated_at": True},
            },
        )

    async def update_stage(self, task_id: str, stage: str) -> None:
        """Advance the *stage* label during an already-RUNNING task (no status change)."""
        await self._col.update_one(
            {"task_id": task_id},
            {
                "$set": {"stage": stage},
                "$currentDate": {"updated_at": True},
            },
        )

    async def set_completed(self, task_id: str, result: dict) -> None:
        """Transition RUNNING → COMPLETED and persist the pipeline result payload.

        Raises ``ValueError`` if the task is not currently RUNNING, acting as a
        double-write guard (e.g. if a background task races to complete twice).
        """
        updated = await self._col.update_one(
            {"task_id": task_id, "status": "RUNNING"},
            {
                "$set": {"status": "COMPLETED", "stage": "done", "result": result},
                "$currentDate": {"completed_at": True, "updated_at": True},
            },
        )
        if updated.matched_count == 0:
            raise ValueError(f"Task {task_id} is not in RUNNING state")

    async def set_failed(
        self,
        task_id: str,
        error_code: str,
        message: str,
        failed_stage: str,
        detail: str,
    ) -> None:
        """Transition to FAILED from any non-terminal state (PENDING or RUNNING).

        Safe to call from error handlers regardless of how far the task progressed.
        """
        await self._col.update_one(
            # Accept both PENDING and RUNNING so failure can be recorded early
            {"task_id": task_id, "status": {"$in": ["PENDING", "RUNNING"]}},
            {
                "$set": {
                    "status": "FAILED",
                    "error": {
                        "error_code": error_code,
                        "message": message,
                        "failed_stage": failed_stage,
                        "detail": detail,
                    },
                },
                "$currentDate": {"completed_at": True, "updated_at": True},
            },
        )

    async def get_task(self, task_id: str) -> Optional[dict]:
        """Return the full task document (without ``_id``) or ``None`` if not found."""
        return await self._col.find_one({"task_id": task_id}, {"_id": 0})

    async def get_task_by_run(self, experiment_id: str, run_id: str) -> Optional[dict]:
        """Return the most-recent task for *(experiment_id, run_id)*, or ``None``."""
        return await self._col.find_one(
            {"experiment_id": experiment_id, "run_id": run_id},
            {"_id": 0},
            sort=[("created_at", -1)],
        )

    async def find_active_task(
        self, agent_id: str, experiment_id: str, run_id: str
    ) -> Optional[dict]:
        """Return the first PENDING or RUNNING task for *(agent_id, experiment_id, run_id)*, or ``None``.

        Used by the router to reject duplicate submissions before task creation.
        """
        return await self._col.find_one(
            {
                "agent_id": agent_id,
                "experiment_id": experiment_id,
                "run_id": run_id,
                "status": {"$in": ["PENDING", "RUNNING"]},
            },
            {"_id": 0},
        )


class CertSessionService:
    """Manages lifecycle state for aggregation-certification pipeline tasks (certification_tasks collection)."""

    def __init__(self, collection: AsyncIOMotorCollection):
        self._col = collection

    async def create_task(
        self,
        cert_task_id: str,
        agent_id: str,
        agent_name: str,
        experiment_id: str,
        certification_run_id: str,
        request_snapshot: dict,
    ) -> None:
        """Insert a new PENDING certification task.

        Raises ``DuplicateKeyError`` if *cert_task_id* already exists.
        """
        now = datetime.now(timezone.utc)
        await self._col.insert_one({
            "cert_task_id": cert_task_id,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "experiment_id": experiment_id,
            "certification_run_id": certification_run_id,
            "status": "PENDING",
            "stage": "pending",
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
            "request": request_snapshot,
            "result": None,
            "error": None,
        })

    async def set_started(self, cert_task_id: str) -> None:
        """Transition PENDING → RUNNING; sets the initial stage to *fetching_metrics*."""
        await self._col.update_one(
            {"cert_task_id": cert_task_id, "status": "PENDING"},
            {
                "$set": {"status": "RUNNING", "stage": "fetching_metrics"},
                "$currentDate": {"started_at": True, "updated_at": True},
            },
        )

    async def update_stage(self, cert_task_id: str, stage: str) -> None:
        """Advance the *stage* label during an already-RUNNING task."""
        await self._col.update_one(
            {"cert_task_id": cert_task_id},
            {
                "$set": {"stage": stage},
                "$currentDate": {"updated_at": True},
            },
        )

    async def set_completed(self, cert_task_id: str, result: dict) -> None:
        """Transition RUNNING → COMPLETED.

        Raises ``ValueError`` if the task is not currently RUNNING (double-write guard).
        """
        updated = await self._col.update_one(
            {"cert_task_id": cert_task_id, "status": "RUNNING"},
            {
                "$set": {"status": "COMPLETED", "stage": "done", "result": result},
                "$currentDate": {"completed_at": True, "updated_at": True},
            },
        )
        if updated.matched_count == 0:
            raise ValueError(f"Task {cert_task_id} is not in RUNNING state")

    async def set_failed(
        self,
        cert_task_id: str,
        error_code: str,
        message: str,
        failed_stage: str,
        detail: str,
    ) -> None:
        """Transition to FAILED from any non-terminal state (PENDING or RUNNING)."""
        await self._col.update_one(
            {"cert_task_id": cert_task_id, "status": {"$in": ["PENDING", "RUNNING"]}},
            {
                "$set": {
                    "status": "FAILED",
                    "error": {
                        "error_code": error_code,
                        "message": message,
                        "failed_stage": failed_stage,
                        "detail": detail,
                    },
                },
                "$currentDate": {"completed_at": True, "updated_at": True},
            },
        )

    async def get_task(self, cert_task_id: str) -> Optional[dict]:
        """Return the full certification task document (without ``_id``) or ``None``."""
        return await self._col.find_one({"cert_task_id": cert_task_id}, {"_id": 0})

    async def get_task_by_experiment(self, experiment_id: str) -> Optional[dict]:
        """Return the most-recent certification task for *experiment_id*, or ``None``."""
        return await self._col.find_one(
            {"experiment_id": experiment_id},
            {"_id": 0},
            sort=[("created_at", -1)],
        )

    async def find_active_task(
        self, agent_id: str, experiment_id: str
    ) -> Optional[dict]:
        """Return a PENDING or RUNNING task for *(agent_id, experiment_id)*, or ``None``.

        Used by the router to reject duplicate certification submissions.
        """
        return await self._col.find_one(
            {
                "agent_id": agent_id,
                "experiment_id": experiment_id,
                "status": {"$in": ["PENDING", "RUNNING"]},
            },
            {"_id": 0},
        )
