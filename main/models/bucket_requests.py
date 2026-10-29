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
    """Trace sourced by fetching observations directly from a Langfuse instance.

    If ``trace_id`` is supplied, the trace is fetched by direct ID lookup --
    correct regardless of what metadata keys the instrumentation layer wrote.
    This is the required path for agents instrumented via ``agent-sidecar``,
    which sets Langfuse's own ``trace_id`` to the ChaosCenter ``NOTIFY_ID`` but
    deliberately omits ``experiment_id``/``experiment_run_id`` from trace
    metadata (to preserve blind-observer integrity), so the metadata-filter
    search below can never match those traces.

    Otherwise (``trace_id`` omitted), traces are identified by matching the
    request's ``experiment_id`` and ``run_id`` against trace metadata keys of
    the same name in Langfuse. Langfuse credentials (LANGFUSE_HOST,
    LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY) are loaded from environment
    variables at application launch.
    """
    type: Literal["langfuse"]
    trace_id: str = Field(default="", max_length=128)
    page_size: int = Field(default=50, ge=1, le=500)
    max_pages: int = Field(default=10, ge=1, le=100)
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


class FaultWindow(BaseModel):
    """One externally-known fault's active time window, supplied out-of-band
    by the caller (e.g. derived from Argo Workflow step start/end timestamps)
    so Phase 0 can split a trace into per-fault buckets even when the trace
    itself carries no `fault: *` spans to split on natively -- e.g. agents
    like flash-agent that are deliberately kept blind to fault identity, so
    nothing in their own trace ever names which fault is active.

    This is supplied entirely after the run completes, from the orchestration
    layer that injected the faults (which already knows this ground truth
    authoritatively) -- it never touches anything the agent's own LLM context
    saw, so it doesn't compromise blind-observer integrity.
    """
    fault_name: str = Field(..., min_length=1, max_length=200)
    start_time: str = Field(..., description="ISO-8601 timestamp")
    end_time: str = Field(..., description="ISO-8601 timestamp")


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
    # Optional ground-truth fault windows (see FaultWindow docstring). Empty
    # by default -- fully backward compatible; Phase 0 falls back to its
    # existing in-trace-span-based / single-fault-bucket behavior when absent.
    fault_windows: list[FaultWindow] = Field(default_factory=list)

    @field_validator("agent_id", "experiment_id", "run_id")
    @classmethod
    def no_path_separators(cls, v: str) -> str:
        """Prevent directory-traversal attacks via user-supplied IDs."""
        if any(sep in v for sep in ("/", "\\", "..")):
            raise ValueError("must not contain path separators")
        return v
