# 08 — Flow Diagrams

---

## 1. API Request Lifecycle (Client View)

```mermaid
sequenceDiagram
    participant C as Client
    participant API as FastAPI
    participant DB as MongoDB<br/>(pipeline_tasks)
    participant BG as Background Worker

    C->>API: POST /api/v1/bucketing-extraction
    API->>DB: find_active_task(exp_id, run_id)
    alt duplicate active task
        DB-->>API: existing task doc
        API-->>C: 409 Conflict
    else no active task
        DB-->>API: null
        API->>DB: create_task() → PENDING
        API->>BG: schedule run_task() (non-blocking)
        API-->>C: 202 Accepted { task_id, poll_url }
    end

    loop poll until terminal
        C->>API: GET /api/v1/tasks/{task_id}
        API->>DB: get_task(task_id)
        DB-->>API: task doc
        API-->>C: { status, stage, data, error }
    end
```

---

## 2. Task State Machine

```mermaid
stateDiagram-v2
    [*] --> PENDING : create_task()

    PENDING --> RUNNING : set_started()

    RUNNING --> COMPLETED : set_completed(result)
    RUNNING --> FAILED : set_failed(error_code, detail)

    COMPLETED --> [*]
    FAILED --> [*]
```

**Rules:**
- `COMPLETED` and `FAILED` are terminal — no further updates.
- `set_completed` / `set_failed` filter on `status = RUNNING` before writing; raise if no match (prevents double-write race).
- `set_failed` is safe to call even if status is still `PENDING` (handles early failures before `set_started` fires).

---

## 3. Task Runner Stage Flow (current — 6 stages)

```mermaid
flowchart TD
    START([run_task called]) --> SET_STARTED

    SET_STARTED["set_started()\nstatus: PENDING → RUNNING\nstage: trace_fetch"]
    SET_STARTED --> TRACE

    TRACE["STAGE: trace_fetch\nacquire trace\n• file: copy to workspace\n• langfuse: fetch + format"]
    TRACE -->|TraceIngestionError| FAIL_TRACE["set_failed()\nTRACE_NOT_FOUND\nLANGFUSE_FETCH_ERROR\nTRACE_PARSE_ERROR"]

    TRACE -->|ok| UPD1["update_stage('validation')"]
    UPD1 --> VALIDATION

    VALIDATION["STAGE: validation\njson.load()\nis array? non-empty?\ncount observations"]
    VALIDATION -->|invalid| FAIL_VAL["set_failed()\nTRACE_PARSE_ERROR"]

    VALIDATION -->|ok| UPD2["update_stage('bucketing')"]
    UPD2 --> PIPELINE

    PIPELINE["STAGE: bucketing\nasync with semaphore:\nawait run_pipeline()\n[bucketing + extraction,\nopaque single call]"]
    PIPELINE -->|Exception| FAIL_PIPE["set_failed()\nBUCKETING_FAILED\nPIPELINE_FAILED"]

    PIPELINE -->|ok| UPD3["update_stage('storage')"]
    UPD3 --> STORAGE

    STORAGE["STAGE: storage\nread pipeline_summary.json\nbuild result dict\n(~10 lines, milliseconds)"]
    STORAGE -->|IOError| FAIL_STOR["set_failed()\nSTORAGE_ERROR"]

    STORAGE -->|ok| DONE["set_completed(result)\nstatus: RUNNING → COMPLETED\nstage: done"]

    FAIL_TRACE --> END([return])
    FAIL_VAL --> END
    FAIL_PIPE --> END
    FAIL_STOR --> END
    DONE --> END

    style PIPELINE fill:#d4edda,stroke:#28a745
    style STORAGE fill:#fff3cd,stroke:#ffc107
    style DONE fill:#d4edda,stroke:#28a745
```

> `storage` and `done` stages (yellow) are milliseconds long — effectively unobservable by a polling client.
> `metrics_extraction` stage exists in the schema but is **never set** in Iteration 1 (pipeline is opaque).

---

## 4. Task Runner Stage Flow (simplified — 3 stages)

```mermaid
flowchart TD
    START([run_task called]) --> SET_STARTED

    SET_STARTED["set_started()\nstatus: PENDING → RUNNING\nstage: acquiring_trace"]
    SET_STARTED --> TRACE

    TRACE["STAGE: acquiring_trace\ncopy file to workspace/exp/run/traces/\nvalidate: JSON array, non-empty"]
    TRACE -->|TraceIngestionError| FAIL_TRACE["set_failed()\nTRACE_NOT_FOUND\nTRACE_PARSE_ERROR"]

    TRACE -->|ok| UPD1["update_stage('running_pipeline')"]
    UPD1 --> PIPELINE

    PIPELINE["STAGE: running_pipeline\nasync with semaphore:\nawait run_pipeline()\n[bucketing + metrics extraction]\nread summary.json + build result"]
    PIPELINE -->|Exception| FAIL_PIPE["set_failed(error_code, stage, traceback)"]

    PIPELINE -->|ok| DONE["set_completed(result)\nstatus: RUNNING → COMPLETED"]

    FAIL_TRACE --> END([return])
    FAIL_PIPE --> END
    DONE --> END

    style PIPELINE fill:#d4edda,stroke:#28a745
    style DONE fill:#d4edda,stroke:#28a745
```

> Validation is folded into `acquiring_trace` (same duration, same failure class).
> Result dict is built inside `running_pipeline` (no observable `storage` stage needed).
> Terminal state is communicated by `status = COMPLETED/FAILED`, not a `done` stage.

---

## 5. Concurrency Architecture

```mermaid
flowchart TD
    subgraph PROCESS["uvicorn ASGI process"]
        subgraph LOOP["asyncio event loop (single thread)"]
            REQ["Request handler\nPOST /bucketing-extraction"]
            BG1["run_task(task_1)\nawaits semaphore"]
            BG2["run_task(task_2)\nawaits semaphore"]
            BG3["run_task(task_3)\nBLOCKED — semaphore full"]
        end

        subgraph SEM["Semaphore (default: 4 slots)"]
            S1[slot 1]
            S2[slot 2]
            S3[slot 3]
            S4[slot 4]
        end

        subgraph THREADS["ThreadPoolExecutor\n(asyncio default)"]
            T1["file I/O\n(shutil.copy, json.load)"]
            T2["Langfuse SDK calls\n(synchronous HTTP)"]
        end
    end

    REQ -->|"add_task(run_task)"| BG1
    BG1 -->|"acquire slot"| S1
    BG2 -->|"acquire slot"| S2
    BG3 -.->|"waits — status shows RUNNING"| SEM

    BG1 -->|"asyncio.to_thread()"| T1
    BG1 -->|"asyncio.to_thread()"| T2
    BG2 -->|"await run_pipeline()"| LOOP
```

**Key rules:**
- `run_task` is `async` — runs on the event loop, not in a thread.
- All blocking calls inside `run_task` must use `asyncio.to_thread()` (file I/O, Langfuse SDK).
- `run_pipeline()` is already `async def` — awaited directly, no thread needed.
- Semaphore is acquired *after* `set_started()` — tasks show `RUNNING` while waiting for a slot.

---

## 6. MongoDB Collection Ownership

```mermaid
flowchart LR
    subgraph API["API Layer (Iteration 1)"]
        POST["POST handler"]
        RUNNER["task_runner.py"]
        SESSION["session_service.py"]
    end

    subgraph PIPELINE["Existing Pipeline (unchanged)"]
        PHASE1["metrics_extractor/\nMongoDBClient"]
    end

    subgraph MONGO["MongoDB: agentcert"]
        PT[("pipeline_tasks\n✅ created at startup")]
        ARM[("agent_run_metrics\n⚠️ created on first write\nby existing MongoDBClient")]
        EM[("extraction_metadata\n🚫 Iteration 2 — skip")]
        FM[("fault_metadata\n🚫 Iteration 2 — skip")]
    end

    POST -->|"create_task()\nfind_active_task()"| SESSION
    RUNNER -->|"set_started()\nupdate_stage()\nset_completed()\nset_failed()"| SESSION
    SESSION -->|"always"| PT

    PHASE1 -->|"storage_config = mongodb | hybrid"| ARM

    style EM fill:#f8d7da,stroke:#dc3545
    style FM fill:#f8d7da,stroke:#dc3545
    style ARM fill:#fff3cd,stroke:#ffc107
    style PT fill:#d4edda,stroke:#28a745
```

---

## 7. App Startup Sequence

```mermaid
flowchart TD
    START([lifespan starts]) --> S1

    S1["1. Load Settings\npydantic BaseSettings\nreads env vars once"]
    S1 --> S2

    S2["2. Init Motor client\nAsyncIOMotorClient(MONGODB_URI)"]
    S2 --> S3

    S3["3. Create pipeline_tasks collection\nensure_index × 4\n(task_id unique, agent_exp_run,\nstatus_created, created_at)"]
    S3 --> S4

    S4["4. Store refs in app.state\napp.state.db\napp.state.tasks_col\napp.state.config\napp.state.settings"]
    S4 --> S5

    S5["5. Create concurrency semaphore\nasyncio.Semaphore(settings.max_concurrent_tasks)"]
    S5 --> S6

    S6["6. Ensure workspace root\nPath(settings.workspace_dir).mkdir()"]
    S6 --> READY([app ready])

    READY --> SHUTDOWN

    SHUTDOWN([SIGTERM / SIGINT]) --> SD1
    SD1["await pending BackgroundTasks\n(set --timeout-graceful-shutdown 300)"]
    SD1 --> SD2["close Motor client"]
    SD2 --> DONE([shutdown complete])
```

> Step 3 creates **only `pipeline_tasks`**. `agent_run_metrics` is owned by the existing `MongoDBClient`
> in `utils/mongodb_util.py`. `extraction_metadata` and `fault_metadata` are not created until Iteration 2.
