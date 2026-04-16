# Plan: Exclude Fault Injection Span from Bucket Events

## Problem

In `fault_analyzer/scripts/fault_bucketing.py`, the `_create_fault_bucket_from_span` method currently:

1. **Includes the fault injection span as an event** in the bucket (`events=[event]`).
2. **Uses the injection span's `endTime` as `mitigated_at`** when `fault.status == "completed"`, and calls `_close_fault` with that timestamp.

Both are incorrect:

- The injection span represents the chaos experiment infrastructure, not the agent's behavior. Including it in `bucket.events` pollutes the bucket with a non-agent event, which downstream components (metrics extractor, LLM classifier) then process as if it were agent activity.
- The injection span's `endTime` is when the chaos experiment finished — not when the agent mitigated the fault. This produces a false `mitigated_at` in the bucket output.

## Current Flow (fault_bucketing.py, `_create_fault_bucket_from_span`)

```
1. Extract fault_name, metadata, ground_truth from the injection span
2. Create FaultBucket with events=[event]            ← PROBLEM: includes injection span
3. If fault.status == "completed":
     _close_fault(fault_id, mitigated_at=event.endTime)  ← PROBLEM: wrong timestamp
```

## Proposed Changes

### Change 1: `_create_fault_bucket_from_span` — exclude injection span from events

**File:** `fault_analyzer/scripts/fault_bucketing.py`  
**Method:** `_create_fault_bucket_from_span`

- Change `events=[event]` → `events=[]` in the `FaultBucket` constructor.
- The injection span is used only for metadata extraction (fault_name, target_pod, namespace, injection_timestamp, ground_truth, experiment/run IDs). After that, it should not be stored as a bucket event.

### Change 2: `_create_fault_bucket_from_span` — do not close bucket or set mitigated_at from injection span

**File:** `fault_analyzer/scripts/fault_bucketing.py`  
**Method:** `_create_fault_bucket_from_span`

- Remove the block:
  ```python
  if fault_status == "completed":
      self._close_fault(fault_id, mitigated_at=event.get("endTime"))
  ```
- The bucket must remain **active** after creation. The **only** legitimate way to close a bucket is when the LLM classifier in Pass 2 identifies a real mitigation event via `classification.fault_mitigated`. No other code path should close the bucket or set `mitigated_at`.
- If the classifier never detects mitigation, the bucket stays open — this is the correct outcome (the agent did not mitigate the fault).

### Change 3: `_create_fault_bucket_from_span` — dedup logic should not add injection span to existing bucket

**File:** `fault_analyzer/scripts/fault_bucketing.py`  
**Method:** `_create_fault_bucket_from_span`

- The two early-return dedup blocks currently do `bucket.events.append(event)` when a matching active bucket exists. Since the injection span should be excluded from events, these should just return without appending.

### Change 4: Update tests

**File:** `fault_analyzer/tests/test_fault_bucketing.py`

- Update any tests for `_create_fault_bucket_from_span` that assert the injection span is in `bucket.events`.
- Add a test verifying that a completed fault injection span results in an **active** bucket with `events=[]` and `mitigated_at=None` (bucket is not closed by the injection span).

## Files Affected

| File | Changes |
|------|---------|
| `fault_analyzer/scripts/fault_bucketing.py` | Changes 1–3 |
| `fault_analyzer/tests/test_fault_bucketing.py` | Change 4 |

## Downstream Impact

- **Metrics extractor** (`metrics_extractor/scripts/metrics_extractor_from_trace.py`): Benefits from this change. The bucket's `events` list will only contain agent spans, making LLM-based span analysis more accurate. The `mitigated_at` field will only be set when the classifier identifies a real mitigation event, so the existing timestamp validation logic in the metrics extractor becomes more reliable.
- **Bucket output JSON** (`data/new_reports/fault_buckets/`): The `event_count` will decrease by 1 (injection span excluded). `mitigated_at` will be `null` when no agent mitigation is identified, instead of being falsely set to the injection span's end time.
- **Bucket closure**: Only happens via `_close_fault` called from the classifier mitigation path in `run()`. No other code path closes or sets `mitigated_at`.
