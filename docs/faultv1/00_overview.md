# 00 — System Overview & Iteration 1 Goals

## Context

AgentCert is a four-phase analytical pipeline that ingests raw Langfuse telemetry from an AI
agent under Kubernetes fault injection and produces a structured certification report.

The four phases already exist as working Python modules:

| Phase | Module | Function |
|---|---|---|
| 0 | `fault_analyzer/` | Classify interleaved trace events into per-fault lifecycle buckets |
| 1 | `metrics_extractor/` | Extract quantitative + qualitative metrics per fault bucket |
| 2 | `aggregator/` | Aggregate metrics across N runs via stats + LLM Council |
| 3 | `cert_builder/` | Build 12-section certification report |

Pipeline scripts `run_bucketing_and_extraction_pipeline.py` and
`run_aggregation_and_certification_pipeline.py` wire these phases together as CLI tools.

**This document series specifies iteration 1 of an HTTP API wrapper** that exposes the
Phase 0+1 pipeline (bucketing + extraction) as a non-blocking REST endpoint.

---

## Iteration 1 Goals

| # | Goal | Definition of Done |
|---|---|---|
| G1 | Fetch traces from Langfuse | Given Langfuse credentials + query params, the API fetches observations from the Langfuse server, normalises them, and saves them to the local workspace |
| G2 | Accept pre-dumped trace files | Given a local file path, the API reads the file and validates its format — no Langfuse call needed |
| G3 | Run Phase 0+1 pipeline asynchronously | POST returns immediately; pipeline runs in the background |
| G4 | Track task progress in MongoDB | A `pipeline_tasks` collection records every task's status, stage, result, and error |
| G5 | Poll task status | GET endpoint returns live status + full result on completion |
| G6 | Persist all artifacts locally | Fault buckets, metrics, summary, and log land in `workspace/` |
| G7 | All MongoDB collections initialised at startup | Application startup creates all collections and indexes; no lazy init |

---

## Out of Scope for Iteration 1

- Azure Blob Storage upload (deferred to iteration 2)
- `ExtractionMetadata` and `FaultMetadata` collection population (schema created at startup; writes deferred)
- Authentication / API key enforcement
- Phase 2+3 API endpoint (aggregation → certification)
- Retry logic for failed tasks
- Task cancellation

---

## Architectural Constraints

1. **Zero changes to existing modules.** `fault_analyzer/`, `metrics_extractor/`, `aggregator/`,
   `cert_builder/`, `utils/`, and the two pipeline runner scripts are read-only. All new code lives
   in `main/`.

2. **Non-blocking by design.** Every pipeline run involves multiple sequential LLM calls (seconds
   each). No HTTP client should ever wait for pipeline completion. The POST endpoint returns a
   `task_id` in ≤ 200 ms; progress is polled separately.

3. **One output directory per task.** The workspace path
   `workspace/{experiment_id}/{run_id}/` is the task's exclusive write scope. Two tasks with the
   same `(experiment_id, run_id)` pair will conflict on disk. The API must reject duplicate
   submissions where a live task (status = PENDING or RUNNING) already exists for that pair.

4. **Motor for all new async MongoDB writes.** The existing `MongoDBClient` in `utils/` is
   synchronous (pymongo). New API-layer writes run inside the asyncio event loop and must use
   `motor` (async pymongo driver, already in `requirements.txt`) to avoid blocking the loop.

5. **Config loaded once at startup.** `ConfigLoader.load_config()` reads `configs/configs.json`
   and resolves `ENV_*` variables. The resolved config dict is stored in `app.state` and injected
   via FastAPI dependency. No module re-reads the config file at request time.

---

## Terminology

| Term | Meaning |
|---|---|
| Task | A single invocation of the Phase 0+1 pipeline, tracked by `task_id` (UUID) |
| Stage | Fine-grained step within a task: `trace_fetch → validation → bucketing → metrics_extraction → storage → done` |
| Workspace | Local directory tree under `certifier/workspace/` holding all task artifacts |
| Trace | A list of Langfuse observation dicts (spans) covering one agent run |
| Fault bucket | A subset of trace events attributed to one specific fault, produced by Phase 0 |
| Extraction result | Quantitative + qualitative metrics for one fault, produced by Phase 1 |

---

## Document Index

| Doc | Topic |
|---|---|
| `01_folder_structure.md` | Complete directory layout and Python package wiring |
| `02_trace_ingestion.md` | Langfuse fetch, file fallback, format contract, validation |
| `03_app_startup.md` | FastAPI lifespan, settings, MongoDB collection init |
| `04_session_management.md` | PipelineTask schema, state machine, CRUD interface |
| `05_api_endpoints.md` | POST + GET request/response schemas, validation, error codes |
| `06_task_runner.md` | Concurrency model, stage progression, artifact layout |
| `07_mongodb_schema.md` | All four collection schemas, field rules, indexes, write ownership |
