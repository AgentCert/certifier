# 09 — Implementation Plan

**Date**: 2026-04-07
**Iteration**: 1 (Phase 2+3 API wrapper)
**Status**: Specification complete — implementation pending

---

## What Needs to Be Built

A non-blocking REST API that wraps the existing
`run_aggregation_and_certification_pipeline.run_pipeline()` script. Clients submit an
`agent_id`, `experiment_id`, and a local `metrics_dir`; get a `cert_task_id` back immediately.
The pipeline runs in the background; clients poll a status endpoint until completion.

---

## Planned File Tree

```
main/                               ~700–850 lines total (estimate), zero changes to existing modules
│
├── main.py                         MODIFIED (+~40 lines)
│   └─ new router registration
│   └─ 3 new collection init helpers
│   └─ cert_semaphore creation
│   └─ workspace/cert/ mkdir
│
├── config/
│   └── settings.py                 MODIFIED (+~15 lines)
│       └─ 5 new fields (cert collections, cert workspace, cert concurrency)
│
├── models/
│   ├── cert_requests.py            NEW ~60 lines
│   │   └─ LocalCertStorageConfig
│   │   └─ AggregationCertificationRequest
│   └── cert_responses.py           NEW ~30 lines
│       └─ CertTaskAcceptedResponse
│       └─ CertTaskStatusResponse
│
├── services/
│   ├── cert_session_service.py     NEW ~120 lines
│   │   └─ CertSessionService (motor CRUD on certification_tasks)
│   └── cert_pipeline_service.py    NEW ~30 lines
│       └─ CertPipelineService (thin adapter over run_pipeline())
│
├── routers/
│   └── aggregation_certification.py  NEW ~110 lines
│       └─ POST /api/v1/aggregation-certification
│       └─ GET  /api/v1/cert-tasks/{cert_task_id}
│
└── workers/
    └── cert_task_runner.py         NEW ~160 lines
        └─ run_cert_task() (async background coroutine)
        └─ classify_cert_error()
        └─ resolve_cert_output_dir()
        └─ _write_certification_metadata()
        └─ _write_aggregated_category_metadata()
```

**Zero lines changed** in `fault_analyzer/`, `metrics_extractor/`, `aggregator/`,
`cert_builder/`, `utils/`, `run_bucketing_and_extraction_pipeline.py`,
`run_aggregation_and_certification_pipeline.py`.

---

## API Surface

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/aggregation-certification` | Submit cert job → 202 + cert_task_id |
| `GET` | `/api/v1/cert-tasks/{cert_task_id}` | Poll status → cert task document |

### POST request body

```json
{
  "agent_id": "agent_v2_4_1",
  "agent_name": "Agent V2.4.1",
  "experiment_id": "exp_001",
  "certification_run_id": "cert_run_001",
  "runs_per_fault": 30,
  "storage_config": {
    "type": "local",
    "metrics_dir": "/srv/projects/mas/mars/agent-cert/certifier/workspace/exp_001/run_001/metrics"
  }
}
```

### POST responses

| Code | Condition |
|---|---|
| 202 | Task accepted — `{ cert_task_id, poll_url }` |
| 400 | `METRICS_NOT_FOUND` — metrics_dir doesn't exist or no matching docs for agent_id |
| 400 | `INVALID_REQUEST` — storage_config.type not "local" |
| 409 | Duplicate PENDING/RUNNING task for same `(agent_id, experiment_id)` |
| 422 | Pydantic validation failure |
| 500 | MongoDB session creation failed |

### GET response (completed example)

```json
{
  "cert_task_id": "7c3a9f12-4b8e-41d6-a2f7-1c9e6d5b3a08",
  "status": "COMPLETED",
  "stage": "done",
  "agent_id": "agent_v2_4_1",
  "agent_name": "Agent V2.4.1",
  "experiment_id": "exp_001",
  "certification_run_id": "cert_run_001",
  "created_at": "2026-04-07T11:00:00Z",
  "started_at": "2026-04-07T11:00:02Z",
  "completed_at": "2026-04-07T11:04:15Z",
  "data": {
    "total_documents": 120,
    "total_fault_categories": 3,
    "fault_categories": ["compute", "network", "storage"],
    "certification_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "storage_paths": {
      "aggregated_scorecard": "workspace/cert/agent_v2_4_1/exp_001/aggregated_scorecard_output_agent_v2_4_1.json",
      "certification_report": "workspace/cert/agent_v2_4_1/exp_001/certification_report_agent_v2_4_1.json",
      "summary": "workspace/cert/agent_v2_4_1/exp_001/pipeline_summary.json"
    },
    "processing_time_seconds": 243.7
  }
}
```

---

## Task Lifecycle

```
POST received
  → 400 METRICS_NOT_FOUND  (if metrics_dir missing or no docs for agent_id)
  → 409 TASK_ALREADY_ACTIVE (if duplicate active task)
  → 202 Accepted (task queued)

Background:
  → PENDING  (session document created)
  → RUNNING  stage: fetching_metrics
             (semaphore acquired; run_pipeline() running)
  → RUNNING  stage: storing_metadata
             (pipeline done; writing MongoDB docs)
  → COMPLETED / FAILED
```

---

## MongoDB

Three new collections in database `agentcert`. All created at startup with indexes
(idempotent — `OperationFailure` code 85/86 skipped on restart).

| Collection | Indexes |
|---|---|
| `certification_tasks` | `cert_task_id` unique; `agent_id + experiment_id`; `status + created_at desc`; `created_at` |
| `certification_metadata` | `certification_id` unique; `agent_id + experiment_id`; `agent_id + created_at desc`; `certification_run_id` sparse |
| `aggregated_category_metadata` | `certification_id + fault_category` unique; `agent_id + experiment_id`; `created_at desc` |

---

## Workspace Layout

```
workspace/cert/
└── {agent_id}/
    └── {experiment_id}/
        ├── aggregated_scorecard_output_{agent_id}.json    ← Phase 2 output (AggregationOrchestrator)
        ├── certification_report_{agent_id}.json            ← Phase 3 output (CertificationPipeline)
        └── pipeline_summary.json                           ← Written by run_pipeline()
```

---

## Configuration

| Variable | Required | Default | Notes |
|---|---|---|---|
| `MONGODB_CONNECTION_STRING` | yes | — | Inherited from faultv1 |
| `MONGODB_DATABASE` | no | `agentcert` | Inherited from faultv1 |
| `CERT_TASK_COLLECTION` | no | `certification_tasks` | New |
| `CERT_METADATA_COLLECTION` | no | `certification_metadata` | New |
| `AGG_CATEGORY_COLLECTION` | no | `aggregated_category_metadata` | New |
| `CERT_WORKSPACE_DIR` | no | `workspace/cert` | New |
| `API_MAX_CONCURRENT_CERT_TASKS` | no | `2` | New |
| `AZURE_OPENAI_*` | yes | — | Required by AzureLLMClient (Phase 2+3 LLM calls) |

---

## Implementation Order

Recommended sequence to minimise integration risk:

1. **`main/config/settings.py`** — add 5 new fields (no dependencies)
2. **`main/models/cert_requests.py` + `cert_responses.py`** — Pydantic models (no dependencies)
3. **`main/services/cert_session_service.py`** — Motor CRUD (depends on settings)
4. **`main/services/cert_pipeline_service.py`** — Thin adapter (depends on run_pipeline import)
5. **`main/workers/cert_task_runner.py`** — Background coroutine (depends on all services)
6. **`main/routers/aggregation_certification.py`** — HTTP handlers (depends on all of the above)
7. **`main/main.py`** — Wire router + init new collections at startup
8. **Manual integration test** — POST with real metrics dir from faultv1 output, poll to COMPLETED

---

## Design Decisions

| Decision | Alternative | Chosen | Reason |
|---|---|---|---|
| Separate cert_semaphore | Share faultv1 semaphore | Separate (default 2) | Phase 2+3 runs are 3–10× heavier; sharing would starve Phase 0+1 tasks |
| Separate GET endpoint (/cert-tasks/) | Extend /tasks/ to check both collections | Separate | Cleaner; avoids a dual-collection lookup and status type ambiguity |
| Sync metrics validation before task creation | Defer to pipeline | Pre-creation validation | Fail fast (400) instead of creating a task that immediately fails |
| storage_config.type = "local" only | Support "mongodb" in iteration 1 | Local only | MongoDB metrics fetch adds substantial complexity; DirectoryQueryService already works |
| Workspace under workspace/cert/{agent_id}/{experiment_id}/ | workspace/{agent_id}/{experiment_id}/ | cert/ subdirectory | Avoids collision with faultv1 workspace which uses experiment_id at top level |

---

## Known Limitations (Iteration 1)

| # | Limitation | Mitigation for Iteration 2 |
|---|---|---|
| 1 | `storage_config.type = "local"` only; no MongoDB or blob metrics fetch | Add MetricsQueryService branch (MongoDB) + AzureBlobService branch |
| 2 | No HTML/PDF report rendering | Add Jinja2 HTML template rendering + WeasyPrint PDF in cert_task_runner.py |
| 3 | Stage stuck at `fetching_metrics` during entire pipeline run (opaque call) | Split run_pipeline into aggregation + certification steps; add stage callback |
| 4 | Tasks show RUNNING while waiting for cert_semaphore (no QUEUED state) | Add QUEUED status (consistent with faultv1 iteration 2 plan) |
| 5 | Stale RUNNING tasks if process killed mid-pipeline | Startup recovery sweep: scan RUNNING cert tasks older than N min, mark FAILED |
| 6 | No blob storage upload of artifacts | Add AzureBlobStorageService in storing_artifacts stage |
| 7 | AggregatedCategoryMetadata written from JSON file (fragile) | Read directly from aggregated scorecard Pydantic model return value once CertificationScorecard is imported from aggregator.schema |

---

## Integration Test Plan

After implementation, verify the following scenarios manually:

| Test | Expected result |
|---|---|
| POST with non-existent `metrics_dir` | HTTP 400 `METRICS_NOT_FOUND` |
| POST with valid dir but no docs for `agent_id` | HTTP 400 `METRICS_NOT_FOUND` |
| POST with `storage_config.type = "mongodb"` | HTTP 400 `INVALID_REQUEST` |
| POST with valid metrics dir, real `agent_id` | HTTP 202 + cert_task_id |
| Duplicate POST while first task is RUNNING | HTTP 409 `TASK_ALREADY_ACTIVE` |
| GET with unknown cert_task_id | HTTP 404 `TASK_NOT_FOUND` |
| Full pipeline run — poll to COMPLETED | `certification_report_{agent_id}.json` written to workspace/cert/ |
| Full pipeline run — MongoDB docs | 1 `certification_metadata` doc + N `aggregated_category_metadata` docs |
