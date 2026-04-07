import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from pymongo.errors import OperationFailure

from main.config.settings import get_settings
from main.routers.bucketing_extraction import router
from utils.load_config import ConfigLoader


async def _ensure_indexes(col: AsyncIOMotorCollection) -> None:
    """Create pipeline_tasks indexes, skipping any that already exist under a different name."""
    specs = [
        ("task_id", {"unique": True, "name": "idx_task_id_unique"}),
        ([("agent_id", 1), ("experiment_id", 1), ("run_id", 1)], {"name": "idx_agent_exp_run"}),
        ([("status", 1), ("created_at", -1)], {"name": "idx_status_created"}),
        ("created_at", {"name": "idx_created_at"}),
    ]
    for key, kwargs in specs:
        try:
            await col.create_index(key, **kwargs)
        except OperationFailure as exc:
            if exc.code in (85, 86):
                # Index already exists on this key with a different name — functionally equivalent, skip.
                pass
            else:
                raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # 1. Load app config (resolves ENV_ vars from configs/configs.json)
    config = ConfigLoader.load_config()
    app.state.config = config

    # 2. Init Motor async MongoDB client
    client = AsyncIOMotorClient(settings.mongodb_uri)
    db = client[settings.mongodb_database]
    app.state.motor_client = client
    app.state.db = db

    # 3. Create pipeline_tasks collection + indexes (idempotent)
    col = db[settings.task_collection]
    await _ensure_indexes(col)
    app.state.task_col = col

    # 4. Store settings + concurrency semaphore
    app.state.settings = settings
    app.state.semaphore = asyncio.Semaphore(settings.max_concurrent_tasks)

    # 5. Ensure workspace root exists
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)

    yield

    # Shutdown: close Motor connection after background tasks complete
    client.close()


app = FastAPI(title="AgentCert API", version="1.0.0", lifespan=lifespan)
app.include_router(router, prefix="/api/v1")


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "main.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        timeout_graceful_shutdown=300,
    )
