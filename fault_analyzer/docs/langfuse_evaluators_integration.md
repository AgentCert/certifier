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
| Write `batch_classification_trace.json` (local file) | `trigger_evaluator` (via `output_dir` param) | ✅ done |

### Trigger mechanism — how it actually works

The Langfuse public API does **not** expose an endpoint to trigger evaluators on demand (no `/api/public/evals/*` in Langfuse 3.x). The evaluation rule fires only at first ingestion of an observation, **not** on `ObservationUpdate` events. Re-ingesting existing observations does not re-queue evaluations in the Langfuse worker.

`trigger_evaluator` therefore replicates the Langfuse evaluator behaviour directly, without going through the Langfuse worker:

1. Fetches the evaluator prompt from `/api/public/unstable/evaluators`
2. Calls `AzureLLMClient` (same Azure deployment as the main pipeline) for each GENERATION observation that has `known_faults_context` in its metadata, substituting `{{metadata}}`, `{{input}}`, `{{output}}` from the observation fields
3. Parses the JSON response (`related_faults`, `confidence`, `reasoning`)
4. Creates one Langfuse score per observation via `client.create_score()` (value=confidence, comment=reasoning)
5. If `output_dir` is provided, writes a `batch_classification_trace.json` file (one entry per scored observation) in the same format as the main pipeline's debug output — consumable directly by `load_predictions()` in `evaluator.py`

This is synchronous from the caller's perspective: scores appear in the Langfuse UI and the local file is written as soon as the function returns, without depending on the worker queue.

### Local output — `batch_classification_trace.json`

`trigger_evaluator` writes one JSON entry per successfully scored observation:

```json
{
  "event_id": "<observation_id>",
  "name": "<span name>",
  "classification": {
    "related_faults": ["fault_id_1"],
    "confidence": 0.9
  },
  "tokens_in": 120,
  "tokens_out": 45,
  "source": "llm",
  "deterministic_assignment": false
}
```

Token counts (`tokens_in` / `tokens_out`) are extracted from the `usage` dict returned by `AzureLLMClient.call_llm`. The file is written to `output_dir` passed to `trigger_evaluator`; when `--output-dir` is not provided on the CLI the file lands in a temporary directory and is deleted at process exit.

### Evaluator lookup — `unstable` API

LLM-as-judge evaluators are **not** returned by `/api/public/score-configs` — score configs and evaluator configs are distinct entities in Langfuse. Evaluators are accessible at:

```
GET /api/public/unstable/evaluators
```

This endpoint is marked unstable by Langfuse (no backwards-compatibility guarantee) but is the only public API that lists evaluator configs and their prompts. `trigger_evaluator` uses it to fetch the prompt template before running the LLM calls.

---

## Partial exploitation of the Langfuse evaluator

The secondary pipeline does not fully exploit the Langfuse evaluator tool. What is used: the prompt (fetched via `/api/public/unstable/evaluators`) and score storage (`client.create_score()`). What is bypassed:

| Langfuse feature | What the pipeline does instead |
|---|---|
| Automatic triggering via evaluation rule + worker | Direct Azure OpenAI call in `trigger_evaluator` |
| LLM connection managed in the Langfuse UI | Certifier credentials from `.env` |
| Two-pass LLM post-processing (reasoning + score extraction) | Direct single-pass JSON parsing |

**Root cause:** the Langfuse evaluator is designed for real-time traces. The evaluation rule only fires on first ingestion of an observation, not on `ObservationUpdate` events. For existing traces, no public API exists to replay the worker — the bypass is therefore unavoidable.

**Ideal integration (live traces):** if the agent injected `needs_fault_classification: "true"` directly into observation metadata at creation time, the evaluator would run automatically on every new trace with no manual intervention. `evaluate_existing_trace.py` would then only serve to backfill historical traces that lacked context at ingestion time.

---

## What Is Left To Do

### 1. End-to-end test on a real trace

`evaluate_existing_trace.py` has not yet been fully validated on a live Langfuse trace. A smoke test should verify:
- `fault: *` spans are correctly detected and fault metadata is extracted
- `known_faults_context` appears in the metadata of GENERATION observations in the Langfuse UI
- `fault-event-classifier-lf` scores appear on the trace with non-empty `related_faults` in the reasoning
- `batch_classification_trace.json` is written to `--output-dir` with correct `related_faults` per entry

### 2. Parallel metrics extraction from Langfuse (binôme)

Once the bucketing output (`batch_classification_trace.json`) is available, the binôme handles extracting metrics (TTD, TTR, etc.) from the Langfuse scores and aggregating them in parallel with the main pipeline's metrics.
