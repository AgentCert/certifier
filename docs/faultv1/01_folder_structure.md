# 01 — Folder Structure

## Complete Layout

```
certifier/                                         ← repo root (Python sys.path anchor)
│
├── main/                                          ← NEW: entire application layer
│   ├── main.py                                    ← FastAPI app factory + uvicorn entry point
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py                            ← Settings dataclass; resolves env vars once
│   │
│   ├── routers/
│   │   ├── __init__.py
│   │   └── bucketing_extraction.py                ← POST /bucketing-extraction + GET /tasks/{id}
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── requests.py                            ← BucketingExtractionRequest (Pydantic v2)
│   │   └── responses.py                           ← TaskAccepted, TaskStatusResponse, etc.
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── trace_service.py                       ← Langfuse fetch OR local file read
│   │   ├── pipeline_service.py                    ← Adapter over run_bucketing_and_extraction_pipeline.run_pipeline()
│   │   └── session_service.py                     ← Async CRUD on pipeline_tasks collection (motor)
│   │
│   └── workers/
│       ├── __init__.py
│       └── task_runner.py                         ← Async background coroutine; orchestrates stages
│
├── workspace/                                     ← NEW: runtime artifact root (add to .gitignore)
│   └── {experiment_id}/
│       └── {run_id}/
│           ├── traces/                            ← raw trace saved here (Langfuse or file copy)
│           │   └── raw_trace.json
│           ├── fault_buckets/                     ← Phase 0 output (FaultBucketingPipeline)
│           │   └── bucket_{fault_id}.json
│           ├── metrics/                           ← Phase 1 output (TraceMetricsExtractor)
│           │   ├── {safe_name}_trace.json             (ephemeral; kept for debug)
│           │   ├── {safe_name}_fault_config.json      (ephemeral; kept for debug)
│           │   └── {safe_name}_metrics.json
│           ├── pipeline_summary.json              ← written by run_pipeline() at the end
│           └── pipeline.log                       ← stderr/stdout capture of pipeline run
│
├── fault_analyzer/                                ← UNCHANGED
├── metrics_extractor/                             ← UNCHANGED
├── aggregator/                                    ← UNCHANGED
├── cert_builder/                                  ← UNCHANGED
├── utils/                                         ← UNCHANGED
├── configs/configs.json                           ← UNCHANGED
├── run_bucketing_and_extraction_pipeline.py       ← UNCHANGED (imported as a library)
├── run_aggregation_and_certification_pipeline.py  ← UNCHANGED
└── requirements.txt                               ← UNCHANGED (langfuse already present via agent-framework)
```

---

## Python Package Wiring

`certifier/` is the working directory when running the application. It is the `sys.path` root. This
means all existing absolute imports (`from fault_analyzer import ...`, `from utils.load_config import ...`)
continue to resolve without modification.

The new `main/` package adds one import path:

```python
# main/main.py
from main.config.settings import get_settings          # own package
from main.routers.bucketing_extraction import router   # own package
from run_bucketing_and_extraction_pipeline import run_pipeline  # certifier root
from utils.load_config import ConfigLoader             # certifier root
```

`main/` is a regular Python package (`main/__init__.py` can be empty). No `setup.py` or
`pyproject.toml` is needed for iteration 1; the working-directory import convention is
sufficient.

> **Critical gap**: If `main/` is ever moved or the working directory changes, all imports will
> break. For production deployment, wrap `certifier/` in a proper installable package.

---

## Start Command

```bash
# From certifier/ root
python -m main.main
# OR
uvicorn main.main:app --host 0.0.0.0 --port 8000 --reload
```

`main/main.py` exposes the ASGI app as the module-level name `app` so both invocation styles work.

---

## What Does NOT Change

Every file outside `main/` and `workspace/` is read-only. The pipeline modules are called as
libraries. If a pipeline module has a bug, it is fixed in that module — the API layer never
patches or monkey-patches existing code.

---

## .gitignore Additions

```
workspace/
*.pyc
__pycache__/
```
