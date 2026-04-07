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

**This document series specifies iteration 1 of the Phase 2+3 HTTP API wrapper** — the
`POST /api/v1/aggregation-certification` endpoint. It extends the `main/` API layer built in
faultv1 to add aggregation, certification, and artifact storage.

---

## Iteration 1 Goals

| # | Goal | Definition of Done |
|---|---|---|
| G1 | Accept local metrics directory | Given a directory containing `*metrics.json` files, the API discovers all per-run metrics for the specified `agent_id` |
| G2 | Run Phase 2+3 pipeline asynchronously | POST returns immediately; aggregation + certification runs in the background |
| G3 | Track cert task progress in MongoDB | A `certification_tasks` collection records every task's status, stage, result, and error |
| G4 | Poll cert task status | GET endpoint returns live status + full result on completion |
| G5 | Persist all artifacts locally | Aggregated scorecard, certification report, and summary land in `workspace/cert/{agent_id}/{experiment_id}/` |
| G6 | Store certification metadata to MongoDB | On success, write one `CertificationMetadata` document and N `AggregatedCategoryMetadata` documents (one per fault category) |
| G7 | All new MongoDB collections initialised at startup | `certification_tasks`, `certification_metadata`, `aggregated_category_metadata` created and indexed at startup |

---

## Out of Scope for Iteration 1

- Azure Blob Storage upload of artifacts (deferred to iteration 2)
- `storage_config.type = "mongodb"` metrics fetch — metrics must be in a local directory (deferred to iteration 2)
- `storage_config.type = "blob_storage"` metrics fetch (deferred to iteration 2)
- HTML/PDF report rendering (deferred to iteration 2)
- Authentication / API key enforcement
- Retry logic for failed tasks
- Task cancellation

---

## Architectural Constraints

1. **Zero changes to existing modules.** `fault_analyzer/`, `metrics_extractor/`, `aggregator/`,
   `cert_builder/`, `utils/`, and the two pipeline runner scripts are read-only. All new code lives
   in `main/`.

2. **Non-blocking by design.** Aggregation involves multiple sequential LLM Council calls plus
   cert_builder narrative generation — often 60–300 seconds per run. No HTTP client should wait
   for completion. The POST endpoint returns a `cert_task_id` in ≤ 200 ms; progress is polled
   separately.

3. **One output directory per cert task.** The workspace path
   `workspace/cert/{agent_id}/{experiment_id}/` is the task's exclusive write scope. Two tasks
   with the same `(agent_id, experiment_id)` pair will conflict on disk. The API must reject
   duplicate submissions where a live task (status = PENDING or RUNNING) already exists for
   that pair.

4. **Motor for all new async MongoDB writes.** All API-layer writes run inside the asyncio
   event loop and use `motor` (async pymongo driver, already in `requirements.txt`).

5. **Config loaded once at startup.** The resolved config dict is stored in `app.state` and
   injected via FastAPI dependency. No module re-reads config at request time.

6. **`run_pipeline()` is the only pipeline entry point.** The API adapter calls
   `run_aggregation_and_certification_pipeline.run_pipeline()` exactly as the CLI does. No
   internal aggregator or cert_builder APIs are called directly from `main/`.

---

## Terminology

| Term | Meaning |
|---|---|
| Cert task | A single invocation of the Phase 2+3 pipeline, tracked by `cert_task_id` (UUID) |
| Stage | Fine-grained step within a cert task: `fetching_metrics → running_pipeline → done` |
| Metrics directory | Local directory containing per-run `*metrics.json` files produced by Phase 1 |
| Aggregated scorecard | `aggregated_scorecard_output_{agent_id}.json` — output of Phase 2 |
| Certification report | `certification_report_{agent_id}.json` — output of Phase 3 |
| Fault category | A grouping of semantically related faults (e.g., `compute`, `network`, `storage`) |

---

## Document Index

| Doc | Topic |
|---|---|
| `01_folder_structure.md` | New files added to `main/`, workspace layout, Python package wiring |
| `02_metrics_fetch.md` | Local metrics discovery, format contract, validation rules |
| `03_app_startup.md` | Extended settings, new collection initialisation, startup sequence |
| `04_session_management.md` | CertificationTask schema, state machine, CRUD interface |
| `05_api_endpoints.md` | POST + GET request/response schemas, validation, error codes |
| `06_task_runner.md` | Concurrency model, stage progression, artifact layout |
| `07_mongodb_schema.md` | All three new collection schemas, field rules, indexes, write ownership |
| `08_flow_diagrams.md` | Sequence, state machine, stage flow, concurrency, startup diagrams |
| `09_implementation.md` | Implementation plan, file tree, known limitations |
