# 03 — Application Startup

## Entry Point

`main/main.py` is the single entry point. It defines:

1. The FastAPI `app` instance.
2. The `lifespan` async context manager that runs startup and shutdown logic.
3. A `if __name__ == "__main__"` block to start uvicorn programmatically.

```python
# main/main.py  (structure, not final code)

from contextlib import asynccontextmanager
from fastapi import FastAPI
import uvicorn

@asynccontextmanager
async def lifespan(app: FastAPI):
    await _startup(app)
    yield
    await _shutdown(app)

app = FastAPI(title="AgentCert API", version="1.0.0", lifespan=lifespan)
app.include_router(router, prefix="/api/v1")

if __name__ == "__main__":
    uvicorn.run("main.main:app", host=settings.host, port=settings.port, reload=False)
```

---

## Settings

`main/config/settings.py` defines a `Settings` dataclass populated from environment variables
at import time. It does **not** use `pydantic-settings` (not in `requirements.txt`); it reads
`os.environ` directly and validates required fields.

```python
# main/config/settings.py

import os
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class Settings:
    # MongoDB
    mongodb_uri: str           = field(default_factory=lambda: os.environ["MONGODB_CONNECTION_STRING"])
    mongodb_database: str      = field(default_factory=lambda: os.getenv("MONGODB_DATABASE", "agentcert"))

    # API-layer collections (new)
    task_collection: str       = field(default_factory=lambda: os.getenv("API_TASK_COLLECTION", "pipeline_tasks"))

    # Workspace
    workspace_dir: Path        = field(default_factory=lambda: Path(os.getenv("WORKSPACE_DIR", "workspace")).resolve())

    # Concurrency
    max_concurrent_tasks: int  = field(default_factory=lambda: int(os.getenv("API_MAX_CONCURRENT_TASKS", "4")))

    # Server
    host: str                  = field(default_factory=lambda: os.getenv("API_HOST", "0.0.0.0"))
    port: int                  = field(default_factory=lambda: int(os.getenv("API_PORT", "8000")))

_settings: Settings | None = None

def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
```

`MONGODB_CONNECTION_STRING` is **required**; missing it raises `KeyError` at startup, preventing
the app from starting silently misconfigured.

All other variables have safe defaults for local development.

---

## Startup Sequence

`_startup(app)` runs the following steps in order. A failure in any step aborts startup.

### Step 1 — Load Application Config

```python
from utils.load_config import ConfigLoader
config = ConfigLoader.load_config()   # reads configs/configs.json, resolves ENV_ vars
app.state.config = config
```

`ConfigLoader` resolves all `ENV_*` tokens. The resolved dict is stored in `app.state.config` and
injected into services via FastAPI's dependency system. No service reads `configs.json` directly
at request time.

### Step 2 — Initialise Motor Client

```python
from motor.motor_asyncio import AsyncIOMotorClient
client = AsyncIOMotorClient(settings.mongodb_uri)
db = client[settings.mongodb_database]
app.state.motor_client = client
app.state.db = db
```

Motor creates the connection pool lazily on the first operation; there is no explicit connect call.

### Step 3 — Create and Index ALL MongoDB Collections

This is the critical step. Every collection the application will ever use is created here with
its full index set. Writes in later iterations will find correctly indexed collections without
any lazy-init logic.

#### 3a. `pipeline_tasks` (new — API session store)

```python
col = db[settings.task_collection]

await col.create_index("task_id", unique=True, name="idx_task_id_unique")
await col.create_index(
    [("agent_id", 1), ("experiment_id", 1), ("run_id", 1)],
    name="idx_agent_exp_run"
)
await col.create_index(
    [("status", 1), ("created_at", -1)],
    name="idx_status_created"
)
await col.create_index("created_at", name="idx_created_at")
```

#### 3b. `agent_run_metrics` (existing — Phase 1 writes)

Existing code in `utils/mongodb_util.py` creates these synchronously. Replicate via motor to
ensure they exist before the first request:

```python
col = db[config["mongodb"]["collections"]["metrics"]]  # "agent_run_metrics"

await col.create_index(
    [("fault_category", 1), ("fault_name", 1)],
    name="idx_fault_category_name"
)
await col.create_index(
    "experiment_id", unique=True, sparse=True,
    name="idx_experiment_id_unique"
)
await col.create_index(
    [("fault_category", 1), ("created_at", -1)],
    name="idx_fault_category_created"
)
await col.create_index("agent_id", sparse=True, name="idx_agent_id")
await col.create_index("run_id", sparse=True, name="idx_run_id")
```

#### 3c. `extraction_metadata` (new — iteration 2 writes, schema ready now)

```python
col = db["extraction_metadata"]

await col.create_index("extraction_id", unique=True, name="idx_extraction_id_unique")
await col.create_index(
    [("experiment_id", 1), ("run_id", 1), ("agent_id", 1)],
    name="idx_exp_run_agent"
)
await col.create_index(
    [("agent_id", 1), ("created_at", -1)],
    name="idx_agent_created"
)
```

#### 3d. `fault_metadata` (new — iteration 2 writes, schema ready now)

```python
col = db["fault_metadata"]

await col.create_index(
    [("experiment_id", 1), ("run_id", 1), ("agent_id", 1)],
    name="idx_exp_run_agent"
)
await col.create_index(
    [("agent_id", 1), ("extraction_id", 1)],
    name="idx_agent_extraction"
)
await col.create_index("fault_id", name="idx_fault_id")
await col.create_index("created_at", name="idx_created_at")
```

> **Why create iteration-2 collections at startup?** If collections are created lazily (on
> first write), a race condition can occur under concurrent load: two writes arrive simultaneously
> before the collection exists, both try to create it, and one silently wins while the other
> gets no index. Creating all collections at startup, once, under the lifespan lock, eliminates
> this class of bug.

All `create_index` calls are idempotent when the `name` parameter is specified — MongoDB will
skip creation if an index with that name already exists on that collection.

### Step 4 — Store Motor Collection Refs in App State

```python
app.state.task_col = db[settings.task_collection]
app.state.metrics_col = db[config["mongodb"]["collections"]["metrics"]]
app.state.extraction_meta_col = db["extraction_metadata"]
app.state.fault_meta_col = db["fault_metadata"]
```

Services receive these via FastAPI dependency injection, not by re-accessing `app.state` directly.

### Step 5 — Create Concurrency Semaphore

```python
import asyncio
app.state.semaphore = asyncio.Semaphore(settings.max_concurrent_tasks)
```

The semaphore limits simultaneous active pipeline runs. It is created once and shared across all
request handlers via dependency injection.

### Step 6 — Ensure Workspace Root Exists

```python
settings.workspace_dir.mkdir(parents=True, exist_ok=True)
```

---

## Shutdown Sequence

`_shutdown(app)` closes the Motor client cleanly:

```python
app.state.motor_client.close()
```

FastAPI's `BackgroundTasks` are awaited before the lifespan context exits, so in-flight tasks
complete before the connection is closed. This is the default uvicorn behaviour with SIGTERM.

> **Critical limitation**: If the process is killed with SIGKILL, in-flight tasks are abandoned
> mid-pipeline. Their `PipelineTask` document will remain in `RUNNING` state forever. A startup
> recovery sweep (not in iteration 1) should scan for `RUNNING` tasks older than N minutes and
> mark them `FAILED` with `error_code = "PROCESS_KILLED"`.

---

## Dependency Injection Pattern

Services are injected into route handlers via `Depends()`. The pattern avoids importing
`app.state` directly in routers:

```python
# main/routers/bucketing_extraction.py

from fastapi import Depends, Request
from main.services.session_service import SessionService
from main.workers.task_runner import run_task
import asyncio

def get_task_collection(request: Request):
    return request.app.state.task_col

def get_semaphore(request: Request) -> asyncio.Semaphore:
    return request.app.state.semaphore

def get_settings(request: Request):
    return request.app.state.settings  # stored at startup
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `MONGODB_CONNECTION_STRING` | **yes** | — | Full MongoDB URI (resolved from `configs.json` `ENV_MONGODB_CONNECTION_STRING`) |
| `MONGODB_DATABASE` | no | `agentcert` | Database name |
| `API_TASK_COLLECTION` | no | `pipeline_tasks` | Collection name for task sessions |
| `WORKSPACE_DIR` | no | `./workspace` | Root directory for artifact storage |
| `API_MAX_CONCURRENT_TASKS` | no | `4` | Max simultaneous background pipeline runs |
| `API_HOST` | no | `0.0.0.0` | Uvicorn bind address |
| `API_PORT` | no | `8000` | Uvicorn bind port |

All other `ENV_*` variables required by the existing modules (LLM endpoints, storage keys) are
resolved by `ConfigLoader` and do not need to be re-declared in `Settings`.
