# Fault Bucketing Module

## Overview

The **Fault Bucketing** module (Phase 0 of the certifier pipeline) preprocesses multi-fault Langfuse traces by splitting interleaved events into per-fault buckets. Each bucket contains the complete lifecycle of a single fault (injection → detection → investigation → remediation), enabling independent metrics extraction per fault in Phase 1.

## Architecture

The module follows a three-layer design:

```
fault_analyzer/
  schema/data_models.py    → Pydantic models (EventClassification,
                              BatchClassificationResult) + FaultBucket
                              dataclass + parsing helpers
  scripts/
    classifier.py          → LLM-based event classification (Azure OpenAI)
    fault_bucketing.py     → Pipeline orchestration and output generation
  config/
    fault_bucketing_config.json  → Module config (model, toggles, batch size)
  prompt/
    v1/prompt.yml          → Default system prompt
    v2/prompt.yml          → Compact-fault-context prompt variant
```

### Data Flow

```
Raw Langfuse Trace (JSON)
    │
    ▼
┌────────────────────────────────────────────┐
│         FaultBucketingPipeline             │
│  1. Load and sort events chronologically   │
│  2. Extract run-level tokens from trace    │
│     'usage' fields (one-time, pre-bucket)  │
│  3. Extract agent metadata from early      │
│     events (agent_id/name/version,         │
│     experiment_id, run_id)                 │
│  4. Pass 1 — deterministic bucket creation │
│     from spans named "fault: <name>"       │
│     (no LLM); dedup against active buckets │
│  5. Pass 2 — per-event temporal router on  │
│     remaining events, batched:             │
│       • scaffolding span → skip (no LLM)   │
│       • 0 faults in flight → skip (no LLM) │
│       • 1 fault in flight → deterministic  │
│         assignment to that fault           │
│       • >1 faults in flight → LLM batch    │
│         restricted to candidate_fault_ids  │
│  6. Record fault detections / mitigations  │
│     from LLM output; close mitigated       │
│     buckets                                │
│  7. Re-sort each bucket's events           │
│  8. Write per-fault JSON, manifest,        │
│     ground-truth files, optional debug     │
│     classification trace                   │
└────────────────────────────────────────────┘
    │
    ▼
Per-Fault Bucket JSON Files + Manifest
(+ ground_truth/ + unclassified + other_detected_faults
 + batch_classification_trace.json when debug=True)
```

## Configuration

Module-specific settings are in `config/fault_bucketing_config.json`:

| Key | Default | Description |
|-----|---------|-------------|
| `classifier.model_name` | `"gpt-4o"` | Azure OpenAI model tier to use |
| `classifier.temperature` | `0.1` | LLM sampling temperature |
| `classifier.max_tokens` | `4000` | Maximum tokens per LLM response |
| `classifier.fallback_confidence` | `0.3` | Confidence score for fallback classifications |
| `classifier.prompt_path` | `"prompt/v1/prompt.yml"` | Path (relative to module) to the system prompt YAML |
| `classifier.fault_pruning` | `false` | When `true`, the `## Known Faults` block uses the compact context (drops timing/severity/SLA/bookkeeping for ~84% smaller fault payload). When `false`, the legacy verbose context is sent |
| `classifier.cache_enabled` | `false` | When `true`, the system prompt is sent in the system role so Azure GPT-4o auto-cache can hit the stable >=1024-token prefix (~50% rebate on batches 2..N). When `false`, the system prompt is inlined into the user message |
| `classifier.include_event_input` | `true` | When `true`, both `event.input` and `event.output` are rendered per event. When `false`, only `event.output` is sent (cheaper but discards agent reasoning) |
| `pipeline.default_batch_size` | `1` | Number of events per LLM classification batch |
| `pipeline.max_filename_stem_length` | `80` | Max characters for output file name stems |

Each classifier toggle resolves with the precedence: **CLI flag > constructor arg > config file > hardcoded default**.

The global Azure OpenAI configuration (endpoints, API keys) is loaded via `ConfigLoader` from `configs/configs.json`.

## Usage

### CLI

```bash
python -m fault_analyzer.scripts.fault_bucketing \
    --trace-file path/to/trace.json \
    --output-dir path/to/output/ \
    --batch-size 1
```

Optional flags:

| Flag | Effect |
|------|--------|
| `--no-debug` | Disable writing `batch_classification_trace.json` (debug trace is on by default) |
| `--prompt <path>` | Override `classifier.prompt_path` for this run |
| `--fault-pruning` / `--no-fault-pruning` | Force compact / verbose known-faults block |
| `--cache` / `--no-cache` | Force system-role / inlined system prompt |
| `--include-input` / `--no-include-input` | Force input+output / output-only per-event rendering |

### Programmatic

```python
import asyncio
from fault_analyzer.scripts.fault_bucketing import FaultBucketingPipeline

pipeline = FaultBucketingPipeline(
    trace_file_path="path/to/trace.json",
    output_dir="path/to/output/",
    batch_size=1,
)
buckets = asyncio.run(pipeline.run())

for fault_id, bucket in buckets.items():
    print(f"{fault_id}: {len(bucket.events)} events ({bucket.status})")
```

## Input Format

The input is a JSON array of Langfuse trace events (spans). Each event should have:

- `id` — Unique event identifier
- `type` — Event type (`"GENERATION"`, `"SPAN"`, etc.)
- `name` — Event name. **Spans whose name matches `fault: <fault-name>` are treated as injection markers** and trigger deterministic bucket creation (e.g. `fault: pod-delete`).
- `startTime` / `endTime` — ISO-8601 timestamps
- `input` / `output` — Event payload
- `metadata` — Carries `attributes.fault.*` keys on injection spans (target, timing, ground_truth, probes, workflow) and `usage` token counts on LLM generations
- `parentObservationId` — Parent span ID (for hierarchy)

Agent metadata (`agent_id`, `agent_name`, `agent_version`, `experiment_id`, `run_id`) is harvested from the `input` / `metadata` fields of events that precede the first `fault: *` span.

Scaffolding spans are deterministically excluded from LLM classification: names starting with `workflow-step`, plus `experiment-triggered` and `experiment_context`, and any event with both empty `input` AND empty `output`.

## Output Format

### Per-Fault Bucket Files

Each bucket file (`{trace_stem}_bucket_{fault_name}.json`) is the serialized `FaultBucket` dataclass:

```json
{
  "fault_id": "pod-delete",
  "fault_name": "pod-delete",
  "severity": "critical",
  "target_pod": "my-app-pod",
  "namespace": "default",
  "detection_signals": ["CrashLoopBackOff", "Pod NotReady"],
  "status": "closed",
  "detected_at": "2025-01-01T10:00:30Z",
  "mitigated_at": "2025-01-01T10:15:00Z",
  "injection_timestamp": "2025-01-01T10:00:00Z",
  "injection_end_timestamp": "2025-01-01T10:05:00Z",
  "injection_metadata": { "name": "...", "target": {...}, "timing": {...}, "injection": {...}, "probes": [...], "workflow": {...} },
  "ground_truth": { "...": "..." },
  "sla": { "...": "..." },
  "ideal_course_of_action": [ ... ],
  "ideal_tool_usage_trajectory": [ ... ],
  "agent_id": "agent-1",
  "agent_name": "k8s-agent",
  "agent_version": "1.0.0",
  "experiment_id": "exp-001",
  "run_id": "run-001",
  "event_count": 25,
  "events": [ ... ]
}
```

The injection span itself is **not** appended to `events` — it is consumed only for metadata extraction.

### Manifest File

The manifest (`{trace_stem}_bucketing_manifest.json`) summarizes the run:

```json
{
  "trace_file": "trace.json",
  "total_faults": 2,
  "total_events_assigned": 50,
  "other_detected_faults_count": 0,
  "unclassified_event_count": 3,
  "trace_tokens": {
    "description": "Tokens extracted ONCE from entire trace (before bucketing) to avoid double-counting",
    "input_tokens": 12000,
    "output_tokens": 3000,
    "total_tokens": 15000
  },
  "llm_tokens_used": {
    "description": "Tokens used by LLM classifier to assign events to fault buckets",
    "input_tokens": 8500,
    "output_tokens": 1200,
    "total_tokens": 9700
  },
  "buckets": [ ... ]
}
```

`trace_tokens` captures agent-side LLM consumption parsed from each event's `usage` field (extracted once before bucketing to prevent double-counting across buckets). `llm_tokens_used` captures the classifier's own Azure OpenAI consumption.

### Auxiliary Outputs

| File | Written when | Contents |
|------|--------------|----------|
| `{stem}_other_detected_faults.json` | LLM identifies a fault that has no matching injected bucket | List of agent-discovered faults (name, severity, target, signals, detection timestamp) |
| `{stem}_unclassified.json` | Any events end up unplaced | Raw event objects skipped or rejected by classification |
| `../ground_truth/{experiment_id}_{fault_name}_ground_truth.json` | Any bucket carries ground truth | Per-(experiment, fault) ground-truth snapshot (written to a sibling `ground_truth/` directory) |
| `batch_classification_trace.json` | `debug=True` (default) | One entry per raw event: source (`deterministic_fault_span` / `llm`), batch index, faults injected, eligible faults, filtered-out faults, per-event token share, captured LLM prompt + classification outcome |

## Workflow Details

### Pass 1 — Deterministic Bucket Creation

Buckets are created **without any LLM call** by scanning span names for the `fault: <name>` pattern.

Deduplication rules:

- If an **active** bucket with the same fault name already exists, the duplicate injection span is silently skipped.
- If all previous buckets with that name are **closed**, a new bucket is created with a numeric suffix (e.g. `pod-delete_2`).

The injection span's `metadata.attributes.fault.*` keys are unpacked into a structured `injection_metadata` block (target, timing, injection, probes, workflow) on the bucket. `ground_truth` is read from `metadata.ground_truth`, `metadata.attributes.ground_truth`, or `input.ground_truth` (in that order); `sla`, `ideal_course_of_action`, and `ideal_tool_usage_trajectory` are lifted out of the ground-truth dict.

### Pass 2 — Per-Event Temporal Router

Remaining events are batched (`default_batch_size = 1`) and routed per-event:

| In-flight fault count at event timestamp | Path | LLM consulted? |
|------------------------------------------|------|----------------|
| 0 | `unclassified_reason = "No fault was temporally in flight..."` | No |
| 1 | Deterministic assignment to that single fault | No |
| >1 | Sent to LLM with `candidate_fault_ids` restricted to the in-flight set | Yes |

"In flight" means the event's `startTime` falls inside `[injection_timestamp - ramp, injection_end_timestamp + ramp]` for that fault (open-ended on the upper bound when `injection_end_timestamp` is absent). Ramp comes from `injection_metadata.timing.ramp_time_sec`.

### LLM Prompting

When the LLM is invoked, the user message contains:

1. A `## Known Faults` block listing only the **eligible** (in-flight) buckets for the events in the batch — closed/not-yet-injected faults are excluded to save tokens. Each fault is rendered using either the compact context (`fault_pruning=true`, ~84% smaller) or the verbose legacy payload (`fault_pruning=false`).
2. An `## Event Batch` block with each event's `id`, `input` (if `include_event_input=true`), `output`, and per-event `candidate_fault_ids` list.
3. Instructions to restrict `related_faults` to `candidate_fault_ids`, use `injection_metadata.target` (namespace, label, workload_ref) for disambiguation, and flag detections / mitigations.

The system prompt is loaded from the YAML referenced by `classifier.prompt_path` (default `prompt/v1/prompt.yml`). When `cache_enabled=true`, the system prompt is sent in the system role so Azure GPT-4o auto-cache can hit the stable prefix; when `false`, it is inlined into the user message and the system role is left empty.

### Fault Detection and Mitigation

When an LLM classification sets `fault_detected`, the matching bucket's `detected_at` is filled (only if not already populated by a prior detection) along with `severity`, `target_pod`, `namespace`, and `detection_signals` (when previously empty). If no matching bucket exists, the detection is recorded in `other_detected_faults` rather than fabricating a new bucket.

When `fault_mitigated` is set, the bucket is moved from `active_faults` to `closed_faults` and `mitigated_at` is recorded.

### Fallback Behavior

When `classify_batch` is called with `catch=True` (the default) and either the LLM call or output parsing fails, `fallback_classify` assigns **every event in the batch to all known faults** at `fallback_confidence` (default 0.3), with a `fault_reasoning` explaining the conservative assignment. Setup errors (config / serialization) always raise `FaultClassifierError`.

### Single-Fault Fallback

If no `fault: *` spans are found in the trace, the pipeline emits one bucket `single_fault` (status `closed`) containing every sorted event, with `detected_at` = first event's `startTime` and `mitigated_at` = last event's `endTime`.

### Run-Level Token Extraction

Before bucketing, the pipeline iterates the sorted events once to sum `usage.input` and `usage.output` across all spans into `trace_input_tokens` / `trace_output_tokens`. These are surfaced in the manifest's `trace_tokens` block and consumed by downstream phases (Phase 1 → 2 → 3) without recalculation, preventing the same generation's tokens being counted multiple times when a span appears in multiple buckets.

## Key Classes

| Class | Location | Purpose |
|-------|----------|---------|
| `FaultBucketingPipeline` | `scripts/fault_bucketing.py` | Main pipeline orchestrator (Pass 1 deterministic bucketing + Pass 2 temporal router) |
| `FaultEventClassifier` | `scripts/classifier.py` | LLM-based event classifier with compact / verbose fault context, cache and input toggles, and conservative fallback |
| `EventClassification` | `schema/data_models.py` | Pydantic model: `related_faults`, `fault_detected` (+severity/target_pod/namespace/signals), `fault_mitigated`, `confidence`, `unclassified_reason`, `fault_reasoning` |
| `BatchClassificationResult` | `schema/data_models.py` | Pydantic wrapper containing a list of `EventClassification` |
| `FaultBucket` | `schema/data_models.py` | Dataclass holding the full per-fault lifecycle: injection metadata, ground truth, ideal course/trajectory, SLA, agent context, events |
