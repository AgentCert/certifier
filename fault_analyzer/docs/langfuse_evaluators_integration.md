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
        │       │       └── side effect: inject known_faults_context on overlap observations
        │       └── Output: per-fault bucket JSON files
        │
        └── Langfuse evaluator method (evaluate_existing_trace.py)
                ├── Fault metadata extraction (deterministic, no LLM)  ← TO IMPLEMENT
                ├── Inject known_faults_context on ALL GENERATION observations
                ├── Trigger fault-event-classifier-lf on each observation
                └── Output: scores on Langfuse trace (binôme extracts them)
```

The two methods are **decoupled by design**: the Langfuse evaluator method must not use the main pipeline's classification result (that would be information leakage). Each method produces its own bucketing independently.

---

## Current State

### What is implemented

**`fault_analyzer/scripts/fault_bucketing.py` — `_duplicate_to_langfuse_evaluator`**

Called as a side effect during the main pipeline's LLM classification step (overlap region only — events with >1 fault in flight simultaneously). For each such observation, it injects a `known_faults_context` block into the observation's Langfuse metadata. This gives the Langfuse evaluator the fault context it needs to classify independently.

Note: this only covers the overlap region. Observations assigned deterministically (single fault in flight) are not enriched here.

**`evaluate_existing_trace.py`**

Standalone CLI that orchestrates the full Langfuse evaluator loop on an existing trace:

| Step | Function | Status |
|---|---|---|
| Load trace (local file or fetch from Langfuse) | `load_local_file_to_dest` / `fetch_trace_to_file` | ✅ done |
| Fault metadata extraction + context injection on overlap observations | `run_bucketing` (full pipeline — temporary) | ⚠️ depends on main pipeline |
| Inject context on ALL remaining GENERATION observations | `inject_context_all_generations` | ✅ done |
| Trigger `fault-event-classifier-lf` on every GENERATION observation | `trigger_evaluator` | ✅ done |

**`fault_analyzer/docs/langfuse-evaluator-config.md`**

Documents the evaluator prompt, variable mapping, and score configuration.

---

## What Is Left To Implement

### `fault_analyzer/scripts/langfuse_bucketing.py` — new file

The main missing piece is a **lightweight, LLM-free fault metadata extractor** that replaces `run_bucketing` (currently the full `FaultBucketingPipeline`) in `evaluate_existing_trace.py`.

Currently `evaluate_existing_trace.py` calls `run_bucketing`, which runs the entire main pipeline including the Azure GPT-4o LLM classifier. This creates a dependency on the main pipeline. If the main pipeline fails or is unavailable, `inject_context_all_generations` receives empty buckets and injects a useless empty context — the evaluator then classifies blind.

The new file should contain a single entry-point function, e.g. `extract_fault_metadata(trace_file, trace_id)`, which:

1. Loads and sorts the trace JSON chronologically
2. Scans spans for the `fault: *` naming pattern (deterministic — no LLM)
3. For each matching span, extracts fault metadata: name, timestamps, ground truth, injection metadata, target, SLA, ideal course of action
4. Builds minimal `FaultBucket` objects (metadata only — no event assignment)
5. Returns a `dict[fault_id, FaultBucket]` ready to feed `inject_context_all_generations`

This replicates only Pass 1 of `FaultBucketingPipeline` (the deterministic part), with no dependency on `FaultEventClassifier`, Azure OpenAI, or any LLM infrastructure.

Once implemented, `evaluate_existing_trace.py` replaces its `run_bucketing` call with `extract_fault_metadata`, making the Langfuse evaluator method fully independent.

### Score extraction (binôme)

After `fault-event-classifier-lf` runs, its output is stored as scores on the Langfuse trace (one score per GENERATION observation, value = confidence, reasoning = justification sentence). The `related_faults` field is embedded in the LLM's raw JSON output.

Reconstructing the bucketing result — the `event_id → [fault_id, ...]` mapping — requires fetching these scores from the Langfuse API and parsing the evaluator's JSON output. This is out of scope for this method and is handled by the metrics extraction module (binôme).

---

## File Map

| File | Role |
|---|---|
| `fault_analyzer/scripts/fault_bucketing.py` | Main pipeline — `_duplicate_to_langfuse_evaluator` injects context on overlap observations as a side effect |
| `fault_analyzer/scripts/langfuse_bucketing.py` | **To implement** — lightweight fault metadata extractor (no LLM) |
| `evaluate_existing_trace.py` | CLI orchestrating the full Langfuse evaluator loop |
| `fault_analyzer/docs/langfuse-evaluator-config.md` | Evaluator prompt + score configuration reference |
| `fault_analyzer/docs/langfuse_evaluators_integration.md` | This file |
