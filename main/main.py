import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from pymongo.errors import OperationFailure

from main.config.settings import get_settings
from main.routers.bucketing_extraction import router
from main.routers.aggregation_certification import router as cert_router
from utils.load_config import ConfigLoader


# MongoDB OperationFailure codes that mean an index already exists under a different name.
# 85 = IndexOptionsConflict  (same key, different options — e.g. name clash)
# 86 = IndexKeySpecsConflict (same name, different key spec)
# Both are safe to ignore on startup: the existing index is functionally equivalent.
_INDEX_CONFLICT_CODES = frozenset({85, 86})


async def _apply_indexes(col: AsyncIOMotorCollection, specs: list) -> None:
    """Idempotently create indexes on *col* from *specs*.

    Each entry in *specs* is ``(key_or_list, kwargs)`` matching the signature of
    ``AsyncIOMotorCollection.create_index``.  Index-conflict errors (codes 85/86)
    are silently swallowed so that repeated startup calls are safe.
    """
    for key, kwargs in specs:
        try:
            await col.create_index(key, **kwargs)
        except OperationFailure as exc:
            if exc.code not in _INDEX_CONFLICT_CODES:
                raise


async def _ensure_indexes(col: AsyncIOMotorCollection) -> None:
    """Create indexes for the *pipeline_tasks* collection (bucketing-extraction pipeline)."""
    await _apply_indexes(col, [
        # Unique task lookup by opaque task_id UUID
        ("task_id", {"unique": True, "name": "idx_task_id_unique"}),
        # Composite key used to detect duplicate active submissions for the same run
        ([("agent_id", 1), ("experiment_id", 1), ("run_id", 1)], {"name": "idx_agent_exp_run"}),
        # Status + created_at: efficient polling queries sorted by recency
        ([("status", 1), ("created_at", -1)], {"name": "idx_status_created"}),
        # TTL / range queries on created_at
        ("created_at", {"name": "idx_created_at"}),
    ])


async def _ensure_cert_task_indexes(col: AsyncIOMotorCollection) -> None:
    """Create indexes for the *certification_tasks* collection (aggregation-certification pipeline)."""
    await _apply_indexes(col, [
        ([("cert_task_id", 1)], {"unique": True, "name": "idx_cert_task_id_unique"}),
        # Duplicate active task guard: one active cert job per (agent, experiment) at a time
        ([("agent_id", 1), ("experiment_id", 1)], {"name": "idx_cert_agent_exp"}),
        ([("status", 1), ("created_at", -1)], {"name": "idx_cert_status_created"}),
        ([("created_at", 1)], {"name": "idx_cert_created_at"}),
    ])


async def _ensure_cert_metadata_indexes(col: AsyncIOMotorCollection) -> None:
    """Create indexes for *certification_metadata* (one document per completed certification run)."""
    await _apply_indexes(col, [
        ([("certification_id", 1)], {"unique": True, "name": "idx_certmeta_id_unique"}),
        ([("agent_id", 1), ("experiment_id", 1)], {"name": "idx_certmeta_agent_exp"}),
        # Descending created_at: fetch the latest cert for an agent efficiently
        ([("agent_id", 1), ("created_at", -1)], {"name": "idx_certmeta_agent_created"}),
        # sparse=True: certification_run_id is optional — nulls must not fill the index
        ([("certification_run_id", 1)], {"sparse": True, "name": "idx_certmeta_run_id"}),
    ])


async def _ensure_agg_category_indexes(col: AsyncIOMotorCollection) -> None:
    """Create indexes for *aggregated_category_metadata* (one doc per fault-category per certification)."""
    await _apply_indexes(col, [
        # Composite unique: a certification may only have one scorecard row per fault category
        (
            [("certification_id", 1), ("fault_category", 1)],
            {"unique": True, "name": "idx_aggcat_cert_fault_unique"},
        ),
        ([("agent_id", 1), ("experiment_id", 1)], {"name": "idx_aggcat_agent_exp"}),
        ([("created_at", -1)], {"name": "idx_aggcat_created_at"}),
    ])


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # 1. Load app config (resolves ENV_ vars from configs/configs.json)
    config = ConfigLoader.load_config()
    app.state.config = config

    # 2. Init Motor async MongoDB client
    client = AsyncIOMotorClient(settings.mongodb_uri)
    db = client[settings.mongodb_database]
    app.state.db = db

    # 3. pipeline_tasks collection + indexes (faultv1 — bucketing/extraction)
    task_col = db[settings.task_collection]
    await _ensure_indexes(task_col)
    app.state.task_col = task_col

    # 4. certification_tasks + indexes (aggrecertv1 — aggregation/certification)
    cert_tasks_col = db[settings.cert_task_collection]
    await _ensure_cert_task_indexes(cert_tasks_col)
    app.state.cert_tasks_col = cert_tasks_col

    # 5. certification_metadata + indexes (stores per-run cert results)
    cert_meta_col = db[settings.cert_metadata_collection]
    await _ensure_cert_metadata_indexes(cert_meta_col)
    app.state.cert_meta_col = cert_meta_col

    # 6. aggregated_category_metadata + indexes (per-category scorecard rows)
    agg_cat_col = db[settings.agg_category_collection]
    await _ensure_agg_category_indexes(agg_cat_col)
    app.state.agg_cat_col = agg_cat_col

    # 7. Attach settings + semaphores to app state
    app.state.settings = settings
    # Semaphores cap simultaneous heavy pipeline executions to prevent resource exhaustion
    app.state.semaphore = asyncio.Semaphore(settings.max_concurrent_tasks)
    app.state.cert_semaphore = asyncio.Semaphore(settings.max_concurrent_cert_tasks)

    # 8. Ensure workspace directories exist before any request arrives
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    settings.cert_workspace_dir.mkdir(parents=True, exist_ok=True)

    yield

    # Shutdown: close Motor connection pool after all background tasks complete
    client.close()


app = FastAPI(title="AgentCert API", version="1.0.0", lifespan=lifespan)
app.include_router(router, prefix="/api/v1")
app.include_router(cert_router, prefix="/api/v1")


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "main.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        # Allow up to 5 minutes for in-flight background tasks to finish on SIGTERM
        timeout_graceful_shutdown=300,
    )
