from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CertStorageConfig(BaseModel):
    """Storage configuration for the aggregation-certification pipeline.

    ``type="local"``   — read metrics from the filesystem workspace written by
    the bucketing pipeline; write HTML/PDF reports to the same workspace.

    ``type="mongodb"`` — read metrics from MongoDB (``agent_run_metrics``
    collection via ``MetricsQueryService``); store HTML/PDF reports in GridFS
    (``cert_reports`` bucket).  Intermediate files use a temp directory that is
    cleaned up after the task completes.

    ``metrics_dir`` is used only for ``type="local"``.  When omitted (or set to
    ``""``), the router derives it automatically as
    ``workspace/{agent_id}/{experiment_id}/fault-bucketing/``.  Supply it
    explicitly only when metrics live outside the default workspace.
    """
    type: Literal["local", "mongodb"] = "local"
    # Empty string signals "derive from experiment_id + workspace_dir" (local mode)
    metrics_dir: str = Field(default="")
    container_name: str = ""     # Reserved for future blob-storage support


class AggregationCertificationRequest(BaseModel):
    """Request body for ``POST /api/v1/aggregation-certification``."""
    agent_id: str = Field(..., min_length=1, max_length=128)
    agent_name: str = Field(..., min_length=1, max_length=256)
    experiment_id: str = Field(..., min_length=1, max_length=128)
    # Optional caller-supplied identifier for this certification run (e.g. a git SHA)
    certification_run_id: str = Field(default="", max_length=128)
    # Expected number of runs per fault; used for statistical significance in the aggregator
    runs_per_fault: int = Field(default=30, ge=1, le=1000)
    storage_config: CertStorageConfig = Field(default_factory=CertStorageConfig)

    @field_validator("agent_id", "experiment_id")
    @classmethod
    def no_path_separators(cls, v: str) -> str:
        """Prevent directory-traversal attacks via user-supplied IDs."""
        if any(sep in v for sep in ("/", "\\", "..")):
            raise ValueError("must not contain path separators")
        return v
