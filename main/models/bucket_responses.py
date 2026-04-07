from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class TaskAcceptedResponse(BaseModel):
    status: str = "accepted"
    task_id: str
    poll_url: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    stage: str
    agent_id: str
    experiment_id: str
    run_id: str
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    data: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None
