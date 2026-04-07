# 02 — Trace Ingestion

## Responsibility

`main/services/trace_service.py` is the single component responsible for acquiring a raw Langfuse
trace and delivering it as a local file path that `run_pipeline()` can consume.

It abstracts two acquisition modes behind one interface so the task runner does not care where the
trace came from.

---

## Trace Source Types

The request body carries a `trace_source` object with a `type` discriminator:

### Type A — `"file"` (local file path)

```json
{
  "trace_source": {
    "type": "file",
    "file_path": "/absolute/path/to/trace.json"
  }
}
```

The file must already exist on the server filesystem. The service copies it into the workspace so
the pipeline always reads from a stable, task-scoped location:

```
workspace/{experiment_id}/{run_id}/traces/raw_trace.json
```

Copying (not moving) preserves the original in case of re-runs.

### Type B — `"langfuse"` (live fetch)

```json
{
  "trace_source": {
    "type": "langfuse",
    "base_url": "http://100.78.130.20:3001",
    "public_key": "pk-lf-...",
    "secret_key": "sk-lf-...",
    "from_timestamp": "2026-04-07T05:00:00Z",
    "page_size": 100,
    "max_pages": 20,
    "include_observations": true
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `base_url` | yes | — | Langfuse server URL |
| `public_key` | yes | — | Langfuse project public key |
| `secret_key` | yes | — | Langfuse project secret key |
| `from_timestamp` | no | 24 h ago | ISO-8601 UTC lower bound — narrows the Langfuse query; does **not** replace metadata filtering |
| `page_size` | no | 100 | Observations per page |
| `max_pages` | no | 20 | Hard cap on pages fetched |
| `include_observations` | no | true | Whether to fetch per-trace spans |

Filtering is done server-side via Langfuse's `filter` parameter (see Fetch Logic). `from_timestamp`
is an optional additional constraint included in the server-side filter to narrow the timestamp
range and reduce pages scanned.

> **Security note**: `public_key` and `secret_key` travel in the request body. The endpoint
> must be deployed behind TLS. In a future iteration these should be stored in KeyVault and
> referenced by a named credential, not sent per-request.

---

## Fetch Logic (Type B)

Langfuse v4 SDK's `trace.list()` supports a `filter` parameter — a JSON-serialised array of
filter conditions evaluated **server-side**. The `metadata` column accepts `stringObject` type
filters that match on specific metadata keys, and metadata filtering uses skip indexes for
performance.

This means `experiment_id` and `run_id` are pushed down as a single API call with no
client-side filtering step.

```
trace_service.fetch_and_save(source, workspace_traces_dir, experiment_id, run_id)
│
├── Build server-side filter JSON
│     filter = [
│       {"type": "stringObject", "column": "metadata", "key": "experiment_id",
│        "operator": "=", "value": experiment_id},
│       {"type": "stringObject", "column": "metadata", "key": "run_id",
│        "operator": "=", "value": run_id},
│       # optional: narrow timestamp window if from_timestamp is provided
│       {"type": "datetime", "column": "timestamp",
│        "operator": ">=", "value": from_timestamp}   ← only when set
│     ]
│     Note: when `filter` is passed, it takes precedence over all query-param filters
│     (sessionId, userId, fromTimestamp, etc.) — all constraints must live in the filter array.
│
├── Instantiate Langfuse(public_key, secret_key, host=base_url)
│
├── Paginate: client.api.trace.list(filter=json.dumps(filter), limit=page_size, page=N)
│     Stop when: response.data is empty OR page >= response.meta.total_pages OR page >= max_pages
│     Each page returns only traces matching both metadata keys (server-filtered).
│
├── If total traces returned == 0 → raise TraceIngestionError("TRACE_NOT_FOUND")
│
├── For each matched trace: fetch observations
│     client.api.legacy.observations_v1.get_many(trace_id=trace.id, limit=500)
│
├── Call format_observations(observations) on all collected observations
│     └── Normalises each span: {id, type, name, startTime, endTime, depth, input, output, metadata}
│     └── Sorts by (depth, startTime)
│
├── Flatten into one list
│
├── Write to workspace/{experiment_id}/{run_id}/traces/raw_trace.json
└── Return absolute file path
```

### Langfuse Trace Metadata Contract

Every Langfuse trace produced by the agent under test **must** carry these fields in its
`metadata` dict at trace creation time:

```json
{
  "metadata": {
    "experiment_id": "exp_001",
    "run_id": "run_001"
  }
}
```

If these fields are absent, the server-side filter returns zero results and the task fails with
`TRACE_NOT_FOUND`. This is a hard contract between the agent instrumentation and the certifier —
it cannot be patched at the API level.

### Filter Construction in Code

```python
import json

def build_langfuse_filter(experiment_id: str, run_id: str, from_timestamp: str | None) -> str:
    conditions = [
        {"type": "stringObject", "column": "metadata",
         "key": "experiment_id", "operator": "=", "value": experiment_id},
        {"type": "stringObject", "column": "metadata",
         "key": "run_id", "operator": "=", "value": run_id},
    ]
    if from_timestamp:
        conditions.append(
            {"type": "datetime", "column": "timestamp",
             "operator": ">=", "value": from_timestamp}
        )
    return json.dumps(conditions)
```

The returned string is passed directly as the `filter=` argument to `client.api.trace.list()`.

---

## Output Format Contract

Both type A and type B must produce a file whose content is a JSON array of observation dicts:

```json
[
  {
    "id": "span-uuid",
    "type": "SPAN | GENERATION | EVENT",
    "name": "string",
    "startTime": "2026-04-07T10:00:00.000Z",
    "endTime": "2026-04-07T10:00:01.234Z",
    "depth": 0,
    "input": "string | null",
    "output": "string | null",
    "metadata": "string | null"
  }
]
```

`input`, `output`, and `metadata` are **JSON-encoded strings** (not objects), matching the
`to_json_str()` output from `langfuce_trace_dump.py`. `FaultBucketingPipeline` expects this
exact format.

**Validation** (applied after acquiring the file, regardless of source type):

1. File exists and is readable. → `TRACE_NOT_FOUND`
2. Content is valid JSON. → `TRACE_PARSE_ERROR`
3. Top-level value is a list. → `TRACE_PARSE_ERROR`
4. List is non-empty. → `TRACE_NOT_FOUND`
5. At least one element has the key `"id"`. → `TRACE_PARSE_ERROR`

For Langfuse mode, rule 4 catches the case where the server-side filter returned zero traces
(i.e., no trace with matching metadata fields exists in Langfuse).

Each assertion maps to a specific `error_code` on `TraceIngestionError`. The task runner reads
`exc.error_code` and passes it directly to `session_service.set_failed()`.

---

## Workspace Traces Directory

```
workspace/{experiment_id}/{run_id}/traces/
└── raw_trace.json
```

This directory is created by `trace_service` before writing. The file is named `raw_trace.json`
regardless of source type. The task runner passes the absolute path of this file to
`pipeline_service.execute_pipeline()`.

---

## Interface

```python
# main/services/trace_service.py

class TraceIngestionError(Exception):
    """Raised when trace cannot be acquired or validated."""
    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code  # e.g. "TRACE_NOT_FOUND", "LANGFUSE_FETCH_ERROR", "TRACE_PARSE_ERROR"

async def acquire_trace(
    source: TraceSource,         # Pydantic model from models/requests.py
    workspace_traces_dir: Path,  # workspace/{experiment_id}/{run_id}/traces/
    experiment_id: str,          # used as metadata filter for Langfuse mode
    run_id: str,                 # used as metadata filter for Langfuse mode
) -> Path:
    """
    Fetch or copy the trace into workspace_traces_dir/raw_trace.json.
    For type="langfuse": calls trace.list() with a server-side filter on
      metadata.experiment_id == experiment_id AND metadata.run_id == run_id,
      then fetches observations for each matched trace.
    For type="file": copies the source file to workspace_traces_dir/raw_trace.json.
    Returns the absolute Path to the saved file.
    Raises TraceIngestionError on any failure.
    """
```

`acquire_trace` is async. All blocking operations (Langfuse SDK, file I/O) run inside
`asyncio.to_thread()` so the event loop is never blocked. The Langfuse SDK has no native async
client — the entire fetch + filter block runs in a single thread.

---

## Langfuse Dependency

`langfuse==4.0.0` is installed in the environment (confirmed). The `filter` parameter and
`stringObject` metadata filtering used in this design require **langfuse >= 4.0.0**.

Verify before implementation:

```bash
pip show langfuse   # must show Version: 4.0.0 or higher
```

If upgrading, check that `langfuce_trace_dump.py` (which uses `client.api.legacy.observations_v1`)
still works — the `legacy` sub-client may be removed in future Langfuse versions.
