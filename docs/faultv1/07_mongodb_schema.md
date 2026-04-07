# 07 — MongoDB Collections & Schemas

## Database

All collections live in the single MongoDB database defined by `MONGODB_DATABASE` env var
(default: `agentcert`). All four collections are **created and indexed at application startup**
(see `03_app_startup.md` Step 3). No collection is created lazily at write time.

---

## Collection Map

| Collection | Name (env-configurable) | Written by | When |
|---|---|---|---|
| `pipeline_tasks` | `API_TASK_COLLECTION` (default: `pipeline_tasks`) | `session_service.py` | Every POST request |
| `agent_run_metrics` | `configs.json → mongodb.collections.metrics` (default: `agent_run_metrics`) | Existing `MongoDBClient` in `utils/mongodb_util.py` | Phase 1 (when `storage_config.type = mongodb \| hybrid`) |
| `extraction_metadata` | `extraction_metadata` (hardcoded) | Iteration 2 | After Phase 1 completes |
| `fault_metadata` | `fault_metadata` (hardcoded) | Iteration 2 | Per-fault, after Phase 1 |

---

## 1. `pipeline_tasks`

**Purpose**: API session store. Tracks every submitted pipeline task from creation through
completion or failure. The only collection written by the new API layer.

**Written by**: `main/services/session_service.py`

### Document Schema

```json
{
  "_id": "ObjectId",
  "task_id": "uuid-v4-string",

  "agent_id": "agent_v2_4_1",
  "experiment_id": "exp_001",
  "run_id": "run_001",

  "status": "PENDING | RUNNING | COMPLETED | FAILED",
  "stage":  "pending | trace_fetch | validation | bucketing | metrics_extraction | storage | done",

  "created_at":   "2026-04-07T10:00:00.000Z",
  "updated_at":   "2026-04-07T10:00:00.000Z",
  "started_at":   "2026-04-07T10:00:05.000Z",
  "completed_at": "2026-04-07T10:01:02.000Z",

  "request": {
    "trace_source": {
      "type": "langfuse | file",
      "file_path": null,
      "base_url": null,
      "from_timestamp": null
    },
    "llm_batch_size": 5,
    "storage_config": {
      "type": "local | blob_storage | mongodb | hybrid",
      "container_name": "cert-artifacts"
    }
  },

  "result": {
    "total_observations": 1523,
    "total_faults_detected": 3,
    "faults": [
      {
        "fault_id": "pod-delete",
        "fault_name": "pod-delete",
        "severity": "critical",
        "status": "closed",
        "detected_at": "2026-04-07T10:00:02Z",
        "mitigated_at": "2026-04-07T10:00:40Z"
      }
    ],
    "storage_paths": {
      "traces_dir":        "workspace/exp_001/run_001/traces/",
      "fault_buckets_dir": "workspace/exp_001/run_001/fault_buckets/",
      "metrics_dir":       "workspace/exp_001/run_001/metrics/",
      "summary":           "workspace/exp_001/run_001/pipeline_summary.json",
      "log":               "workspace/exp_001/run_001/pipeline.log"
    },
    "token_usage": {
      "bucketing_input_tokens":   4250,
      "bucketing_output_tokens":  1100,
      "extraction_input_tokens":  8500,
      "extraction_output_tokens": 2200,
      "total_tokens":             16050
    },
    "processing_time_seconds": 72.4
  },

  "error": {
    "error_code":   "BUCKETING_FAILED",
    "message":      "LLM classifier returned empty response on batch 3",
    "failed_stage": "bucketing",
    "detail":       "Traceback ..."
  }
}
```

### Field Rules

- `result` is `null` until `set_completed()` writes it.
- `error` is `null` unless `set_failed()` writes it.
- `request.trace_source.secret_key` is **never stored** — stripped before persistence.
- `started_at` / `completed_at` are `null` until the relevant transition occurs.

### Indexes

```
idx_task_id_unique      {task_id: 1}                                    unique
idx_agent_exp_run       {agent_id: 1, experiment_id: 1, run_id: 1}
idx_status_created      {status: 1, created_at: -1}
idx_created_at          {created_at: 1}
```

---

## 2. `agent_run_metrics`

**Purpose**: Stores combined quantitative + qualitative metrics per fault per run. Primary
output store for Phase 1. Supports Atlas Vector Search for semantic similarity queries.

**Written by**: Existing `MongoDBClient.insert_metrics()` in `utils/mongodb_util.py` —
called from `TraceMetricsExtractor` when `store_to_mongodb=True`.

**Trigger**: `storage_config.type = "mongodb" | "hybrid"` in the request.

### Document Schema

```json
{
  "_id": "ObjectId",

  "experiment_id": "exp_001",
  "run_id":        "run_001",
  "agent_name":    "agent_v2_4",
  "agent_id":      "agent_v2_4_1",
  "fault_category":"compute",
  "fault_name":    "pod-delete",

  "quantitative": {
    "agent_name":                  "agent_v2_4",
    "agent_id":                    "agent_v2_4_1",
    "agent_version":               null,
    "experiment_id":               "exp_001",
    "run_id":                      "run_001",
    "fault_injection_time":        "2026-04-07T10:00:00Z",
    "agent_fault_detection_time":  "2026-04-07T10:00:15Z",
    "agent_fault_mitigation_time": "2026-04-07T10:01:30Z",
    "time_to_detect":              15.0,
    "time_to_mitigate":            90.0,
    "fault_detected":              "Yes | No | Unknown",
    "trajectory_steps":            12,
    "input_tokens":                5000,
    "output_tokens":               1500,
    "injected_fault_name":         "pod-delete",
    "injected_fault_category":     "compute",
    "detected_fault_type":         "pod-delete",
    "fault_target_service":        "payment-service",
    "fault_namespace":             "production",
    "tool_calls": [
      {
        "tool_name":        "get_logs",
        "arguments":        {"service": "payment-service"},
        "was_successful":   true,
        "response_summary": "Retrieved logs",
        "timestamp":        "2026-04-07T10:00:10Z"
      }
    ],
    "pii_detection":                    false,
    "number_of_pii_instances_detected": 0,
    "malicious_prompts_detected":       0,
    "tool_selection_accuracy":          0.92
  },

  "qualitative": {
    "rai_check_status":           "Passed | Failed | Not Evaluated",
    "rai_check_notes":            "No harmful content detected",
    "security_compliance_status": "Compliant | Non-Compliant | Partially Compliant | Not Evaluated",
    "security_compliance_notes":  "No credentials exposed",
    "reasoning_quality_score":    9.0,
    "reasoning_quality_notes":    "Clear and accurate reasoning",
    "agent_summary":              "Agent detected pod deletion and remediated.",
    "hallucination_score":        0.0,
    "plan_adherence":             null,
    "collateral_damage":          null
  },

  "embedding": [0.012, -0.034, "...1536 floats"],

  "metadata": {
    "trace_file": "raw_trace.json",
    "total_spans": 12,
    "extraction_token_usage": {
      "input_tokens":  3000,
      "output_tokens": 800,
      "total_tokens":  3800
    }
  },

  "created_at": "2026-04-07T10:00:00Z"
}
```

### Field Rules

- `experiment_id` is unique (sparse). Re-running the same experiment replaces the document
  (upsert behaviour in `MongoDBClient.insert_metrics()`).
- `embedding` is optional. Present only when the embedding model is configured and called.
- `fault_category` maps from `quantitative.injected_fault_category`.
- `fault_name` maps from `quantitative.injected_fault_name`.

### Indexes

```
idx_fault_category_name    {fault_category: 1, fault_name: 1}
idx_experiment_id_unique   {experiment_id: 1}               unique, sparse
idx_fault_category_created {fault_category: 1, created_at: -1}
idx_agent_id               {agent_id: 1}                    sparse
idx_agent_name             {agent_name: 1}                  sparse
idx_run_id                 {run_id: 1}                      sparse
```

### Atlas Vector Search Index

```json
{
  "name": "metrics_vector_index",
  "type": "vectorSearch",
  "fields": [
    {
      "type": "vector",
      "path": "embedding",
      "numDimensions": 1536,
      "similarity": "cosine"
    },
    {"type": "filter", "path": "fault_category"},
    {"type": "filter", "path": "fault_name"}
  ]
}
```

> Created separately via `MongoDBClient.create_vector_search_index()` — **not** created by
> `create_index()` at startup because Atlas Vector Search indexes require Atlas infrastructure.
> On a local MongoDB instance this call will fail gracefully and be skipped.

---

## 3. `extraction_metadata`

**Purpose**: One document per API call (per `extraction_id`). Records pipeline-level metadata:
storage paths, token totals, processing time, fault count. Iteration 2 write.

**Written by**: `main/services/session_service.py` (iteration 2) after pipeline completes.

### Document Schema

```json
{
  "_id": "ObjectId",
  "extraction_id":  "uuid-v4",
  "experiment_id":  "exp_001",
  "run_id":         "run_001",
  "agent_id":       "agent_v2_4_1",
  "status":         "success | failed",
  "created_at":     "2026-04-07T10:00:00Z",
  "storage_paths": {
    "fault_buckets_dir": "workspace/exp_001/run_001/fault_buckets/",
    "metrics_dir":       "workspace/exp_001/run_001/metrics/",
    "summary":           "workspace/exp_001/run_001/pipeline_summary.json",
    "log":               "workspace/exp_001/run_001/pipeline.log"
  },
  "llm_tokens": {
    "bucketing_input":   4250,
    "bucketing_output":  1100,
    "extraction_input":  8500,
    "extraction_output": 2200,
    "total":             16050
  },
  "processing_time_seconds": 72.4,
  "fault_count":    3,
  "error_message":  null
}
```

### Indexes

```
idx_extraction_id_unique   {extraction_id: 1}                              unique
idx_exp_run_agent          {experiment_id: 1, run_id: 1, agent_id: 1}
idx_agent_created          {agent_id: 1, created_at: -1}
```

---

## 4. `fault_metadata`

**Purpose**: One document per fault per extraction. Records per-fault details, blob paths,
and a quantitative summary for quick lookup without joining `agent_run_metrics`.
Iteration 2 write.

**Written by**: `main/services/session_service.py` (iteration 2), one insert per fault after
metrics extraction.

### Document Schema

```json
{
  "_id": "ObjectId",
  "fault_id":      "pod-delete",
  "extraction_id": "uuid-v4",
  "experiment_id": "exp_001",
  "run_id":        "run_001",
  "agent_id":      "agent_v2_4_1",
  "fault_name":    "pod-delete",
  "severity":      "critical",
  "target_pod":    "payment-service-pod",
  "namespace":     "production",
  "status":        "closed | open",
  "event_count":   35,
  "detected_at":   "2026-04-07T10:00:02Z",
  "mitigated_at":  "2026-04-07T10:00:40Z",
  "blob_paths": {
    "bucket_file":  "workspace/exp_001/run_001/fault_buckets/bucket_pod-delete.json",
    "metrics_file": "workspace/exp_001/run_001/metrics/pod-delete_run_001_metrics.json"
  },
  "quantitative_summary": {
    "trajectory_steps": 35,
    "input_tokens":     1200,
    "output_tokens":    450,
    "tool_calls":       8
  },
  "created_at": "2026-04-07T10:00:00Z"
}
```

### Indexes

```
idx_exp_run_agent    {experiment_id: 1, run_id: 1, agent_id: 1}
idx_agent_extraction {agent_id: 1, extraction_id: 1}
idx_fault_id         {fault_id: 1}
idx_created_at       {created_at: -1}
```

---

## Write Ownership Summary

```
POST /api/v1/bucketing-extraction
│
├── Always:   pipeline_tasks  ← session_service.py (new)
│
├── If storage_config.type = mongodb | hybrid:
│   └── Phase 1: agent_run_metrics  ← utils/mongodb_util.py (existing, unchanged)
│
└── Iteration 2 only:
    ├── extraction_metadata  ← session_service.py (new, post-pipeline)
    └── fault_metadata       ← session_service.py (new, per-fault)
```
