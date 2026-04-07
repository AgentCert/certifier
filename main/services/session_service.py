from typing import Optional

from motor.motor_asyncio import AsyncIOMotorCollection


class SessionService:
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
        """Insert a new PENDING task. Raises DuplicateKeyError on task_id collision."""
        from datetime import datetime, timezone
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
        """Transition PENDING → RUNNING. Sets stage to acquiring_trace."""
        await self._col.update_one(
            {"task_id": task_id, "status": "PENDING"},
            {
                "$set": {"status": "RUNNING", "stage": "acquiring_trace"},
                "$currentDate": {"started_at": True, "updated_at": True},
            },
        )

    async def update_stage(self, task_id: str, stage: str) -> None:
        """Advance the stage field during a running task."""
        await self._col.update_one(
            {"task_id": task_id},
            {
                "$set": {"stage": stage},
                "$currentDate": {"updated_at": True},
            },
        )

    async def set_completed(self, task_id: str, result: dict) -> None:
        """Transition RUNNING → COMPLETED. Raises if task is not RUNNING (double-write guard)."""
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
        """Transition to FAILED. Safe to call from any non-terminal state."""
        await self._col.update_one(
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
        """Return the task document (without _id) or None."""
        return await self._col.find_one({"task_id": task_id}, {"_id": 0})

    async def find_active_task(
        self, experiment_id: str, run_id: str
    ) -> Optional[dict]:
        """Return a PENDING or RUNNING task for this (experiment_id, run_id) pair."""
        return await self._col.find_one(
            {
                "experiment_id": experiment_id,
                "run_id": run_id,
                "status": {"$in": ["PENDING", "RUNNING"]},
            },
            {"_id": 0},
        )
