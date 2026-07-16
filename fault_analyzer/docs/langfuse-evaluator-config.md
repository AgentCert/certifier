# Langfuse Evaluator — `fault-event-classifier-lf`

## Purpose

LLM-judge evaluator configured in the Langfuse UI. It provides a **parallel, independent bucketing method** alongside the main pipeline: it classifies each GENERATION observation of a trace into one or more fault buckets, with no access to the main pipeline's classification result.

---

## Variables injected into the prompt

| Variable | Langfuse source | Content |
|---|---|---|
| `{{metadata}}` | Observation `metadata` field | The `known_faults_context` block injected by `_duplicate_to_langfuse_evaluator` (known faults: symptoms, target, remediation, ideal actions) |
| `{{input}}` | Observation `input` field | Agent reasoning (tool arguments, chain-of-thought) |
| `{{output}}` | Observation `output` field | Action or response produced by the agent |

---

## Evaluation prompt

```
You are a fault classifier for Kubernetes chaos engineering experiments.

## Known Faults (injected into this observation's context)
{{metadata}}

## Observation to classify
Input: {{input}}
Output: {{output}}

---

Based on the known faults listed in the metadata above (look for the key
"known_faults_context"), determine which fault(s) this observation relates to.

Return ONLY valid JSON:
{
  "related_faults": ["fault_id_1"],
  "confidence": 0.9,
  "reasoning": "one sentence explaining the match"
}

Rules:
- related_faults must only contain fault_ids from the known faults list
- confidence is a float between 0.0 and 1.0
- If the observation is unrelated to any fault, return related_faults as []
```

The LLM produces a structured JSON with three fields: the list of related faults, a confidence score, and a one-sentence reasoning.

---

## Score configuration

The score stored in Langfuse is of type **Numeric**.

Langfuse runs two additional extraction passes on the LLM output:

| Field | Post-processing prompt | Stored value |
|---|---|---|
| **Reasoning** | `Extract the reasoning field from the JSON below. Return only the reasoning string.` | The LLM's justification sentence |
| **Score (numeric value)** | `Extract the confidence value from the JSON below. Return only a number between 0 and 1.` | Confidence as a float (0–1) |

---

## What is visible in the Langfuse UI

For each GENERATION observation of the evaluated trace:
- A score named `fault-event-classifier-lf` with the numeric confidence value
- The associated reasoning string

The `related_faults` (the actual bucketing result) are in the raw LLM output but **are not stored as a dedicated score field**. To reconstruct the `event_id → [fault_id, ...]` mapping and compare it against the main pipeline's bucketing, scores must be fetched and parsed from the Langfuse API — this is the responsibility of the metrics extraction module (binôme).

---

## Known constraints

- The evaluator only works on **traces already present in Langfuse** (no real-time mode).
- Only `GENERATION` observations are evaluated. `fault:` spans and scaffolding spans (`workflow-step`, `experiment-triggered`) receive no score.
- Overlap-region observations (>1 fault in flight simultaneously) receive a `known_faults_context` restricted to temporally eligible faults. Observations outside the overlap receive the context of all known faults (via `inject_context_all_generations`).
