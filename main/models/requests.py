from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, field_validator


class FileTraceSource(BaseModel):
    type: Literal["file"]
    file_path: str = Field(..., min_length=1)


class LangfuseTraceSource(BaseModel):
    type: Literal["langfuse"]
    base_url: str
    public_key: str
    secret_key: str
    from_timestamp: str
    page_size: int = Field(default=100, ge=1, le=500)
    max_pages: int = Field(default=20, ge=1, le=100)
    include_observations: bool = True


TraceSource = Annotated[
    Union[FileTraceSource, LangfuseTraceSource],
    Field(discriminator="type"),
]


class StorageConfig(BaseModel):
    type: Literal["local", "blob_storage", "mongodb", "hybrid"] = "local"
    container_name: str = ""


class BucketingExtractionRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=128)
    experiment_id: str = Field(..., min_length=1, max_length=128)
    run_id: str = Field(..., min_length=1, max_length=128)
    trace_source: TraceSource
    llm_batch_size: int = Field(default=5, ge=1, le=50)
    storage_config: StorageConfig = Field(default_factory=StorageConfig)

    @field_validator("experiment_id", "run_id")
    @classmethod
    def no_path_separators(cls, v: str) -> str:
        if any(sep in v for sep in ("/", "\\", "..")):
            raise ValueError("must not contain path separators")
        return v
