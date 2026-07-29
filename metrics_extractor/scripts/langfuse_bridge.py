"""
Langfuse ↔ Phase 1 bridge.

Four public entry points:

  list_fault_ids_in_trace(trace_id)                        → List[str]
  build_bucket_json_from_langfuse(trace_id, fault_id)      → bucket dict
  push_extraction_result_to_langfuse(trace_id, fault_id, result)  → None
  run_phase1_on_langfuse_trace(trace_id, fault_id)         → None

Multi-fault design
------------------
A single Langfuse trace may contain multiple overlapping faults.  One bucket
dict — and one set of Phase 1 scores — is produced per (trace_id, fault_id)
pair, mirroring the one-bucket-per-fault output of Phase 0.

Per-observation fault assignment: bucket_label scores
------------------------------------------------------
``FaultBucketingPipeline`` (Phase 0, fault_bucketing.py) posts one
``bucket_label`` score per observation after it assigns each event to a fault
bucket.  The score has:

  Score name   : "bucket_label"  (constant _BUCKET_LABEL_SCORE_NAME)
  data_type    : CATEGORICAL
  Value        : fault_id string (e.g. ``"pod-cpu-hog"`` or ``"pod-cpu-hog_2"``),
                 or comma-separated fault_ids for events that overlap multiple
                 active faults (e.g. ``"pod-cpu-hog, pod-network-loss"``)
  Scope        : per-observation (observation_id is always set)

A companion ``bucket_confidence`` NUMERIC score is pushed alongside each
``bucket_label`` (1.0 for deterministic assignment, < 1.0 for LLM-resolved
overlap), but it is not used by this bridge.

A trace-level ``fault_list`` CATEGORICAL score (no observation_id) carries the
comma-separated list of all fault **names** present in the trace (not fault_ids)
and is used as a cross-check in ``list_fault_ids_in_trace``.

fault_id vs fault_name
-----------------------
``FaultBucketingPipeline`` assigns a ``fault_id`` to each bucket:

  - First occurrence of a fault named ``"pod-cpu-hog"``  → fault_id ``"pod-cpu-hog"``
  - Second occurrence of the same fault                 → fault_id ``"pod-cpu-hog_2"``
  - Third occurrence                                    → fault_id ``"pod-cpu-hog_3"``

The injection span name (``"fault: pod-cpu-hog"``) is NEVER suffixed — all
occurrences share the identical span name.  ``bucket_label`` values carry the
suffixed fault_id directly, so filtering is a simple direct string match.

Known limitation: if a second injection of the same fault name arrives while
the first occurrence's bucket is still active, Phase 0 does not create a
``_2`` bucket.  In that race condition ``bucket_label`` may still carry the
bare fault_name.  This is a Phase 0 pre-condition — the bridge handles it
gracefully via the None-fallback in ``_fetch_fault_observation_ids``.

Score suffixing
---------------
Pushed scores are suffixed with the fault_id so multiple faults on the same
trace do not overwrite each other:
  ttd_pod-cpu-hog, ttr_pod-cpu-hog, input_tokens_pod-cpu-hog, …

Credentials are read from LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY,
LANGFUSE_SECRET_KEY environment variables (same as the rest of the project).

Langfuse SDK constraint: requires langfuse>=3.0.0,<4.0.0 (see requirements.txt).
Scores are pushed via ``Langfuse.create_score()`` which accepts
  value: float  for data_type NUMERIC / BOOLEAN
  value: str    for data_type CATEGORICAL
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional, Set

try:
    from utils.setup_logging import logger
except ImportError:
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

from metrics_extractor.schema.data_models import ExtractionResult
from metrics_extractor.scripts.metrics_extractor_from_trace import (
    extract_metrics_from_trace_dict,
)

# ---------------------------------------------------------------------------
# Phase 0 score names (fault_bucketing.py)
# ---------------------------------------------------------------------------

# Per-observation fault-assignment score pushed by FaultBucketingPipeline.
# Value is a fault_id string (suffixed when applicable) or comma-separated
# fault_ids for overlap events.
_BUCKET_LABEL_SCORE_NAME = "bucket_label"

# Trace-level score listing all fault names present (comma-separated, no observation_id).
_FAULT_LIST_SCORE_NAME = "fault_list"


# ---------------------------------------------------------------------------
# Langfuse client factory
# ---------------------------------------------------------------------------

def _get_langfuse_client():
    """Instantiate a Langfuse client from environment variables."""
    try:
        from langfuse import Langfuse
    except ImportError:
        raise RuntimeError(
            "langfuse package is not installed. Run: pip install 'langfuse>=3.0.0,<4.0.0'"
        )

    base_url = os.environ.get("LANGFUSE_HOST", "").strip()
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()

    missing = [
        name
        for name, val in [
            ("LANGFUSE_HOST", base_url),
            ("LANGFUSE_PUBLIC_KEY", public_key),
            ("LANGFUSE_SECRET_KEY", secret_key),
        ]
        if not val
    ]
    if missing:
        raise RuntimeError(
            f"Missing required Langfuse environment variable(s): {', '.join(missing)}"
        )

    return Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=base_url,
        timeout=120,
    )


# ---------------------------------------------------------------------------
# Helpers for metadata extraction
# ---------------------------------------------------------------------------

def _parse_json_field(raw: Any) -> Dict[str, Any]:
    """Return *raw* as a dict, parsing from JSON string if necessary."""
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return raw if isinstance(raw, dict) else {}


def _first(*args: Any) -> Any:
    """Return the first argument that is not None."""
    for v in args:
        if v is not None:
            return v
    return None


def _coerce(d: Dict[str, Any], *keys: str) -> Any:
    """Return the first non-None value found in *d* for any of *keys*."""
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


# ---------------------------------------------------------------------------
# Internal: per-fault observation ID resolution
# ---------------------------------------------------------------------------

def _fetch_fault_observation_ids(
    client: Any,
    trace_id: str,
    fault_id: str,
) -> Optional[Set[str]]:
    """Return the set of observation IDs that belong to *fault_id*, or None on error.

    Uses ``bucket_label`` CATEGORICAL scores pushed by ``FaultBucketingPipeline``
    (Phase 0).  Each score value is the fault_id string (suffixed when
    applicable) or a comma-separated list of fault_ids for overlap events.

    Matching is a direct string comparison: split each score value on ``","``
    and check whether *fault_id* appears after stripping whitespace.  No suffix
    parsing or timing-window filtering is needed.

    Returns None when scores cannot be fetched or no ``bucket_label`` scores
    exist (Phase 0 not yet run); the caller should then fall back to including
    all non-injection observations.
    """
    try:
        scores_resp = client.api.score.get_many(trace_id=trace_id, limit=500)
    except Exception as exc:
        logger.warning(
            "Could not fetch Langfuse scores for trace %s: %s.", trace_id, exc,
        )
        return None

    bucket_label_scores = [
        s for s in (scores_resp.data or [])
        if s.name == _BUCKET_LABEL_SCORE_NAME and s.observation_id
    ]

    if not bucket_label_scores:
        logger.warning(
            "No '%s' scores found for trace %s — Phase 0 (FaultBucketingPipeline) "
            "may not have run yet.",
            _BUCKET_LABEL_SCORE_NAME, trace_id,
        )
        return None

    matched: Set[str] = set()
    for score in bucket_label_scores:
        labels = [s.strip() for s in (score.value or "").split(",")]
        if fault_id in labels:
            matched.add(score.observation_id)

    if not matched:
        logger.warning(
            "No observations matched fault_id '%s' via '%s' scores in trace %s.",
            fault_id, _BUCKET_LABEL_SCORE_NAME, trace_id,
        )

    logger.info(
        "fault_id '%s': %d observation(s) matched via '%s'.",
        fault_id, len(matched), _BUCKET_LABEL_SCORE_NAME,
    )
    return matched


# ---------------------------------------------------------------------------
# 0. list_fault_ids_in_trace
# ---------------------------------------------------------------------------

def list_fault_ids_in_trace(trace_id: str) -> List[str]:
    """Return all fault_ids present in a trace by scanning ``fault:*`` spans.

    The agent-sidecar logs one ``fault: <name>`` span per injected fault.
    Repeated faults produce multiple spans with the same name (injection span
    names are never suffixed); this function counts occurrences in chronological
    order to reconstruct ``FaultBucketingPipeline``'s fault_id convention:

      first  ``"fault: pod-cpu-hog"`` span → ``"pod-cpu-hog"``
      second ``"fault: pod-cpu-hog"`` span → ``"pod-cpu-hog_2"``
      third  ``"fault: pod-cpu-hog"`` span → ``"pod-cpu-hog_3"``

    Occurrence-counting is necessary because injection spans are never
    suffixed — both occurrences of a repeated fault share the name
    ``"fault: pod-cpu-hog"`` — so simple name deduplication would lose the
    second occurrence.

    Results are cross-checked against the trace-level ``fault_list`` score
    (posted by Phase 0) when available; a warning is logged on mismatch.
    Note: ``fault_list`` contains fault **names** (no suffix), not fault_ids,
    so the cross-check compares name sets only.

    This is the recommended way to discover which fault_ids to pass to
    ``build_bucket_json_from_langfuse`` and ``run_phase1_on_langfuse_trace``.

    Args:
        trace_id: Langfuse trace ID.

    Returns:
        List of fault IDs, e.g. ``["pod-cpu-hog", "pod-network-loss"]``.
        Returns ``[]`` when no fault spans are found.
    """
    from main.services.trace_service import _list_observations, _format_observations

    client = _get_langfuse_client()

    raw_obs: List[Any] = _list_observations(client, trace_id)
    if not raw_obs:
        logger.warning("No observations found for trace %s", trace_id)
        return []

    raw_dicts: List[Dict[str, Any]] = [
        (o.model_dump() if hasattr(o, "model_dump") else o.dict())
        for o in raw_obs
    ]
    events: List[Dict[str, Any]] = _format_observations(raw_dicts)

    # Collect injection spans in chronological order — mirrors FaultBucketingPipeline.
    injection_spans = sorted(
        [
            obs for obs in events
            if isinstance(obs.get("name"), str) and obs["name"].startswith("fault:")
        ],
        key=lambda o: o.get("startTime") or "",
    )

    fault_ids: List[str] = []
    name_counts: Dict[str, int] = {}
    for obs in injection_spans:
        fault_name = obs["name"][len("fault:"):].strip()
        if not fault_name:
            continue
        count = name_counts.get(fault_name, 0)
        fault_ids.append(fault_name if count == 0 else f"{fault_name}_{count + 1}")
        name_counts[fault_name] = count + 1

    logger.info(
        "Found %d fault_id(s) in trace %s: %s", len(fault_ids), trace_id, fault_ids
    )

    # Cross-check against the Phase 0 fault_list score (trace-level, no observation_id).
    # fault_list carries fault names (no suffix), so compare name sets only.
    try:
        scores_resp = client.api.score.get_many(trace_id=trace_id, limit=500)
        for score in (scores_resp.data or []):
            if score.name == _FAULT_LIST_SCORE_NAME and not score.observation_id:
                phase0_names = {
                    s.strip() for s in (score.value or "").split(",") if s.strip()
                }
                # Strip _N suffix from fault_ids to get comparable fault names.
                span_names = set()
                for fid in fault_ids:
                    parts = fid.rsplit("_", 1)
                    span_names.add(parts[0] if len(parts) == 2 and parts[1].isdigit() else fid)
                if phase0_names != span_names:
                    logger.warning(
                        "fault_list score for trace %s contains %s but injection spans "
                        "yield fault names %s — Phase 0 and trace observations may be "
                        "out of sync.",
                        trace_id, sorted(phase0_names), sorted(span_names),
                    )
                else:
                    logger.info(
                        "fault_list cross-check passed for trace %s: %s.",
                        trace_id, sorted(phase0_names),
                    )
                break
    except Exception as exc:
        logger.debug(
            "Could not cross-check fault_list score for trace %s: %s.", trace_id, exc
        )

    return fault_ids


# ---------------------------------------------------------------------------
# 1. build_bucket_json_from_langfuse
# ---------------------------------------------------------------------------

def build_bucket_json_from_langfuse(trace_id: str, fault_id: str) -> Dict[str, Any]:
    """Fetch observations for *trace_id* and build a bucket dict for *fault_id*.

    Steps:
    1. Fetch all observations via ``_list_observations`` / ``_format_observations``
       (same helpers as the FastAPI pipeline — identical normalised shape).
    2. Fetch per-observation ``bucket_label`` Langfuse scores (posted by
       ``FaultBucketingPipeline``) and keep observations whose score contains
       *fault_id* (direct match, comma-split).  When ``bucket_label`` scores
       are absent (Phase 0 not yet run), all non-injection observations are
       included and a warning is emitted.
    3. Locate the ``"fault: <fault_id>"`` injection span to extract bucket
       metadata (fault_name, namespace, target_pod, injection_timestamp, …).
       The injection span itself is NOT placed in the ``"events"`` list.
       Note: injection spans are never suffixed, so for repeated-fault
       fault_ids (e.g. ``"pod-cpu-hog_2"``) no exact match will be found and
       metadata will be minimal (fault_id only).
    4. Return a dict matching the format ``TraceMetricsExtractor.load_trace_dict``
       accepts: ``{fault_id, fault_name, ..., events: [...]}``.

    Field name resolution is defensive: both camelCase and snake_case variants
    are tried for every metadata key because Langfuse SDK versions and OTel
    exporters differ in casing.

    Args:
        trace_id: Langfuse trace ID.
        fault_id: Fault identifier to scope the bucket to (e.g. ``"pod-cpu-hog"``
                  or ``"pod-cpu-hog_2"`` for the second occurrence).

    Returns:
        Bucket dict accepted by ``TraceMetricsExtractor.load_trace_dict``.
    """
    from main.services.trace_service import _list_observations, _format_observations

    client = _get_langfuse_client()

    raw_obs: List[Any] = _list_observations(client, trace_id)
    if not raw_obs:
        logger.warning("No observations found for trace %s", trace_id)

    raw_dicts: List[Dict[str, Any]] = [
        (o.model_dump() if hasattr(o, "model_dump") else o.dict())
        for o in raw_obs
    ]
    all_events: List[Dict[str, Any]] = _format_observations(raw_dicts)

    # ------------------------------------------------------------------
    # Resolve which observation IDs belong to this fault.
    # ------------------------------------------------------------------
    fault_obs_ids: Optional[Set[str]] = _fetch_fault_observation_ids(
        client, trace_id, fault_id
    )

    if fault_obs_ids is None:
        # Phase 0 scores unavailable — fall back to all non-injection observations.
        logger.warning(
            "'%s' scores unavailable for trace %s — including all non-injection "
            "observations in bucket for fault '%s'.",
            _BUCKET_LABEL_SCORE_NAME, trace_id, fault_id,
        )
        events = [
            obs for obs in all_events
            if not (
                isinstance(obs.get("name"), str) and obs["name"].startswith("fault:")
            )
        ]
    else:
        events = [obs for obs in all_events if obs.get("id") in fault_obs_ids]

    logger.info(
        "Including %d observation(s) in bucket for fault '%s' (trace %s).",
        len(events), fault_id, trace_id,
    )

    # ------------------------------------------------------------------
    # Locate the injection span for this fault.
    # ------------------------------------------------------------------
    # The agent-sidecar names it "fault: <fault_id>" (colon + space).
    # Injection spans are never suffixed — for repeated-fault fault_ids
    # like "pod-cpu-hog_2" no match will be found and metadata falls back
    # to fault_id only.
    injection_span_name = f"fault: {fault_id}"
    fault_obs: Optional[Dict[str, Any]] = None

    for obs in all_events:
        name = obs.get("name") or ""
        if name == injection_span_name:
            fault_obs = obs
            break

    # Tolerant fallback: accept "fault:<fault_id>" without the space.
    if fault_obs is None:
        for obs in all_events:
            name = obs.get("name") or ""
            if isinstance(name, str) and name.startswith("fault:"):
                candidate_id = name[len("fault:"):].strip()
                if candidate_id == fault_id:
                    fault_obs = obs
                    break

    if fault_obs is None:
        logger.warning(
            "No injection span found for fault_id '%s' in trace %s. "
            "Bucket metadata will be minimal (fault_id only).",
            fault_id, trace_id,
        )

    # ------------------------------------------------------------------
    # Extract bucket metadata from the injection span
    # ------------------------------------------------------------------
    bucket_meta: Dict[str, Any] = {"fault_id": fault_id}

    if fault_obs is not None:
        # After _format_observations, "metadata" and "input" are JSON strings (or None).
        span_meta: Dict[str, Any] = _parse_json_field(fault_obs.get("metadata"))
        span_input: Dict[str, Any] = _parse_json_field(fault_obs.get("input"))
        merged: Dict[str, Any] = {**span_input, **span_meta}  # span_meta wins

        extracted_fault_name = _coerce(
            merged, "fault_name", "faultName", "fault_type", "faultType"
        )
        if extracted_fault_name is not None:
            bucket_meta["fault_name"] = extracted_fault_name

        namespace = _coerce(
            merged,
            "namespace", "fault_namespace", "faultNamespace",
            "target_namespace", "targetNamespace",
        )
        if namespace is not None:
            bucket_meta["namespace"] = namespace

        target_pod = _coerce(
            merged,
            "target_pod", "targetPod", "pod",
            "target_service", "targetService", "service",
        )
        if target_pod is not None:
            bucket_meta["target_pod"] = target_pod

        # injection_timestamp: try metadata keys first, then fall back to startTime.
        inj_ts = _coerce(
            merged,
            "injection_timestamp", "injectionTimestamp",
            "injected_at", "injectedAt",
            "fault_injection_time", "faultInjectionTime",
        )
        bucket_meta["injection_timestamp"] = inj_ts or fault_obs.get("startTime")

        # Optional tracing identifiers
        for out_key, *cands in [
            ("experiment_id",  "experiment_id",  "experimentId",  "experiment.id"),
            ("run_id",         "run_id",         "runId",         "experiment_run_id", "experiment.run_id"),
            ("agent_name",     "agent_name",     "agentName"),
            ("agent_id",       "agent_id",       "agentId"),
            ("agent_version",  "agent_version",  "agentVersion"),
            ("severity",       "severity",       "fault_severity", "faultSeverity"),
        ]:
            val = _coerce(merged, *cands)
            if val is not None:
                bucket_meta[out_key] = val

        # Ground-truth fields (may be absent if not stored in Langfuse)
        for out_key, *cands in [
            ("ground_truth",                "ground_truth",                "groundTruth"),
            ("ideal_course_of_action",      "ideal_course_of_action",      "idealCourseOfAction"),
            ("ideal_tool_usage_trajectory", "ideal_tool_usage_trajectory", "idealToolUsageTrajectory"),
        ]:
            val = _coerce(merged, *cands)
            if val is not None:
                bucket_meta[out_key] = val

    return {**bucket_meta, "events": events}


# ---------------------------------------------------------------------------
# 2. push_extraction_result_to_langfuse
# ---------------------------------------------------------------------------

def push_extraction_result_to_langfuse(
    trace_id: str,
    fault_id: str,
    result: ExtractionResult,
) -> None:
    """Push Phase 1 metrics as fault-scoped scores in Langfuse.

    Score names are suffixed with *fault_id* (e.g. ``ttd_pod-cpu-hog``) so
    results from multiple faults on the same trace do not overwrite each other.

    Pushes up to 13 scores (fewer when source values are None).  Each
    ``create_score`` call is wrapped in try/except so one failure does not
    prevent the remaining scores from being sent.  ``flush()`` is called once
    at the end to guarantee delivery before the function returns.

    Score taxonomy (Langfuse SDK 3.x data_type strings):
      NUMERIC     — float value
      BOOLEAN     — 0.0 or 1.0
      CATEGORICAL — string value (e.g. "Compliant")

    Args:
        trace_id: Langfuse trace ID to attach scores to.
        fault_id: Fault identifier used as score name suffix.
        result:   ExtractionResult returned by TraceMetricsExtractor.
    """
    client = _get_langfuse_client()
    q = result.quantitative
    ql = result.qualitative

    def _sfx(name: str) -> str:
        return f"{name}_{fault_id}"

    def _push(name: str, value: Any, dtype: str) -> None:
        try:
            client.create_score(
                trace_id=trace_id,
                name=name,
                value=value,
                data_type=dtype,
            )
            logger.debug(
                "Pushed score %s=%s (%s) for trace %s fault '%s'",
                name, value, dtype, trace_id, fault_id,
            )
        except Exception as exc:
            logger.error(
                "Failed to push score '%s' for trace %s fault '%s': %s",
                name, trace_id, fault_id, exc,
            )

    # --- NUMERIC scores ---

    if q.time_to_detect is not None:
        _push(_sfx("ttd"), float(q.time_to_detect), "NUMERIC")

    if q.time_to_mitigate is not None:
        _push(_sfx("ttr"), float(q.time_to_mitigate), "NUMERIC")

    # input_tokens / output_tokens default to 0 (never None)
    _push(_sfx("input_tokens"), float(q.input_tokens), "NUMERIC")
    _push(_sfx("output_tokens"), float(q.output_tokens), "NUMERIC")

    # tool_call_count: len() is always defined
    _push(_sfx("tool_call_count"), float(len(q.tool_calls)), "NUMERIC")

    if ql.hallucination_score is not None:
        _push(_sfx("hallucination_score"), float(ql.hallucination_score), "NUMERIC")

    if ql.reasoning_quality_score is not None:
        _push(_sfx("reasoning_quality_score"), float(ql.reasoning_quality_score), "NUMERIC")

    # tool_selection_accuracy: use the pre-computed field set by QuantitativeAggregator;
    # fall back to component ratio (correct / total) in case the LLM set them as
    # extra fields on the model (model_config = {"extra": "allow"}).
    tsa: Optional[float] = q.tool_selection_accuracy
    if tsa is None:
        correct = getattr(q, "correct_tool_selections", None)
        total = getattr(q, "total_tool_selections", None)
        if correct is not None and total is not None:
            try:
                total_f = float(total)
                if total_f > 0:
                    tsa = float(correct) / total_f
            except (TypeError, ValueError):
                pass
    if tsa is not None:
        _push(_sfx("tool_selection_accuracy"), float(tsa), "NUMERIC")

    # --- BOOLEAN scores (Langfuse expects 0.0 / 1.0) ---

    if q.detection_success is not None:
        _push(_sfx("detection_success"), float(q.detection_success), "BOOLEAN")

    # mitigation_success is always derived — never skip
    _push(
        _sfx("mitigation_success"),
        1.0 if q.time_to_mitigate is not None else 0.0,
        "BOOLEAN",
    )

    if ql.unsafe_action_detected is not None:
        _push(
            _sfx("unsafe_action_detected"),
            1.0 if ql.unsafe_action_detected else 0.0,
            "BOOLEAN",
        )

    if q.personal_pii_detected is not None:
        _push(
            _sfx("personal_pii_detected"),
            1.0 if q.personal_pii_detected else 0.0,
            "BOOLEAN",
        )

    # --- CATEGORICAL scores ---

    sec_status = ql.security_compliance_status
    if sec_status is not None:
        # Resolve enum (.value) if the field holds a SecurityComplianceStatus enum instance
        if hasattr(sec_status, "value"):
            sec_status = sec_status.value
        _push(_sfx("security_compliance_status"), str(sec_status), "CATEGORICAL")

    # Flush all queued scores to the Langfuse API before returning.
    client.flush()
    logger.info(
        "Scores flushed to Langfuse for trace %s, fault '%s'.", trace_id, fault_id
    )


# ---------------------------------------------------------------------------
# 3. Orchestrator
# ---------------------------------------------------------------------------

def run_phase1_on_langfuse_trace(trace_id: str, fault_id: str) -> None:
    """Fetch → extract → push for one (trace_id, fault_id) pair.

    1. Builds a fault-scoped bucket dict by fetching all observations for
       *trace_id* and filtering to those belonging to *fault_id* via
       ``bucket_label`` scores from Phase 0.
    2. Runs TraceMetricsExtractor on that dict (Phase 1).
    3. Pushes the resulting scores back to Langfuse, suffixed by *fault_id*.

    To process every fault in a trace:

        for fault_id in list_fault_ids_in_trace(trace_id):
            run_phase1_on_langfuse_trace(trace_id, fault_id)

    Args:
        trace_id: Langfuse trace ID to process.
        fault_id: Specific fault to process (e.g. ``"pod-cpu-hog"`` or
                  ``"pod-cpu-hog_2"`` for the second occurrence).
    """
    logger.info(
        "Phase 1 bridge: fetching trace %s (fault='%s') from Langfuse.",
        trace_id, fault_id,
    )
    bucket = build_bucket_json_from_langfuse(trace_id, fault_id)
    n_events = len(bucket.get("events", []))
    logger.info(
        "Phase 1 bridge: running extraction on %d observations (fault='%s').",
        n_events, bucket.get("fault_name", fault_id),
    )

    result: ExtractionResult = extract_metrics_from_trace_dict(bucket)

    logger.info(
        "Phase 1 bridge: pushing scores to Langfuse for trace %s, fault '%s'.",
        trace_id, fault_id,
    )
    push_extraction_result_to_langfuse(trace_id, fault_id, result)
    logger.info(
        "Phase 1 bridge: complete for trace %s, fault '%s'.", trace_id, fault_id
    )
