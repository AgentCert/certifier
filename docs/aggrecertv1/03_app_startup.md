# 03 — App Startup & Settings Extension

## Settings Extension

`main/config/settings.py` gains three new fields for the certification pipeline collections:

```python
# main/config/settings.py  (additions only)

@dataclass
class Settings:
    # --- faultv1 fields (unchanged) ---
    mongodb_uri: str
    mongodb_database: str      # default: "agentcert"
    task_collection: str       # default: "pipeline_tasks"
    workspace_dir: Path        # default: Path("workspace")
    max_concurrent_tasks: int  # default: 4
    host: str                  # default: "0.0.0.0"
    port: int                  # default: 8000

    # --- aggrecertv1 additions ---
    cert_task_collection: str         # default: "certification_tasks"
    cert_metadata_collection: str     # default: "certification_metadata"
    agg_category_collection: str      # default: "aggregated_category_metadata"
    cert_workspace_dir: Path          # default: Path("workspace/cert")
    max_concurrent_cert_tasks: int    # default: 2
```

### New Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `CERT_TASK_COLLECTION` | no | `certification_tasks` | MongoDB collection for cert task tracking |
| `CERT_METADATA_COLLECTION` | no | `certification_metadata` | MongoDB collection for CertificationMetadata docs |
| `AGG_CATEGORY_COLLECTION` | no | `aggregated_category_metadata` | MongoDB collection for AggregatedCategoryMetadata docs |
| `CERT_WORKSPACE_DIR` | no | `workspace/cert` | Root directory for certification artifacts |
| `API_MAX_CONCURRENT_CERT_TASKS` | no | `2` | Max concurrent Phase 2+3 pipeline runs |

> `max_concurrent_cert_tasks` defaults to 2 (not 4) because the LLM Council and cert_builder
> narrative generation are significantly heavier than Phase 0+1 — each cert run may consume
> 6–10 concurrent LLM calls internally.

---

## `main/main.py` Changes

The lifespan function gains a second block of collection initialisation for the three new
MongoDB collections. The new router is registered alongside the existing one.

```python
# main/main.py (additions)

from main.routers.aggregation_certification import router as cert_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    config = ConfigLoader.load_config()
    client = AsyncIOMotorClient(settings.mongodb_uri)
    db = client[settings.mongodb_database]

    # --- faultv1: pipeline_tasks (unchanged) ---
    tasks_col = db[settings.task_collection]
    await _ensure_pipeline_task_indexes(tasks_col)

    # --- aggrecertv1: three new collections ---
    cert_tasks_col   = db[settings.cert_task_collection]
    cert_meta_col    = db[settings.cert_metadata_collection]
    agg_cat_col      = db[settings.agg_category_collection]

    await _ensure_cert_task_indexes(cert_tasks_col)
    await _ensure_cert_metadata_indexes(cert_meta_col)
    await _ensure_agg_category_indexes(agg_cat_col)

    # Store refs
    app.state.db               = db
    app.state.tasks_col        = tasks_col
    app.state.cert_tasks_col   = cert_tasks_col
    app.state.cert_meta_col    = cert_meta_col
    app.state.agg_cat_col      = agg_cat_col
    app.state.config           = config
    app.state.settings         = settings

    # Semaphores
    app.state.semaphore      = asyncio.Semaphore(settings.max_concurrent_tasks)
    app.state.cert_semaphore = asyncio.Semaphore(settings.max_concurrent_cert_tasks)

    # Workspace dirs
    Path(settings.workspace_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.cert_workspace_dir).mkdir(parents=True, exist_ok=True)

    yield

    client.close()

app.include_router(cert_router, prefix="/api/v1")
```

---

## Index Creation Helpers

Each helper is idempotent: `OperationFailure` with code 85 or 86 (index already exists under
a different name) is caught and silently skipped, matching the faultv1 pattern.

### `_ensure_cert_task_indexes(col)`

```python
await col.create_index([("cert_task_id", 1)], unique=True, name="idx_cert_task_id_unique")
await col.create_index([("agent_id", 1), ("experiment_id", 1)], name="idx_cert_agent_exp")
await col.create_index([("status", 1), ("created_at", -1)], name="idx_cert_status_created")
await col.create_index([("created_at", 1)], name="idx_cert_created_at")
```

### `_ensure_cert_metadata_indexes(col)`

```python
await col.create_index([("certification_id", 1)], unique=True, name="idx_certmeta_id_unique")
await col.create_index([("agent_id", 1), ("experiment_id", 1)], name="idx_certmeta_agent_exp")
await col.create_index([("agent_id", 1), ("created_at", -1)], name="idx_certmeta_agent_created")
await col.create_index([("certification_run_id", 1)], sparse=True, name="idx_certmeta_run_id")
```

### `_ensure_agg_category_indexes(col)`

```python
await col.create_index(
    [("certification_id", 1), ("fault_category", 1)],
    unique=True,
    name="idx_aggcat_cert_fault_unique"
)
await col.create_index([("agent_id", 1), ("experiment_id", 1)], name="idx_aggcat_agent_exp")
await col.create_index([("created_at", -1)], name="idx_aggcat_created_at")
```

---

## Full Startup Sequence

```
1. Load Settings
   read env vars → Settings dataclass

2. Load app config
   ConfigLoader.load_config() → resolve ENV_* vars from configs/configs.json

3. Init Motor client
   AsyncIOMotorClient(settings.mongodb_uri)

4. Create pipeline_tasks indexes (faultv1, unchanged)
   4 indexes (task_id unique, agent_exp_run, status_created, created_at)

5. Create certification_tasks indexes
   4 indexes (cert_task_id unique, agent_exp, status_created, created_at)

6. Create certification_metadata indexes
   4 indexes (certification_id unique, agent_exp, agent_created, run_id sparse)

7. Create aggregated_category_metadata indexes
   3 indexes (cert_fault unique, agent_exp, created_at)

8. Store refs in app.state
   db, tasks_col, cert_tasks_col, cert_meta_col, agg_cat_col, config, settings

9. Create concurrency semaphores
   asyncio.Semaphore(max_concurrent_tasks)       ← faultv1 (unchanged)
   asyncio.Semaphore(max_concurrent_cert_tasks)  ← new

10. Ensure workspace dirs
    workspace/        ← faultv1 (unchanged)
    workspace/cert/   ← new

11. Register routers
    /api/v1/bucketing-extraction  ← faultv1 (unchanged)
    /api/v1/aggregation-certification ← new
    /api/v1/cert-tasks/{id}           ← new

→ app ready
```

---

## Shutdown

Unchanged from faultv1. FastAPI awaits all pending BackgroundTasks before the lifespan
context exits. `--timeout-graceful-shutdown 300` recommended; cert tasks can take up to
5 min for LLM-heavy runs.
