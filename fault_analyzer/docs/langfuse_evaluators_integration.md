# Langfuse Evaluator — Integration & Status

## Overview

The Langfuse evaluator method (`fault-event-classifier-lf`) is a **parallel, independent bucketing method** that runs alongside the main pipeline without depending on it. Instead of calling Azure GPT-4o directly in `FaultEventClassifier`, the Langfuse LLM-judge evaluator classifies each GENERATION observation and stores the result as a score on that observation.

For details on the evaluator configuration (prompt, score type, variable mapping), see [`langfuse-evaluator-config.md`](langfuse-evaluator-config.md).

---

## Architecture — Two Parallel Methods

```
Raw Langfuse trace
        │
        ├── Main pipeline (fault_bucketing.py)
        │       ├── Pass 1: deterministic fault detection (fault: * spans)
        │       ├── Pass 2: LLM classifier (FaultEventClassifier / Azure GPT-4o)
        │       └── Output: per-fault bucket JSON files
        │
        └── Langfuse evaluator method (evaluate_existing_trace.py)
                ├── Fault metadata extraction (langfuse_bucketing.py — deterministic, no LLM)
                ├── Inject known_faults_context on ALL GENERATION observations
                ├── Trigger fault-event-classifier-lf on each observation
                └── Output: scores on Langfuse trace (binôme extracts them)
```

The two methods are **completely decoupled**: `fault_bucketing.py` has no Langfuse SDK dependency and is unaware of the secondary method. Each method produces its own bucketing independently.

---

## File Map

| File | Role |
|---|---|
| `fault_analyzer/scripts/fault_bucketing.py` | Main pipeline — no Langfuse references |
| `fault_analyzer/scripts/langfuse_bucketing.py` | Lightweight fault metadata extractor (Pass 1 only, no LLM) |
| `evaluate_existing_trace.py` | CLI orchestrating the full Langfuse evaluator loop |
| `fault_analyzer/docs/langfuse-evaluator-config.md` | Evaluator prompt + score configuration reference |
| `fault_analyzer/docs/langfuse_evaluators_integration.md` | This file |

---

## Current State — What Is Implemented

### `fault_analyzer/scripts/langfuse_bucketing.py`

Standalone fault metadata extractor. Entry point: `extract_fault_metadata(trace_file: Path) -> dict[fault_id, FaultBucket]`.

- Loads and sorts the trace chronologically
- Extracts agent metadata from early spans (agent_id, experiment_id, run_id…)
- Scans `fault: *` spans deterministically — builds minimal `FaultBucket` objects (metadata only, `events=[]`)
- Fallback: creates a `single_fault` bucket if no `fault: *` span is found
- No dependency on `FaultBucketingPipeline`, `FaultEventClassifier`, or Azure OpenAI

### `evaluate_existing_trace.py`

CLI orchestrator for the full Langfuse evaluator loop. Steps:

| Step | Function | Status |
|---|---|---|
| Load trace (local file or fetch from Langfuse) | `load_local_file_to_dest` / `fetch_trace_to_file` | ✅ done |
| Extract fault metadata (deterministic, no LLM) | `extract_fault_metadata` from `langfuse_bucketing.py` | ✅ done |
| Inject `known_faults_context` on ALL GENERATION observations | `inject_context_all_generations` | ✅ done |
| Trigger `fault-event-classifier-lf` on every GENERATION observation | `trigger_evaluator` | ✅ done |

---

## What Is Left To Do

### 1. End-to-end test on a real trace

`evaluate_existing_trace.py` has not yet been validated on a live Langfuse trace. A smoke test should verify:
- `fault: *` spans are correctly detected and fault metadata is extracted
- `known_faults_context` appears in the metadata of GENERATION observations in the Langfuse UI
- `fault-event-classifier-lf` scores appear on the trace with non-empty `related_faults` in the reasoning

### 2. Score extraction and bucketing output (this method's responsibility)

After `fault-event-classifier-lf` runs, its output is stored as Langfuse scores — one per GENERATION observation, with a numeric confidence value and a reasoning string containing `related_faults`.

To make this method's bucketing comparable to the main pipeline's output, a score extractor must be implemented in `evaluate_existing_trace.py` (or a dedicated module) that:

1. Fetches `fault-event-classifier-lf` scores from the Langfuse API for the evaluated trace
2. Parses each score's reasoning JSON to extract `related_faults` and `confidence`
3. Writes a `batch_classification_trace.json` file in the same format as the main pipeline's output

The expected format per entry (matching `load_predictions` in `fault_analyzer/tests/evaluation/evaluator.py`):
```json
{
  "event_id": "<observation_id>",
  "name": "<span name>",
  "classification": {
    "related_faults": ["fault_id_1"],
    "confidence": 0.9
  },
  "tokens_in": 0,
  "tokens_out": 0,
  "deterministic_assignment": false
}
```

Once this file exists, the existing `evaluator.py` harness (`load_predictions` + `evaluate`) can compare the two bucketing methods directly against the same ground truth labels.

### 3. Parallel metrics extraction from Langfuse (binôme)

Once the bucketing output is available, the binôme handles extracting metrics (TTD, TTR, etc.) from the Langfuse scores and aggregating them in parallel with the main pipeline's metrics.
