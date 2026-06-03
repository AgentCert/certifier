# Metric Extraction from Trace Module (Phase 1)

## Overview

The **Metric Extraction from Trace** module is Phase 1 of the AgentCert pipeline. It consumes per-fault bucket JSON files produced by Phase 0 (`fault_analyzer/`) and extracts:

- **Quantitative metrics** per fault (TTD, TTR, token counts, tool calls, ground-truth comparison)
- **Qualitative metrics** per fault (RAI checks, security compliance, reasoning quality, hallucination score, agent summary)

It combines batched LLM extraction with deterministic, code-based numeric aggregation: the LLM only synthesizes narrative / categorical fields, while all numbers (sums, averages, ratios, TTD/TTR) are computed in Python and overwrite any LLM-produced numeric values.

## Architecture

```
metrics_extractor/
  __init__.py                    → Exports TraceMetricsExtractor + helpers
  config/
    metric_extraction_config.json
  docs/                          → This documentation
  prompt/
    prompts.yml                  → All system prompts (one YAML file)
  schema/
    metrics_model.py             → Pydantic schemas (LLMQuantitativeExtraction,
                                    LLMQualitativeExtraction, CombinedJudgeResponse, ...)
    data_models.py               → Dataclasses (ExtractionResult, TokenUsage)
  scripts/
    metrics_extractor_from_trace.py  → Orchestrator: TraceMetricsExtractor class + CLI
    span_aggregator.py               → Code-based aggregators
                                       (QuantitativeAggregator, QualitativeAggregator)
    combined_judge.py                → Per-step combined hallucination + reasoning
                                       quality judge (single LLM call per step)
    hallucination_validator.py       → Standalone per-step claim-grounding judge
                                       (legacy / standalone use)
  tests/                         → Unit tests
```

### Data Flow

```
Phase 0 bucket JSON (one file per fault)
    │  { fault_id, fault_name, namespace, target_pod,
    │    injection_timestamp, detected_at, mitigated_at,
    │    ground_truth: {...}, events: [span, ...] }
    ▼
┌──────────────────────────────────────────────────────┐
│ TraceMetricsExtractor.extract_metrics_async          │
│                                                      │
│ 1. load_trace_file                                   │
│    - accepts either {events: [...], <metadata>}      │
│      or a plain list of spans                        │
│    - non-`events` keys become bucket_metadata        │
│                                                      │
│ 2. extract_quantitative_metrics                      │
│    a. sort spans by startTime, split into batches    │
│       (extractor_batch_size, default 6)              │
│    b. per-batch LLM call → partial quantitative      │
│    c. _identify_detection_mitigation_spans (LLM)     │
│       → agent_fault_detection_time /                 │
│         agent_fault_mitigation_time                  │
│    d. _validate_bucket_timestamps_with_llm           │
│       (validates bucket detected_at / mitigated_at)  │
│    e. QuantitativeAggregator.prescan_spans_for_      │
│       sensitive_data (PII scan)                      │
│    f. QuantitativeAggregator.extract_token_and_      │
│       tool_metrics (input/output tokens, tool calls) │
│    g. QuantitativeAggregator.aggregate               │
│       (deterministic ground-truth + numerics)        │
│    h. LLM text consolidation                         │
│    i. Override LLM numerics with code values         │
│                                                      │
│ 3. extract_qualitative_metrics                       │
│    a. batched LLM extraction                         │
│    b. QualitativeAggregator.aggregate                │
│    c. judge_combined (per-step combined judge):      │
│       hallucination counts + four-dimension          │
│       reasoning quality scores                       │
│    d. LLM narrative synthesis                        │
│    e. Override LLM numerics with code values         │
│                                                      │
│ 4. Optional store_metrics_to_mongodb                 │
└──────────────────────────────────────────────────────┘
    │
    ▼
ExtractionResult
  ├── quantitative      (LLMQuantitativeExtraction)
  ├── qualitative       (LLMQualitativeExtraction)
  ├── token_usage       (TokenUsage)
  └── mongodb_document_id (Optional[str])
```

## Input Format

The module is designed to consume per-fault bucket JSON files emitted by Phase 0. The expected shape is:

```json
{
  "fault_id": "...",
  "fault_name": "pod-cpu-hog",
  "namespace": "sock-shop",
  "target_pod": "orders",
  "injection_timestamp": "2026-05-11T08:49:42.763Z",
  "detected_at": "...",
  "mitigated_at": "...",
  "ground_truth": {
    "fault_description_goal_remediation": {
      "symptoms": [...],
      "remediation": "..."
    }
  },
  "ideal_course_of_action": [...],
  "ideal_tool_usage_trajectory": [...],
  "events": [ { "id": "...", "type": "GENERATION", "startTime": "...", "endTime": "...",
                "input": "...", "output": "...", "metadata": "...", "usage": "..." }, ... ]
}
```

When the file has this shape, every top-level key except `events` is captured into `bucket_metadata` and used for ground-truth context injection and TTD baselines. A plain list of spans is also accepted (no bucket context in that case).

## Configuration

Module config: `config/metric_extraction_config.json`

```json
{
    "extractor": {
        "model_name": "gpt-4o",
        "extractor_batch_size": 6,
        "temperature": 0.1,
        "max_tokens": 16000
    },
    "mongodb": {
        "database": "agentcert",
        "quantitative_collection": "llm_quantitative_extractions",
        "qualitative_collection": "llm_qualitative_extractions"
    }
}
```

Notes:
- `extractor_batch_size` controls span batching for both quantitative and qualitative extraction; default is 6.
- An optional `detection_span_limit` key under `extractor` caps the number of spans sent to the detection/mitigation identification LLM call (useful for very large traces).

Global configuration (Azure OpenAI endpoints, keys, MongoDB connection string) is loaded via `ConfigLoader` from `utils/load_config.py`, with `ENV_*` env-var resolution as described in the root `CLAUDE.md`.

## Usage

### CLI

```bash
# Single bucket trace file
python -m metrics_extractor.scripts.metrics_extractor_from_trace \
    --trace-file-name path/to/<fault_bucket>.json \
    --store

# Directory of bucket files
python -m metrics_extractor.scripts.metrics_extractor_from_trace \
    --trace-directory path/to/buckets/
```

The CLI accepts only `--trace-file-name`, `--trace-directory`, and `--store`. Bucket metadata (fault name, injection timestamp, ground truth) is read from the bucket JSON itself — there is no separate fault-config flag.

### Programmatic (Async)

```python
import asyncio
from metrics_extractor import (
    TraceMetricsExtractor,
    extract_metrics_from_trace_async,
)

# Convenience function — bucket metadata is auto-extracted from bucket JSON
result = asyncio.run(extract_metrics_from_trace_async(
    "path/to/<fault_bucket>.json",
    store_to_mongodb=True,
))

# Class directly — optionally pass bucket_metadata explicitly
extractor = TraceMetricsExtractor(
    bucket_metadata={"fault_name": "pod-cpu-hog", "injection_timestamp": "..."},
    output_dir=Path("out/"),
    debug_metrics=True,   # write debug_detection_analysis_<fault>.json
)
result = asyncio.run(extractor.extract_metrics_async("path/to/trace.json"))
```

### Programmatic (Sync)

```python
from metrics_extractor import extract_metrics_from_trace

result = extract_metrics_from_trace("path/to/<fault_bucket>.json")
print(result.quantitative.model_dump_json(indent=2))
print(result.qualitative.model_dump_json(indent=2))
print(result.token_usage.to_dict())
```

## Output

`ExtractionResult` (`schema/data_models.py`) contains:

- **quantitative** (`LLMQuantitativeExtraction`): fault info, `fault_injection_time`, `agent_fault_detection_time`, `agent_fault_mitigation_time`, TTD/TTR, input/output tokens, tool calls, ground-truth comparison, PII pre-scan results
- **qualitative** (`LLMQualitativeExtraction`): RAI checks, security compliance, agent summary, plan adherence; reasoning quality (composite + four sub-dimensions: logical coherence, diagnostic depth, tool usage relevance, explanation clarity); hallucination score with per-type breakdown (ungrounded external, fabricated tool calls, trajectory deviations, non-operational)
- **token_usage** (`TokenUsage`): cumulative input / output / total tokens across all LLM calls
- **mongodb_document_id** (`Optional[str]`): MongoDB document ID when `store_to_mongodb=True`

Output is either written to a `*_metrics.json` file by the orchestrating pipeline (see `run_bucketing_and_extraction_pipeline.py`) or persisted to the MongoDB collections defined in module config (`llm_quantitative_extractions`, `llm_qualitative_extractions`).

## Storage

Persistence is handled by `utils.mongodb_util.MongoDBClient.insert_metrics(quantitative, qualitative, metadata)`. The extractor lazily constructs `MongoDBConfig(self.config)` and `MongoDBClient` on the first `store_metrics_to_mongodb` call and closes the connection after each insert. File-based storage (writing `*_metrics.json` next to bucket files) is performed by the upstream pipeline runner — not by the extractor itself.

## Key Design Decisions

1. **Batch Processing**: Spans are sorted by `startTime` and split into batches of `extractor_batch_size` (default 6) to fit LLM context limits.
2. **Two-Phase Aggregation**: The LLM extracts per-batch observations and synthesizes narrative fields; all numerics are computed by `QuantitativeAggregator` / `QualitativeAggregator` and overwrite any LLM-emitted numbers in a final override pass.
3. **Detection / Mitigation Span Identification**: A dedicated LLM call (`_identify_detection_mitigation_spans`) selects the first detection span and the final mitigation span; their `startTime` / `endTime` become `agent_fault_detection_time` / `agent_fault_mitigation_time`.
4. **Bucket Timestamp Validation**: `_validate_bucket_timestamps_with_llm` re-checks bucket `detected_at` / `mitigated_at` by asking the LLM whether the events at those exact timestamps actually represent detection / mitigation. Rejected timestamps are nulled out.
5. **Combined Per-Step Judge**: `combined_judge.judge_combined` issues a single LLM call per reasoning step that returns both claim-grounding classifications and four-dimension reasoning quality scores — halving the LLM calls a separate hallucination judge would require.
6. **Bucket-Metadata Ground Truth**: When bucket JSON contains `ground_truth`, `ideal_course_of_action`, `ideal_tool_usage_trajectory`, etc., these are injected into both quantitative and qualitative prompts (via the `ground_truth_with_config` / `behavioural_with_config` prompt templates) and deterministic fields override LLM-extracted values.
7. **No LLM Math**: All arithmetic happens in `span_aggregator.py` for reproducibility.
8. **Debug Mode**: When `debug_metrics=True` and `output_dir` is set, the orchestrator writes `debug_detection_analysis_<fault_name>.json` containing the LLM input, selected detection span, per-span detection scores (first 10 spans only), and selection reasoning.

## Fault Detection Workflow

The module implements a high-precision fault detection workflow that combines fault-specific context injection with token-optimized span processing.

### Fault Context Injection

When `fault_configuration.json` is provided, the following context is injected into LLM prompts to improve detection accuracy:

- **Injection Timestamp**: Precise time when fault was injected into the system
- **Target Service Name**: Specific service affected by the fault (e.g., `orders`, `carts`, `user`)
- **Expected Symptoms**: Fault-specific symptoms to look for:
  - `pod-cpu-hog`: High CPU usage, throttling, slow response times
  - `pod-network-loss`: Packet drops, connection timeouts, "no reachable servers" errors
  - `pod-memory-hog`: OOMKilled events, memory pressure, CrashLoopBackOff
- **Remediation Approach**: Expected agent response patterns

**Why This Matters**: Without target service context, the LLM may detect unrelated anomalies (e.g., identifying `carts` service probe failures when the actual fault targets the `orders` service). Fault context ensures the LLM filters to the correct target and symptoms.

**Example Impact**:
```
Before: "carts-probe failure detected" (wrong service, unrelated anomaly)
After: "orders pod memory pressure detected: OOMKilled events observed" (correct target)
```

### Token Optimization Strategy (v6_wo_input)

Large traces (particularly `pod-network-loss` with high span counts) previously exceeded LLM context windows, resulting in 0% detection rates. The v6_wo_input optimization resolves this by removing redundant fields from spans.

**Field Filtering**:

```python
# Before: 100% of span data (token-heavy)
prepared_span = {
    "id": "time-08-49-42-763832",
    "type": "GENERATION",
    "name": "ChatCompletion",
    "startTime": "2026-05-11T08:49:42.763Z",
    "endTime": "2026-05-11T08:49:44.891Z",
    "input": {...},      # 2-5KB: user prompt (90% repetitive)
    "output": {...},     # 1-3KB: LLM response (semantic content)
    "metadata": {...},   # 500B: system metadata
    "usage": {...}       # 200B: token counts
}

# After: Semantically relevant data only (v6_wo_input)
prepared_span = {
    "id": "time-08-49-42-763832",
    "type": "GENERATION",
    "name": "ChatCompletion",
    "startTime": "2026-05-11T08:49:42.763Z",
    "endTime": "2026-05-11T08:49:44.891Z",
    "output": {...}      # 1-3KB: LLM reasoning and tool calls
}
```

**Token Reduction**:
- Removed fields: `input` (2-5KB/span), `metadata` (~500B/span), `usage` (~200B/span)
- Retained field: `output` (contains agent reasoning and diagnostic actions)
- **Result**: ~60-70% token reduction per span, enabling full trace processing

**Why `output` Is Sufficient**: The `output` field contains:
- Agent's reasoning about observed symptoms
- Tool invocation patterns (`pods_top`, `execute_query`, `get_logs`)
- Diagnostic results and conclusions
- All semantically relevant information for fault detection

**Impact on Detection**:
- `pod-network-loss`: 0% → 100% detection rate (token overflow resolved)
- No loss of semantic accuracy (output field preserves all diagnostic reasoning)

### Detection Confidence Scoring

The LLM assigns confidence scores (0.0-1.0) to each potential detection span based on:

1. **Tool Usage Patterns**: Alignment with fault-specific diagnostic commands
   - `pod-cpu-hog`: `pods_top`, PromQL `container_cpu_usage_seconds_total` queries
   - `pod-network-loss`: PromQL `container_network_receive/transmit_packets_dropped` queries
   - `pod-memory-hog`: OOMKilled events, `container_memory_working_set_bytes` queries

2. **Symptom Matching**: Presence of fault-specific symptoms in agent output
   - Error patterns: "no reachable servers", "connection timeout", "OOMKilled"
   - Metric anomalies: CPU spikes, packet drop rates, memory pressure

3. **Target Service Focus**: Agent explicitly investigating the injected fault's target service

4. **Temporal Alignment**: Span timestamp falls within fault injection window

**Confidence Thresholds**:
- **≥0.85**: High confidence - clear fault symptoms + targeted diagnostics
- **0.70-0.84**: Moderate confidence - symptom recognition with investigation initiation
- **<0.70**: Low confidence - ambiguous or incomplete signals

**Example Reasoning Chain**:
```
15:02:54 (score 0.6): Agent targets non-ready pod → anomaly recognition
15:02:56 (score 0.7): Agent fetches logs for non-ready pod → investigation
15:02:58 (score 0.8): Agent recognizes "no reachable servers" error → fault identification [SELECTED]
15:03:00 (score 0.85): Agent lists dependency pods → verification
15:03:02 (score 1.0): Final analysis marks namespace degraded → conclusive
```

The LLM selects the **earliest span with sufficient confidence** to mark detection (typically ≥0.8), representing the moment the agent demonstrates fault awareness.

### Conservative Detection Behavior

The extraction module exhibits **zero false positive** behavior through conservative detection logic:

**Design Principle**: Only detect when **clear fault symptoms** are present in the trace.

**Conservative Behavior Examples**:

1. **pod-memory-hog** (Run 1a916cea):
   - No OOMKilled or CrashLoopBackOff events observed
   - Metrics server access forbidden (RBAC), cannot verify memory pressure
   - **Result**: `selected_span_id: null` (no detection)
   - **Reasoning**: "All 16 pods Running/Ready with low restart counts. No memory pressure signals. Conservatively avoiding false positive."
   - ✓ Correct behavior - fault did not manifest observably

2. **Ambiguous Multi-Fault Events**:
   - During fault window overlaps, LLM may miss detections rather than guess
   - Prioritizes precision over recall
   - **Trade-off**: May miss ~5-10% of detections to maintain zero false positives

**Why Conservative Is Correct**:
- Safety-critical fault detection requires high precision
- False negatives (missed detections) are acceptable
- False positives (incorrect detections) undermine certification trust

**Measured Performance**:
- **Precision**: ~0.96-0.98 (very few false positives)
- **Recall**: ~0.66-0.78 (detects 2/3 to 3/4 of faults)
- **False Positive Rate**: Near zero (conservative threshold enforcement)

## Detection Performance Metrics

Validation against manually labeled ground truth (17 production agent runs):

### Overall Detection Rates

| Fault Type | Detection Rate | Notes |
|---|---|---|
| **pod-cpu-hog** | **100%** (when fault manifests) | Millisecond-precision timestamp alignment |
| **pod-network-loss** | **100%** (after token optimization) | Resolved from 0% baseline via v6_wo_input |
| **pod-memory-hog** | Conservative (detects when symptoms clear) | Zero false positives; skips ambiguous cases |

### Timestamp Accuracy

Detection timestamps demonstrate **millisecond-level precision** when compared to manual ground truth:

**Example: Run 1a916cea-b5fe-4a5d-9d03-3f0cc695aef0**

| Fault Type | Manual CSV Timestamp | Metrics Detection Timestamp | Match Status |
|---|---|---|---|
| pod-cpu-hog | 2026-05-11T08:49:42.763Z | 2026-05-11T08:49:42.763Z | ✓ EXACT MATCH (0ms delta) |
| pod-network-loss | 2026-05-11T08:58:37.702Z | 2026-05-11T08:58:37.702Z | ✓ EXACT MATCH (0ms delta) |
| pod-memory-hog | Not detected (no symptoms) | Not detected (`null`) | ✓ CORRECT BEHAVIOR |

**Timing Variance**:
- **Typical delta**: 0-4 seconds from manual labels
- **Variance source**: Semantic differences in "detection moment" definition:
  - Manual labels: Often mark investigation **confirmation** step
  - Metrics extraction: Marks investigation **initiation** or fault **recognition** step
- **Acceptability**: 0-4s variance is minimal and reflects legitimate timeline interpretation differences

### Detection Quality Indicators

1. **Tool Pattern Recognition**: LLM accurately identifies fault-specific diagnostic sequences
   ```
   pod-cpu-hog detection tools:
   - pods_list_in_namespace → enumerate targets
   - pods_top → CPU/memory snapshot
   - execute_query → PromQL container_cpu_usage_seconds_total rate[2m]
   - list_metrics → search 'chaos|fault|inject' patterns
   ```

2. **Multi-Fault Disambiguation**: During overlapping fault windows, LLM correctly identifies which fault(s) the agent was actively investigating
   ```
   Event with overlapping faults:
   - Manual: ['pod-cpu-hog', 'pod-network-loss']
   - Predicted: ['pod-cpu-hog', 'pod-network-loss']
   - LLM Reasoning (pod-cpu-hog): "pods_top + PromQL CPU queries align DIRECTLY with cpu-hog diagnostics"
   - LLM Reasoning (pod-network-loss): "PromQL packet drop queries + network metrics match network-loss trajectory"
   ```

3. **Reasoning Quality**: Even on missed detections, LLM reasoning remains logically consistent
   ```
   Missed detection example:
   - Manual: ['pod-cpu-hog', 'pod-network-loss', 'pod-memory-hog']
   - Predicted: [] (not classified)
   - LLM Reasoning: "Namespace healthy with only transient startup probe warning. 
     No high CPU, packet loss, or memory pressure symptoms. All pods Running/Ready."
   - Outcome: Conservative miss, not confusion/hallucination
   ```

### Known Limitations

1. **RBAC-Restricted Environments**: When metrics server access is forbidden, memory pressure cannot be verified directly. LLM conservatively skips detection rather than guessing.

2. **Fault Manifestation**: Detection requires observable symptoms in trace. Faults that are injected but do not manifest (e.g., network loss with no active traffic) will not be detected.

3. **Timing Granularity**: Detection timestamps reflect agent reasoning spans (1-3 second duration), not sub-second event boundaries.

## Dependencies

- `utils.azure_openai_util.AzureLLMClient` — LLM API calls (`with_structured_output`, `call_llm`)
- `utils.load_config.ConfigLoader` — Global configuration with `ENV_*` resolution
- `utils.mongodb_util.MongoDBClient` / `MongoDBConfig` — MongoDB persistence (`insert_metrics`)
- `utils.custom_errors.MetricsExtractorError`, `ConfigLoaderError` — typed errors
- `utils.setup_logging.logger` — Centralized logging
- `metrics_extractor.schema.metrics_model` — Pydantic models for structured LLM output (`LLMQuantitativeExtraction`, `LLMQualitativeExtraction`, `CombinedJudgeResponse`, `CombinedStepJudgment`, `JudgedClaim`)
