# Fault Bucketing Module

## Overview

The **Fault Bucketing** module preprocesses multi-fault Langfuse traces by splitting interleaved events into per-fault buckets. Each bucket contains the complete lifecycle of a single fault (detection → investigation → remediation → verification), enabling independent metrics extraction per fault.

## Architecture

The module follows a three-layer design:

```
schema/          → Data models (Pydantic + dataclass) and parsing helpers
scripts/
  classifier.py  → LLM-based event classification (Azure OpenAI)
  fault_bucketing.py → Pipeline orchestration and output generation
config/          → Module-specific configuration (batch size, model params)
prompt/          → LLM system prompt templates (YAML)
```

### Data Flow

```
Raw Langfuse Trace (JSON)
    │
    ▼
┌─────────────────────────┐
│  FaultBucketingPipeline  │
│  1. Load & sort events   │
│  2. Extract FAULT_DATA   │
│     (injected ground     │
│      truth)              │
│  3. Batch events         │
│     chronologically      │
│  4. Send to LLM for      │
│     classification       │
│  5. Create/close fault   │
│     buckets              │
│  6. Enrich with ground   │
│     truth                │
│  7. Write per-fault JSON │
│     + manifest           │
└─────────────────────────┘
    │
    ▼
Per-Fault Bucket JSON Files + Manifest
```

## Configuration

Module-specific settings are in `config/fault_bucketing_config.json`:

| Key | Default | Description |
|-----|---------|-------------|
| `classifier.model_name` | `"extraction_model"` | Azure OpenAI model tier to use |
| `classifier.temperature` | `0.1` | LLM sampling temperature |
| `classifier.max_tokens` | `4000` | Maximum tokens per LLM response |
| `classifier.fallback_confidence` | `0.3` | Confidence score for fallback classifications |
| `pipeline.default_batch_size` | `10` | Number of events per LLM classification batch |
| `pipeline.max_filename_stem_length` | `80` | Max characters for output file name stems |

The global Azure OpenAI configuration (endpoints, API keys) is loaded via `ConfigLoader` from `agentcert/configs/configs.json`.

## Usage

### CLI

```bash
python -m notebooks.fault_bucketing.scripts.fault_bucketing \
    --trace-file path/to/trace.json \
    --output-dir path/to/output/ \
    --batch-size 10
```

### Programmatic

```python
import asyncio
from notebooks.fault_bucketing import FaultBucketingPipeline

pipeline = FaultBucketingPipeline(
    trace_file_path="path/to/trace.json",
    output_dir="path/to/output/",
    batch_size=10,
)
buckets = asyncio.run(pipeline.run())

for fault_id, bucket in buckets.items():
    print(f"{fault_id}: {len(bucket.events)} events ({bucket.status})")
```

## Input Format

The input is a JSON array of Langfuse trace events (spans). Each event should have:

- `id` — Unique event identifier
- `type` — Event type (`"GENERATION"`, `"SPAN"`, `"FAULT_DATA"`, etc.)
- `name` — Event name (e.g., `"k8s_pods_list"`, `"pod-delete"`)
- `startTime` — ISO-8601 timestamp
- `endTime` — ISO-8601 timestamp
- `input` / `output` — Event payload
- `parentObservationId` — Parent span ID (for hierarchy)

Events with `type == "FAULT_DATA"` are treated as injected ground truth from the chaos engineering platform.

## Output Format

### Per-Fault Bucket Files

Each bucket file (`{trace_stem}_bucket_{fault_name}.json`) contains:

```json
{
  "fault_id": "pod-delete",
  "fault_name": "pod-delete",
  "severity": "critical",
  "target_pod": "my-app-pod",
  "namespace": "default",
  "status": "closed",
  "detected_at": "2025-01-01T10:00:00Z",
  "mitigated_at": "2025-01-01T10:15:00Z",
  "ground_truth": { ... },
  "event_count": 25,
  "events": [ ... ]
}
```

### Manifest File

The manifest (`{trace_stem}_bucketing_manifest.json`) provides a summary:

```json
{
  "trace_file": "trace.json",
  "total_injected_faults": 2,
  "total_faults": 2,
  "total_events_assigned": 50,
  "unclassified_event_count": 3,
  "llm_tokens_used": { "input_tokens": 12000, "output_tokens": 3000 },
  "buckets": [ ... ]
}
```

## Key Classes

| Class | Location | Purpose |
|-------|----------|---------|
| `FaultBucketingPipeline` | `scripts/fault_bucketing.py` | Main pipeline orchestrator |
| `FaultEventClassifier` | `scripts/classifier.py` | LLM-based event classifier |
| `EventClassification` | `schema/data_models.py` | Pydantic model for per-event classification |
| `BatchClassificationResult` | `schema/data_models.py` | Pydantic wrapper for batch results |
| `FaultBucket` | `schema/data_models.py` | Dataclass for fault lifecycle container |
