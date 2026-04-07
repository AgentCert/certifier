# 01 — Folder Structure

## Complete Layout

```
certifier/                                                  ← repo root (Python sys.path anchor)
│
├── main/                                                   ← EXTENDED: new files added to existing layer
│   ├── main.py                                             ← MODIFIED: add new router + 3 new collections at startup
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py                                     ← MODIFIED: add 3 new collection name fields
│   │
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── bucketing_extraction.py                         ← UNCHANGED (faultv1)
│   │   └── aggregation_certification.py                    ← NEW: POST /aggregation-certification + GET /cert-tasks/{id}
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── requests.py                                     ← UNCHANGED (faultv1)
│   │   ├── responses.py                                    ← UNCHANGED (faultv1)
│   │   ├── cert_requests.py                                ← NEW: AggregationCertificationRequest (Pydantic v2)
│   │   └── cert_responses.py                               ← NEW: CertTaskAcceptedResponse, CertTaskStatusResponse
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── session_service.py                              ← UNCHANGED (faultv1)
│   │   ├── trace_service.py                                ← UNCHANGED (faultv1)
│   │   ├── pipeline_service.py                             ← UNCHANGED (faultv1)
│   │   ├── cert_session_service.py                         ← NEW: Async CRUD on certification_tasks (motor)
│   │   └── cert_pipeline_service.py                        ← NEW: Adapter over run_aggregation_and_certification_pipeline.run_pipeline()
│   │
│   └── workers/
│       ├── __init__.py
│       ├── task_runner.py                                  ← UNCHANGED (faultv1)
│       └── cert_task_runner.py                             ← NEW: Async background coroutine for Phase 2+3
│
├── workspace/                                              ← EXTENDED: new cert subdirectory
│   ├── {experiment_id}/                                    ← Phase 0+1 workspace (faultv1, unchanged)
│   │   └── {run_id}/
│   │       ├── traces/
│   │       ├── fault_buckets/
│   │       └── metrics/
│   │
│   └── cert/                                               ← NEW: Phase 2+3 workspace root
│       └── {agent_id}/
│           └── {experiment_id}/
│               ├── aggregated_scorecard_output_{agent_id}.json   ← Phase 2 output
│               ├── certification_report_{agent_id}.json          ← Phase 3 output
│               ├── pipeline_summary.json                         ← Written by run_pipeline()
│               └── pipeline.log                                  ← stderr/stdout capture
│
├── fault_analyzer/                                         ← UNCHANGED
├── metrics_extractor/                                      ← UNCHANGED
├── aggregator/                                             ← UNCHANGED
├── cert_builder/                                           ← UNCHANGED
├── utils/                                                  ← UNCHANGED
├── configs/configs.json                                    ← UNCHANGED
├── run_bucketing_and_extraction_pipeline.py                ← UNCHANGED
├── run_aggregation_and_certification_pipeline.py           ← UNCHANGED (imported as a library)
└── requirements.txt                                        ← UNCHANGED
```

---

## Python Package Wiring

`certifier/` is the working directory when running the application. It is the `sys.path` root.
All existing absolute imports continue to resolve without modification.

The new files add two import paths:

```python
# main/routers/aggregation_certification.py
from main.models.cert_requests import AggregationCertificationRequest
from main.models.cert_responses import CertTaskAcceptedResponse, CertTaskStatusResponse
from main.services.cert_session_service import CertSessionService
from main.workers.cert_task_runner import run_cert_task
from run_aggregation_and_certification_pipeline import run_pipeline  # certifier root
```

```python
# main/workers/cert_task_runner.py
from main.services.cert_session_service import CertSessionService
from main.services.cert_pipeline_service import CertPipelineService
from run_aggregation_and_certification_pipeline import run_pipeline  # certifier root
```

---

## Start Command

No change to the start command. The same `main.main:app` ASGI application now exposes both APIs:

```bash
# From certifier/
python -m main.main
# OR
uvicorn main.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## What Does NOT Change

Every file outside `main/` and `workspace/` remains read-only. The pipeline modules are
called as libraries. No existing faultv1 code in `main/` changes — new files are added
alongside existing ones.

---

## .gitignore

No changes required. `workspace/` is already excluded. The new `workspace/cert/` subtree
is covered by the existing `workspace/` ignore rule.
