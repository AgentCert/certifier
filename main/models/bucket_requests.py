from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, field_validator


# ── Trace source discriminated union ─────────────────────────────────────────
# The ``type`` field is the discriminator: Pydantic selects the correct model
# at parse time so the router receives a fully-typed object.

class FileTraceSource(BaseModel):
    """Trace sourced from a file already present on the server filesystem."""
    type: Literal["file"]
    file_path: str = Field(..., min_length=1)


class LangfuseTraceSource(BaseModel):
    """Trace sourced by fetching observations directly from a Langfuse instance."""
    type: Literal["langfuse"]
    base_url: str
    public_key: str
    # secret_key is stripped from the persisted request snapshot (see bucketing_extraction.py)
    secret_key: str
    from_timestamp: str          # ISO-8601 string; the fetch returns traces after this point
    page_size: int = Field(default=100, ge=1, le=500)
    max_pages: int = Field(default=20, ge=1, le=100)
    include_observations: bool = True


TraceSource = Annotated[
    Union[FileTraceSource, LangfuseTraceSource],
    Field(discriminator="type"),
]


class StorageConfig(BaseModel):
    """Controls where extracted metrics are persisted after Phase 1."""
    # "local" = filesystem only; "mongodb" = DB only; "hybrid" = both
    type: Literal["local", "blob_storage", "mongodb", "hybrid"] = "local"
    container_name: str = ""     # Used only for blob_storage / hybrid


class BucketingExtractionRequest(BaseModel):
    """Request body for ``POST /api/v1/bucketing-extraction``."""
    agent_id: str = Field(..., min_length=1, max_length=128)
    experiment_id: str = Field(..., min_length=1, max_length=128)
    # run_id uniquely identifies one execution of the agent within the experiment
    run_id: str = Field(..., min_length=1, max_length=128)
    trace_source: TraceSource
    # Controls LLM call batching during Phase 0 fault bucketing
    llm_batch_size: int = Field(default=5, ge=1, le=50)
    storage_config: StorageConfig = Field(default_factory=StorageConfig)

    @field_validator("agent_id", "experiment_id", "run_id")
    @classmethod
    def no_path_separators(cls, v: str) -> str:
        """Prevent directory-traversal attacks via user-supplied IDs."""
        if any(sep in v for sep in ("/", "\\", "..")):
            raise ValueError("must not contain path separators")
        return v
