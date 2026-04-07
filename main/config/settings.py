import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    # MongoDB
    mongodb_uri: str = field(
        default_factory=lambda: os.environ["MONGODB_CONNECTION_STRING"]
    )
    mongodb_database: str = field(
        default_factory=lambda: os.getenv("MONGODB_DATABASE", "agentcert")
    )
    task_collection: str = field(
        default_factory=lambda: os.getenv("API_TASK_COLLECTION", "pipeline_tasks")
    )

    # Workspace
    workspace_dir: Path = field(
        default_factory=lambda: Path(os.getenv("WORKSPACE_DIR", "workspace")).resolve()
    )

    # Concurrency
    max_concurrent_tasks: int = field(
        default_factory=lambda: int(os.getenv("API_MAX_CONCURRENT_TASKS", "4"))
    )

    # Server
    host: str = field(default_factory=lambda: os.getenv("API_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("API_PORT", "8000")))


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
