from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class CertTaskAcceptedResponse(BaseModel):
    status: str = "accepted"
    cert_task_id: str
    poll_url: str


class CertTaskStatusResponse(BaseModel):
    cert_task_id: str
    status: str
    stage: str
    agent_id: str
    agent_name: str
    experiment_id: str
    certification_run_id: str
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    data: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None
