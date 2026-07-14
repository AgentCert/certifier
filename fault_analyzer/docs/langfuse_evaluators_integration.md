# Langfuse LLM Evaluators — Fault Labeling Integration

## What Langfuse LLM Evaluators Are

Langfuse has a built-in **LLM-as-a-judge** feature. Instead of calling Azure GPT-4o directly in `FaultEventClassifier`, Langfuse runs a configured evaluator prompt against each observation (span) and stores the result as a **score** on that observation. You then read those scores back.

---

## Is It Possible?

**Yes, but with one structural constraint to design around:**

`FaultEventClassifier` is stateful — it passes a `known_faults` context block (all faults injected so far) into each LLM call. Langfuse evaluators are stateless per-observation — they only see that span's `input`/`output`. To replicate the current flow, inject the fault context via the evaluator's **variable mapping** before triggering it.

---

## Full Flow

### Step 1: Design Your Evaluator Prompt in Langfuse UI

In Langfuse, go to **Evaluations → Configure** and create an LLM evaluator.

The prompt template uses variable placeholders that Langfuse fills from the observation:

```
You are a fault classifier for Kubernetes chaos engineering experiments.

## Known Faults
{{known_faults_context}}

## Event to Classify
Input: {{input}}
Output: {{output}}
Span Name: {{name}}

Classify which fault(s) this event belongs to.
Return JSON: {"related_faults": ["fault_id_1"], "fault_detected": null, "fault_mitigated": null, "confidence": 0.9}
```

`{{known_faults_context}}` is injected per-observation at evaluation trigger time via the API (Step 5).

Configure the evaluator to return a **categorical score** (the JSON string) plus a **comment** (reasoning).

---

### Step 2: Fetch the Trace (Same as Now)

Nothing changes. Export the Langfuse trace as a flat JSON array of spans.

---

### Step 3: Pass 1 — Deterministic Bucket Creation (Same as Now)

Identical to current pipeline. Walk `fault: *` spans, create `FaultBucket` objects, extract `injection_metadata`. No LLM needed. This produces the list of known faults with their temporal windows.

---

### Step 4: Inject `known_faults_context` into Each Span's Metadata

**This is the key new step** that makes evaluators work with the stateful pipeline.

For each non-fault span needing classification, determine which faults were in flight at its timestamp using `_temporally_active_faults`, then build the compact fault context block using the same logic as `_compact_fault_context`.

```python
for event in non_scaffold_events:
    eligible_faults = _temporally_active_faults(known_faults, event["startTime"])
    # Pass context to evaluator trigger in Step 5
    event["_eligible_faults"] = eligible_faults
```

---

### Step 5: Trigger Evaluations via Langfuse API

For each observation to classify, trigger the evaluator with the fault context injected as a variable override:

```python
import langfuse

client = langfuse.Langfuse()

for event in ambiguous_events:  # only multi-fault-in-flight events
    client.create_evaluation(
        trace_id=trace_id,
        observation_id=event["id"],
        evaluator_id="your-fault-classifier-evaluator-id",
        variables={
            "known_faults_context": build_known_faults_block(event["_eligible_faults"])
        }
    )
```

For N observations this triggers N async evaluator jobs in Langfuse. Each calls the configured LLM and stores results as scores.

---

### Step 6: Poll for Evaluation Completion

Evaluations are async. Poll until all scores appear:

```python
import time

def wait_for_scores(client, trace_id, observation_ids, evaluator_name, timeout=120):
    deadline = time.time() + timeout
    pending = set(observation_ids)

    while pending and time.time() < deadline:
        scores = client.get_scores(trace_id=trace_id, name=evaluator_name)
        for score in scores:
            pending.discard(score.observation_id)
        if pending:
            time.sleep(3)

    return scores
```

---

### Step 7: Read Scores and Reconstruct Classifications

Scores come back as:

```json
{
  "observation_id": "evt_abc",
  "name": "fault-classifier",
  "value": "1.0",
  "comment": "{\"related_faults\": [\"fault_1\"], \"fault_detected\": null, \"fault_mitigated\": null, \"confidence\": 0.88}"
}
```

Parse the `comment` field back into the existing `EventClassification` Pydantic model:

```python
import json
from fault_analyzer.schema import EventClassification

classifications = {}
for score in scores:
    raw = json.loads(score.comment)
    classifications[score.observation_id] = EventClassification(
        event_id=score.observation_id,
        **raw
    )
```

---

### Step 8: Pass 2 — Bucket Assignment (Same as Now)

The same `EventClassification` objects the current pipeline produces are now available. Feed them into `_place_event_in_buckets`, `_record_fault_detection`, `_close_fault` — **zero changes needed** in the rest of the pipeline.

---

## What Changes vs. What Stays the Same

| Current | With Langfuse Evaluators |
|---|---|
| `FaultEventClassifier.classify_batch()` calls Azure GPT-4o directly | Langfuse calls GPT-4o on your behalf, stores score |
| Prompt lives in `prompt/v1/prompt.yml` | Prompt lives in Langfuse UI (version-controlled there) |
| Batch size = N events per call | 1 evaluation per observation (no batching) |
| Synchronous (`await` in pipeline) | Async — must poll for results |
| Token tracking via `total_input_tokens` | Token tracking via Langfuse's built-in cost tracking |
| `fallback_classify` on error | Langfuse retries; fallback still needed for timeouts |

---

## Key Limitation: No Native Batching

Langfuse evaluators fire one LLM call per observation. For a trace with 200 spans, that is 200 LLM calls vs. the current batched approach — more expensive and slower.

**Mitigation**: Keep the existing deterministic skip logic and only trigger evaluators for ambiguous multi-fault events (same condition that currently routes to `classify_batch`). This keeps the evaluator call count small, matching the current behavior.

---

## Summary

```
Fetch trace
    → Deterministic bucket creation from fault: * spans (unchanged)
    → For each ambiguous span: inject fault context → trigger Langfuse evaluator
    → Poll until all scores arrive
    → Parse scores back to EventClassification
    → Existing _place_event_in_buckets / _record_fault_detection / _close_fault (unchanged)
    → Write output files (unchanged)
```

Prompt management and LLM cost visibility move into Langfuse. `FaultBucketingPipeline` orchestration logic stays intact. The main tradeoff is losing batching in exchange for centralized prompt versioning and cost tracking inside Langfuse.
