# 08 — Flow Diagrams

---

## 1. API Request Lifecycle (Client View)

```mermaid
sequenceDiagram
    participant C as Client
    participant API as FastAPI
    participant FS as Filesystem<br/>(metrics_dir)
    participant DB as MongoDB<br/>(certification_tasks)
    participant BG as Background Worker

    C->>API: POST /api/v1/aggregation-certification
    API->>API: Pydantic validation
    API->>FS: discover_metrics_files(metrics_dir)
    FS-->>API: [list of *metrics.json paths]
    API->>FS: validate_agent_metrics(files, agent_id)
    FS-->>API: count (N matching docs)
    alt count == 0 or dir missing
        API-->>C: 400 METRICS_NOT_FOUND
    else metrics found
        API->>DB: find_active_task(agent_id, experiment_id)
        alt duplicate active task
            DB-->>API: existing task doc
            API-->>C: 409 TASK_ALREADY_ACTIVE
        else no active task
            DB-->>API: null
            API->>DB: create_task() → PENDING
            API->>BG: schedule run_cert_task() (non-blocking)
            API-->>C: 202 Accepted { cert_task_id, poll_url }
        end
    end

    loop poll until terminal
        C->>API: GET /api/v1/cert-tasks/{cert_task_id}
        API->>DB: get_task(cert_task_id)
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
- `set_completed` / `set_failed` filter on `status = RUNNING` before writing; raise `ValueError`
  if no match (prevents double-write race).
- `set_failed` is safe to call even if `status = PENDING` (handles early failures before
  `set_started` fires).

---

## 3. Task Runner Stage Flow

```mermaid
flowchart TD
    START([run_cert_task called]) --> SET_STARTED

    SET_STARTED["set_started()\nstatus: PENDING → RUNNING\nstage: fetching_metrics"]
    SET_STARTED --> PIPELINE_BLOCK

    subgraph SEM["async with cert_semaphore"]
        PIPELINE["run_pipeline()\n• DirectoryQueryService reads *metrics.json\n• AggregationOrchestrator (Phase 2)\n  numeric stats + LLM Council\n• CertificationPipeline (Phase 3)\n  12-section report generation\nwrites aggregated_scorecard.json\nwrites certification_report.json\nwrites pipeline_summary.json"]
    end

    PIPELINE_BLOCK["[Acquire cert_semaphore]\nstart_time = now()"] --> SEM
    SEM -->|empty result\nno docs for agent_id| FAIL_EMPTY["set_failed()\nMETRICS_NOT_FOUND"]
    SEM -->|Exception| FAIL_PIPE["set_failed()\nAGGREGATION_FAILED\nCERT_GENERATION_FAILED\nPIPELINE_FAILED"]

    SEM -->|ok| UPD1["update_stage('storing_metadata')\nelapsed = now() - start_time"]
    UPD1 --> METADATA

    METADATA["STAGE: storing_metadata\nRead pipeline_summary.json\nGenerate certification_id (UUID)\nInsert CertificationMetadata doc\nInsert N AggregatedCategoryMetadata docs"]
    METADATA -->|MotorError / IOError| FAIL_META["set_failed()\nSTORAGE_ERROR"]

    METADATA -->|ok| DONE["set_completed(result_dict)\nstatus: RUNNING → COMPLETED\nstage: done"]

    FAIL_EMPTY --> END([return])
    FAIL_PIPE --> END
    FAIL_META --> END
    DONE --> END

    style SEM fill:#d4edda,stroke:#28a745
    style DONE fill:#d4edda,stroke:#28a745
    style METADATA fill:#fff3cd,stroke:#ffc107
```

> The entire `run_pipeline()` call is in one semaphore-guarded block. Stage visible to pollers
> during pipeline execution is `fetching_metrics` (not split between aggregation and certification
> in iteration 1). `storing_metadata` and `done` transitions are milliseconds to a few seconds.

---

## 4. Phase 2+3 Pipeline Internals (inside `run_pipeline()`)

```mermaid
flowchart TD
    subgraph PIPELINE["run_pipeline() — opaque to API layer"]
        DQ["DirectoryQueryService\nGlob *metrics.json files\nFilter by agent_id\nReturn List[PerRunMetrics]"]
        DQ --> AO

        AO["AggregationOrchestrator.aggregate_all()\n• compute_numeric_aggregates() per fault_category\n• compute_derived_rates() per fault_category\n• compute_boolean_aggregates() per fault_category\n• LLMCouncil.synthesize() — async LLM calls\n• ScorecardAssembler.assemble()"]
        AO --> SCORE

        SCORE["Write aggregated_scorecard_output_{agent_id}.json"]
        SCORE --> CP

        CP["CertificationPipeline.run()\n1. Ingestion: parse scorecard\n2. Computation: build 12 sections\n   (cards, tables, charts, assessments)\n3. Narratives: concurrent LLM generation\n   (5 builders via asyncio.gather)\n4. Assembly: merge + validate Pydantic schema"]
        CP --> REPORT

        REPORT["Write certification_report_{agent_id}.json\nWrite pipeline_summary.json"]
    end
```

---

## 5. Concurrency Architecture

```mermaid
flowchart TD
    subgraph PROCESS["uvicorn ASGI process"]
        subgraph LOOP["asyncio event loop (single thread)"]
            REQ["POST /aggregation-certification"]
            GET["GET /cert-tasks/{id}"]
            BG1["run_cert_task(task_1)\nawaits cert_semaphore"]
            BG2["run_cert_task(task_2)\nBLOCKED — semaphore full"]
            FV1["run_task(faultv1_task_1)\nawaits semaphore"]
        end

        subgraph SEM1["Semaphore (max_concurrent_tasks: 4)\nfor Phase 0+1 tasks"]
            S1a[slot 1]
            S1b[slot 2]
            S1c[slot 3]
            S1d[slot 4]
        end

        subgraph SEM2["cert_semaphore (max_concurrent_cert_tasks: 2)\nfor Phase 2+3 tasks"]
            S2a[slot 1]
            S2b[slot 2]
        end
    end

    REQ -->|"add_task(run_cert_task)"| BG1
    BG1 -->|"acquire slot"| S2a
    BG2 -.->|"waits"| SEM2

    FV1 -->|"acquire slot"| S1a
```

**Key rule**: `cert_semaphore` and `semaphore` are independent. A Phase 2+3 task waiting for
`cert_semaphore` does not block Phase 0+1 tasks from acquiring `semaphore`, and vice versa.

---

## 6. MongoDB Collection Ownership

```mermaid
flowchart LR
    subgraph API["API Layer"]
        POST1["POST /bucketing-extraction\n(faultv1)"]
        POST2["POST /aggregation-certification\n(aggrecertv1)"]
        RUNNER1["task_runner.py\n(faultv1)"]
        RUNNER2["cert_task_runner.py\n(aggrecertv1)"]
        SESSION1["session_service.py"]
        SESSION2["cert_session_service.py"]
    end

    subgraph PIPELINE["Existing Pipeline (unchanged)"]
        PHASE1["metrics_extractor/\nMongoDBClient"]
    end

    subgraph MONGO["MongoDB: agentcert"]
        PT[("pipeline_tasks\n✅ faultv1 startup")]
        ARM[("agent_run_metrics\n⚠️ existing MongoDBClient")]
        CT[("certification_tasks\n✅ aggrecertv1 startup")]
        CM[("certification_metadata\n✅ aggrecertv1 startup")]
        AC[("aggregated_category_metadata\n✅ aggrecertv1 startup")]
    end

    POST1 --> SESSION1 --> PT
    RUNNER1 --> SESSION1

    POST2 --> SESSION2 --> CT
    RUNNER2 --> SESSION2
    RUNNER2 -->|"storing_metadata stage"| CM
    RUNNER2 -->|"storing_metadata stage"| AC

    PHASE1 -->|"storage_config = mongodb | hybrid"| ARM

    style ARM fill:#fff3cd,stroke:#ffc107
```

---

## 7. Extended App Startup Sequence

```mermaid
flowchart TD
    START([lifespan starts]) --> S1

    S1["1. Load Settings\nread env vars → Settings dataclass"]
    S1 --> S2

    S2["2. Load app config\nConfigLoader.load_config()"]
    S2 --> S3

    S3["3. Init Motor client\nAsyncIOMotorClient(MONGODB_URI)"]
    S3 --> S4

    S4["4. Create pipeline_tasks indexes\n(faultv1 — unchanged)\n4 indexes"]
    S4 --> S5

    S5["5. Create certification_tasks indexes\n4 indexes\n(cert_task_id unique, agent_exp,\nstatus_created, created_at)"]
    S5 --> S6

    S6["6. Create certification_metadata indexes\n4 indexes\n(certification_id unique, agent_exp,\nagent_created, run_id sparse)"]
    S6 --> S7

    S7["7. Create aggregated_category_metadata indexes\n3 indexes\n(cert_fault unique, agent_exp, created_at)"]
    S7 --> S8

    S8["8. Store refs in app.state\ndb, tasks_col, cert_tasks_col,\ncert_meta_col, agg_cat_col,\nconfig, settings"]
    S8 --> S9

    S9["9. Create semaphores\nasyncio.Semaphore(max_concurrent_tasks)\nasyncio.Semaphore(max_concurrent_cert_tasks)"]
    S9 --> S10

    S10["10. Ensure workspace dirs\nworkspace/\nworkspace/cert/"]
    S10 --> S11

    S11["11. Register routers\n/api/v1/bucketing-extraction (faultv1)\n/api/v1/aggregation-certification (new)\n/api/v1/cert-tasks/{id} (new)"]
    S11 --> READY([app ready])

    READY --> SHUTDOWN
    SHUTDOWN([SIGTERM / SIGINT]) --> SD1
    SD1["await pending BackgroundTasks\n(--timeout-graceful-shutdown 600)"]
    SD1 --> SD2["close Motor client"]
    SD2 --> DONE([shutdown complete])
```
