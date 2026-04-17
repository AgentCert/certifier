from typing import Literal

from pydantic import BaseModel, Field, field_validator


class LocalCertStorageConfig(BaseModel):
    """Storage configuration for the aggregation-certification pipeline.

    Only ``type="local"`` is supported in iteration 1.

    ``metrics_dir`` is optional.  When omitted (or set to ``""``), the router
    derives it automatically as ``workspace/{experiment_id}/`` — the parent
    directory written by the bucketing pipeline.  The recursive glob inside
    ``DirectoryQueryService`` then finds all ``*metrics.json`` files across
    every ``{run_id}`` subdirectory for that experiment.

    Supply ``metrics_dir`` explicitly only when the metrics live outside the
    default workspace (e.g. a merged export directory).
    """
    type: Literal["local"] = "local"
    # Empty string signals "derive from experiment_id + workspace_dir"
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
    storage_config: LocalCertStorageConfig = Field(default_factory=LocalCertStorageConfig)

    @field_validator("agent_id", "experiment_id")
    @classmethod
    def no_path_separators(cls, v: str) -> str:
        """Prevent directory-traversal attacks via user-supplied IDs."""
        if any(sep in v for sep in ("/", "\\", "..")):
            raise ValueError("must not contain path separators")
        return v
