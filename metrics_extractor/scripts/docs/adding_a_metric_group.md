# Adding a MetricGroup

## 1. The contract

A `MetricGroup` is a class that owns one coherent batch of LLM calls and returns a flat dict of metric names to values.

Two things are required:

```python
class MyGroup(MetricGroup):
    provides: ClassVar[list[str]] = ["metric_a", "metric_b"]

    async def execute(self, context: ExtractionContext) -> dict:
        ...
```

**`provides`** declares every key your `execute` may return. The orchestrator uses this to decide whether to run your group at all when a caller requests a subset of metrics.

**`execute`** receives an `ExtractionContext` with:

| Attribute | What it gives you |
|---|---|
| `context.spans` | Chronologically ordered list of raw span dicts for this fault bucket |
| `context.bucket_metadata` | Fault injection metadata (fault name, timestamps, namespace, …) |
| `context.extractor` | The `TraceMetricsExtractor` instance; call `context.extractor.llm_client` to reach the LLM client |
| `context.results` | Shared dict — earlier groups write here, later groups read from it |

**Hard rule:** any number derived from an LLM-identified span must be computed in code. If an LLM tells you span `abc` is the resolution span, you look up `abc`'s timestamp yourself and subtract. You never ask the LLM to compute the delta and then use that number directly — LLMs make arithmetic errors, and the result would not be reproducible.

---

## 2. How discovery works

Inheriting from `MetricGroup` is sufficient. As long as your class is imported somewhere that runs at startup (typically the module you add it to, once that module is imported by the package), it appears automatically in `list_available_metrics()` and becomes selectable via `run_extraction(requested=["metric_a", ...])`.

There is no registry file to edit. Adding the class is the entire registration step.

---

## 3. Worked example — TimeToResolutionGroup

Domain: a customer-support agent. The group provides two metrics: `time_to_resolution` and `resolution_success`.

**Prompt:**

```
You are analyzing a customer support conversation, given as a chronologically ordered list of spans (agent messages, tool calls, customer messages).

Identify two spans:
1. INTENT SPAN: the first span where the agent demonstrates it has correctly understood what the customer needs — not just acknowledged the message, but shown comprehension of the actual request.
2. RESOLUTION SPAN: the span where the agent completes an action that resolves the customer's request. If no such span exists, return null for resolution_span_id.

Return a JSON object with exactly these fields:
{
  "intent_span_id": "<span id or null>",
  "intent_reason": "<one sentence>",
  "resolution_span_id": "<span id or null>",
  "resolution_reason": "<one sentence>"
}
```

**Implementation:**

```python
from datetime import datetime
from metrics_extractor.scripts.metric_groups import MetricGroup, ExtractionContext


class TimeToResolutionGroup(MetricGroup):
    provides = ["time_to_resolution", "resolution_success"]

    PROMPT = """You are analyzing a customer support conversation, given as a chronologically
ordered list of spans (agent messages, tool calls, customer messages).

Identify two spans:
1. INTENT SPAN: the first span where the agent demonstrates it has correctly
   understood what the customer needs — not just acknowledged the message,
   but shown comprehension of the actual request.
2. RESOLUTION SPAN: the span where the agent completes an action that
   resolves the customer's request. If no such span exists, return null
   for resolution_span_id.

Return a JSON object with exactly these fields:
{
  "intent_span_id": "<span id or null>",
  "intent_reason": "<one sentence>",
  "resolution_span_id": "<span id or null>",
  "resolution_reason": "<one sentence>"
}"""

    async def execute(self, context: ExtractionContext) -> dict:
        sorted_spans = sorted(context.spans, key=lambda s: s.get("startTime", ""))
        span_start_times = {s["id"]: s.get("startTime") for s in sorted_spans}
        span_end_times = {s["id"]: s.get("endTime") for s in sorted_spans}

        prepared = [
            {"id": s["id"], "startTime": s.get("startTime"),
             "endTime": s.get("endTime"), "output": s.get("output", "")}
            for s in sorted_spans
        ]

        extractor = context.extractor
        extractor._init_llm_client()
        result, _ = await extractor.llm_client.call_llm(
            model_name="gpt-4o",
            system_prompt=self.PROMPT,
            messages=f"Spans:\n{prepared}",
        )

        intent_id = result.get("intent_span_id")
        resolution_id = result.get("resolution_span_id")

        intent_ts = span_start_times.get(intent_id) if intent_id else None
        resolution_ts = span_end_times.get(resolution_id) if resolution_id else None

        time_to_resolution = None
        if intent_ts and resolution_ts:
            t0 = datetime.fromisoformat(intent_ts.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(resolution_ts.replace("Z", "+00:00"))
            time_to_resolution = (t1 - t0).total_seconds()

        return {
            "time_to_resolution": time_to_resolution,
            "resolution_success": resolution_id is not None,
        }
```

This mirrors `SpanIdentificationGroup`'s existing pattern exactly — one LLM call identifies spans by ID, then code computes the delta and the boolean, and the LLM's own output is never used as the final numeric value. Note also that the prompt text above is domain vocabulary a team supplies: the engine itself does not know what "resolution" or "intent" means. This is precisely what a domain pack would carry — the prompt text, the span-selection criteria, the field names — per the coupling-audit discussion from the generalization study.

---

## 4. What not to do

Do not write one class per metric, each making its own LLM call. If `time_to_resolution` and `resolution_success` each had their own group and their own call to identify the resolution span, you would make two LLM calls to answer the same question, doubling cost and runtime. `SpanIdentificationGroup` is the pattern to follow: it groups `time_to_detect`, `time_to_mitigate`, and `detection_success` under a single span-identification call, then derives all three values in code. Batch metrics that share an LLM call into one group.
