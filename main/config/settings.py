import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    """Runtime configuration for the AgentCert API server.

    All values are resolved from environment variables at instantiation time.
    Fields marked ``ENV_`` in the variable name follow the same convention as
    ``configs/configs.json``; see ``.env.example`` for the full list.

    The ``get_settings()`` singleton ensures that env-var reads happen once per
    process so that tests can patch the environment before the first call.
    """

    # ── MongoDB ───────────────────────────────────────────────────────────────
    # MONGODB_CONNECTION_STRING is required — the server will crash at startup
    # if it is absent, which is the correct behaviour (fail fast on misconfiguration).
    mongodb_uri: str = field(
        default_factory=lambda: os.environ["MONGODB_CONNECTION_STRING"]
    )
    mongodb_database: str = field(
        default_factory=lambda: os.getenv("MONGODB_DATABASE", "agentcert")
    )
    # Collection that holds bucketing-extraction task documents
    task_collection: str = field(
        default_factory=lambda: os.getenv("API_TASK_COLLECTION", "pipeline_tasks")
    )

    # ── Workspace ─────────────────────────────────────────────────────────────
    # Root directory for per-run trace + metrics output (faultv1 pipeline)
    workspace_dir: Path = field(
        default_factory=lambda: Path(os.getenv("WORKSPACE_DIR", "workspace")).resolve()
    )

    # ── Concurrency ───────────────────────────────────────────────────────────
    # Maximum simultaneous bucketing-extraction pipeline executions
    max_concurrent_tasks: int = field(
        default_factory=lambda: int(os.getenv("API_MAX_CONCURRENT_TASKS", "4"))
    )

    # ── Server ────────────────────────────────────────────────────────────────
    host: str = field(default_factory=lambda: os.getenv("API_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("API_PORT", "8000")))

    # ── aggrecertv1 — certification pipeline ──────────────────────────────────
    # Collection that holds aggregation-certification task documents
    cert_task_collection: str = field(
        default_factory=lambda: os.getenv("CERT_TASK_COLLECTION", "certification_tasks")
    )
    # Collection that holds one metadata document per completed certification run
    cert_metadata_collection: str = field(
        default_factory=lambda: os.getenv("CERT_METADATA_COLLECTION", "certification_metadata")
    )
    # Collection that holds one scorecard row per fault category per certification
    agg_category_collection: str = field(
        default_factory=lambda: os.getenv("AGG_CATEGORY_COLLECTION", "aggregated_category_metadata")
    )
    # Maximum simultaneous aggregation-certification pipeline executions
    # (lower default than bucketing because cert runs are significantly heavier)
    max_concurrent_cert_tasks: int = field(
        default_factory=lambda: int(os.getenv("API_MAX_CONCURRENT_CERT_TASKS", "2"))
    )


# Module-level singleton — initialised on first call to get_settings()
_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide ``Settings`` singleton, creating it on first call."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
