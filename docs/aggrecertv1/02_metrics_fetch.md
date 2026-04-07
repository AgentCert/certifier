# 02 — Metrics Discovery & Ingestion

## Responsibility

`main/services/cert_pipeline_service.py` validates that metrics exist before handing off to
the pipeline. The actual metrics reading is handled internally by `DirectoryQueryService`
(in `aggregator/scripts/aggregation.py`) when `run_pipeline()` is called.

The API layer's responsibility is limited to:
1. Verifying the `metrics_dir` exists on disk before creating a task.
2. Verifying that at least one `*metrics.json` file contains a document with the requested
   `agent_id` — ensuring the request will not silently produce an empty aggregation.

---

## Storage Config Types

| `storage_config.type` | Iteration 1 | Iteration 2 |
|---|---|---|
| `"local"` | Fully supported — `metrics_dir` field required | — |
| `"mongodb"` | Rejected at validation (HTTP 400) | Metrics queried from `agent_run_metrics` by `agent_id` + `experiment_id` |
| `"blob_storage"` | Rejected at validation (HTTP 400) | Metrics downloaded from Azure Blob container |
| `"hybrid"` | Rejected at validation (HTTP 400) | Blob + MongoDB write |

---

## Local Metrics Discovery (Iteration 1)

### Input

`storage_config.type = "local"` + `storage_config.metrics_dir` = absolute path to directory.

The directory must contain one or more files matching `*metrics.json`. These are produced by
Phase 1 (`metrics_extractor/`) and written to `workspace/{experiment_id}/{run_id}/metrics/`.

### Discovery algorithm

```python
import glob, json
from pathlib import Path

def discover_metrics_files(metrics_dir: str) -> list[Path]:
    """
    Recursively find all *metrics.json files in metrics_dir.
    Returns sorted list of absolute Paths.
    Raises MetricsNotFoundError if none found.
    """
    pattern = str(Path(metrics_dir) / "**" / "*metrics.json")
    files = sorted(glob.glob(pattern, recursive=True))
    if not files:
        raise MetricsNotFoundError(
            f"No *metrics.json files found in '{metrics_dir}'"
        )
    return [Path(f) for f in files]
```

### Agent-ID filter

After discovery, validate that at least one file contains a document with the requested
`agent_id`. This prevents a successful task creation followed by a silent empty aggregation:

```python
def validate_agent_metrics(files: list[Path], agent_id: str) -> int:
    """
    Load each file and check for agent_id match.
    Returns count of matching documents.
    Raises MetricsNotFoundError if count == 0.
    """
    count = 0
    for path in files:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and doc.get("agent_id") == agent_id:
                count += 1
            elif isinstance(doc, list):
                count += sum(1 for d in doc if isinstance(d, dict) and d.get("agent_id") == agent_id)
        except (json.JSONDecodeError, OSError):
            continue  # skip unreadable files; pipeline will surface the error
    if count == 0:
        raise MetricsNotFoundError(
            f"No metrics documents found for agent_id='{agent_id}' in '{metrics_dir}'"
        )
    return count
```

This validation runs synchronously inside the POST handler before the task is created.
It uses `asyncio.to_thread()` to avoid blocking the event loop.

---

## Format Contract

Each `*metrics.json` file must be a JSON object (dict) with at minimum:

```json
{
  "agent_id": "agent_v2_4_1",
  "experiment_id": "exp_001",
  "run_id": "run_001",
  "fault_category": "compute",
  "fault_name": "pod-delete",
  "quantitative": { ... },
  "qualitative": { ... }
}
```

The full schema is defined by `metrics_extractor/` and stored in `agent_run_metrics`. The
API validation layer only checks for `agent_id` presence; the aggregator performs full schema
validation internally.

---

## Error Handling

| Condition | Error Code | HTTP | When raised |
|---|---|---|---|
| `metrics_dir` does not exist on disk | `METRICS_NOT_FOUND` | 400 | Pre-task-creation validation |
| No `*metrics.json` files in directory (recursive) | `METRICS_NOT_FOUND` | 400 | Pre-task-creation validation |
| No documents match `agent_id` | `METRICS_NOT_FOUND` | 400 | Pre-task-creation validation |
| `storage_config.type` is not `"local"` | `INVALID_REQUEST` | 400 | Pydantic validation |
| File unreadable mid-pipeline | `AGGREGATION_FAILED` | — (async) | Inside `run_pipeline()` |

> Errors raised before task creation return HTTP 400/422 synchronously.
> Errors inside `run_pipeline()` are recorded in the `certification_tasks` document as
> `status=FAILED` and surfaced via the GET polling endpoint.

---

## Validation in the POST Handler

```
POST /api/v1/aggregation-certification
│
├── 1. Pydantic validation (automatic — returns 422 on type/field errors)
│
├── 2. storage_config.type check
│      if not "local": return 400 INVALID_REQUEST
│
├── 3. metrics_dir existence check
│      asyncio.to_thread(Path(metrics_dir).is_dir)
│      if False: return 400 METRICS_NOT_FOUND
│
├── 4. metrics file discovery
│      asyncio.to_thread(discover_metrics_files, metrics_dir)
│      if empty: return 400 METRICS_NOT_FOUND
│
├── 5. agent_id filter
│      asyncio.to_thread(validate_agent_metrics, files, agent_id)
│      if count == 0: return 400 METRICS_NOT_FOUND
│
├── 6. duplicate task check (see §04_session_management)
│      if active task exists for (agent_id, experiment_id): return 409
│
└── 7. create task + dispatch background worker → return 202
```

Steps 3–5 run inside `asyncio.to_thread()` because they touch the filesystem. Each is
bounded: discovery scales with the number of files (≤ O(N) reads of small JSON headers).
For a typical experiment with 30 runs × 4 faults = 120 files, this completes in < 500 ms.
