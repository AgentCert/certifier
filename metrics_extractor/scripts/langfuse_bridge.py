"""
Langfuse ↔ Phase 1 bridge.

Six public entry points:

  list_fault_ids_in_trace(trace_id)                        → List[str]
  build_bucket_json_from_langfuse(trace_id, fault_id)      → bucket dict
  push_extraction_result_to_langfuse(trace_id, fault_id, result)  → None
  clear_existing_evaluation_scores(trace_id, fault_ids)    → int
  run_phase1_on_langfuse_trace(trace_id, fault_id)         → None
  run_phase0_then_phase1(trace_id)                         → Dict[str, ExtractionResult]

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
import re
import subprocess
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

# Per-observation confidence score companion to bucket_label.
_BUCKET_CONFIDENCE_SCORE_NAME = "bucket_confidence"

# Trace-level score listing all fault names present (comma-separated, no observation_id).
_FAULT_LIST_SCORE_NAME = "fault_list"

# All Phase 0 score names — exact match, no suffix.
_PHASE0_SCORE_NAMES: frozenset = frozenset({
    _BUCKET_LABEL_SCORE_NAME,
    _BUCKET_CONFIDENCE_SCORE_NAME,
    _FAULT_LIST_SCORE_NAME,
})

# Phase 1 score base names (before the "_<fault_id>" suffix).
# Full score name = f"{base}_{fault_id}", e.g. "ttd_pod-cpu-hog".
# These are exactly the names pushed by push_extraction_result_to_langfuse.
_PHASE1_SCORE_BASE_NAMES: frozenset = frozenset({
    "ttd", "ttr",
    "input_tokens", "output_tokens", "tool_call_count",
    "hallucination_score", "reasoning_quality_score", "tool_selection_accuracy",
    "detection_success", "mitigation_success",
    "unsafe_action_detected", "personal_pii_detected",
    "security_compliance_status",
})


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
# Internal: score fetching (SDK-version-safe)
# ---------------------------------------------------------------------------

def _list_all_scores(client: Any, trace_id: str) -> List[Any]:
    """Paginate all scores for *trace_id* using the Langfuse SDK ≥ 4.x endpoint.

    Langfuse SDK 4.x renamed ``client.api.score`` to ``client.api.scores`` and
    capped the per-page limit at 100.  CATEGORICAL score string values are in
    ``score.string_value``, not ``score.value`` (which is always a float).
    """
    results = []
    page = 1
    while True:
        resp = client.api.scores.get_many(trace_id=trace_id, limit=100, page=page)
        results.extend(resp.data or [])
        if not resp.data or page >= resp.meta.total_pages:
            break
        page += 1
    return results


def _score_string_value(score: Any) -> str:
    """Return the string value of a score regardless of SDK version.

    SDK ≥ 4.x stores CATEGORICAL values in ``string_value``; ``value`` is
    always a float.  SDK 3.x stored the string directly in ``value``.
    """
    sv = getattr(score, "string_value", None)
    if sv is not None:
        return sv
    v = getattr(score, "value", None)
    return str(v) if v is not None else ""


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
        all_scores = _list_all_scores(client, trace_id)
    except Exception as exc:
        logger.warning(
            "Could not fetch Langfuse scores for trace %s: %s.", trace_id, exc,
        )
        return None

    bucket_label_scores = [
        s for s in all_scores
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
        labels = [s.strip() for s in _score_string_value(score).split(",")]
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
        all_scores = _list_all_scores(client, trace_id)
        for score in all_scores:
            if score.name == _FAULT_LIST_SCORE_NAME and not score.observation_id:
                phase0_names = {
                    s.strip() for s in _score_string_value(score).split(",") if s.strip()
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

        # The chaos harness stores target metadata one level deeper under an
        # "attributes" sub-dict with dotted OTel-style key names.  Try the flat
        # merged dict first (for traces produced by other exporters), then fall
        # back to attributes.  Note: "fault.namespace" in attributes is the
        # chaos-engine namespace (e.g. "litmus"), NOT the application target
        # namespace — use "fault.target_namespace" for the latter.
        attrs: Dict[str, Any] = merged.get("attributes") or {}

        namespace = _coerce(
            merged,
            "namespace", "fault_namespace", "faultNamespace",
            "target_namespace", "targetNamespace",
        ) or _coerce(attrs, "fault.target_namespace")
        if namespace is not None:
            bucket_meta["namespace"] = namespace

        target_pod = _coerce(
            merged,
            "target_pod", "targetPod", "pod",
            "target_service", "targetService", "service",
        ) or _coerce(attrs, "fault.target_label", "fault.target.workload_ref")
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
# 3. Score cleanup — direct ClickHouse deletion
# ---------------------------------------------------------------------------

# Regex that safe IDs (trace_id, fault_id, project_id, container name) must match.
# Allows hex digits, letters, digits, hyphens, underscores, dots, colons, slashes.
# Forbids single quotes, backslashes, semicolons, and other SQL-injection chars.
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_.:/\-]+$")


def _assert_safe(value: str, label: str) -> None:
    if not _SAFE_ID_RE.match(value):
        raise ValueError(
            f"Unsafe {label} value {value!r} — contains characters not allowed "
            f"in SQL identifiers.  Only alphanumerics, hyphens, underscores, "
            f"dots, colons, and slashes are permitted."
        )


def _ch_query(container: str, user: str, password: str, query: str) -> str:
    """Run *query* against the ClickHouse container and return stdout."""
    result = subprocess.run(
        ["docker", "exec", container,
         "clickhouse-client", "--user", user, "--password", password,
         "--query", query],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ClickHouse query failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


def clear_existing_evaluation_scores(
    trace_id: str,
    fault_ids: Optional[List[str]] = None,
    *,
    clickhouse_container: Optional[str] = None,
    project_id: Optional[str] = None,
    clickhouse_user: Optional[str] = None,
    clickhouse_password: Optional[str] = None,
) -> int:
    """Delete pipeline evaluation scores for *trace_id* directly in ClickHouse.

    Scoping rules
    -------------
    ``fault_ids=None``
        Full reset: deletes all Phase 0 scores (``bucket_label``,
        ``bucket_confidence``, ``fault_list``) **and** all Phase 1 scores for
        every fault present on this trace (matched with ``LIKE`` patterns).
        Use this before re-running Phase 0 from scratch.

    ``fault_ids=[...]``
        Partial reset: deletes Phase 1 scores for the listed fault IDs only
        (exact names, e.g. ``ttd_pod-cpu-hog``).  Phase 0 scores are **not
        touched** — use this when re-running Phase 1 alone for specific faults.

    Safety guarantee
    ----------------
    Only score names in ``_PHASE0_SCORE_NAMES`` (3 names) or names matching
    ``<base>_<fault_id>`` for bases in ``_PHASE1_SCORE_BASE_NAMES`` (13 bases)
    are ever deleted.  Anything else is left completely untouched.

    Credentials / container resolution
    ------------------------------------
    Arguments take precedence over environment variables.  Required env vars:

      ``LANGFUSE_CLICKHOUSE_CONTAINER``   — e.g. ``lucien-langfuse-lucien-clickhouse-1``
      ``LANGFUSE_PROJECT_ID``             — e.g. ``lucien-project``

    Optional (default ``"clickhouse"`` for both):

      ``LANGFUSE_CLICKHOUSE_USER``
      ``LANGFUSE_CLICKHOUSE_PASSWORD``

    Args:
        trace_id: Langfuse trace ID whose scores should be cleaned.
        fault_ids: If provided, restrict Phase 1 deletion to these fault IDs.
                   If ``None``, delete Phase 0 scores + all Phase 1 scores.

    Returns:
        Total number of score rows deleted.

    Raises:
        RuntimeError: Container or project_id not configured, or ClickHouse
            query fails.
        ValueError: ``trace_id`` or a ``fault_id`` contains unsafe characters.
    """
    container = clickhouse_container or os.environ.get("LANGFUSE_CLICKHOUSE_CONTAINER", "")
    proj_id   = project_id          or os.environ.get("LANGFUSE_PROJECT_ID",             "")
    ch_user   = clickhouse_user     or os.environ.get("LANGFUSE_CLICKHOUSE_USER",    "clickhouse")
    ch_pass   = clickhouse_password or os.environ.get("LANGFUSE_CLICKHOUSE_PASSWORD", "clickhouse")

    if not container:
        raise RuntimeError(
            "ClickHouse container not specified.  Set LANGFUSE_CLICKHOUSE_CONTAINER "
            "or pass clickhouse_container=... to clear_existing_evaluation_scores()."
        )
    if not proj_id:
        raise RuntimeError(
            "Langfuse project ID not specified.  Set LANGFUSE_PROJECT_ID "
            "or pass project_id=... to clear_existing_evaluation_scores()."
        )

    _assert_safe(trace_id, "trace_id")
    _assert_safe(container, "clickhouse_container")
    _assert_safe(proj_id, "project_id")

    # ------------------------------------------------------------------
    # Build the name filter — never touches names outside our universe.
    # ------------------------------------------------------------------
    name_clauses: List[str] = []

    if fault_ids is None:
        # Full reset: Phase 0 (exact) + Phase 1 (LIKE patterns per base name).
        p0_sql = ", ".join(f"'{n}'" for n in sorted(_PHASE0_SCORE_NAMES))
        name_clauses.append(f"name IN ({p0_sql})")
        for base in sorted(_PHASE1_SCORE_BASE_NAMES):
            # LIKE pattern: '<base>\_%' — ClickHouse treats \ as default escape,
            # so \_ matches a literal underscore, then % matches the fault_id suffix.
            name_clauses.append(f"name LIKE '{base}\\_%'")
    else:
        if not fault_ids:
            logger.info(
                "clear_existing_evaluation_scores: fault_ids=[] — nothing to delete "
                "for trace %s.", trace_id,
            )
            return 0
        # Partial reset: exact Phase 1 names for the listed fault IDs only.
        for fid in fault_ids:
            _assert_safe(fid, "fault_id")
        exact_names = sorted(
            f"{base}_{fid}"
            for fid in fault_ids
            for base in _PHASE1_SCORE_BASE_NAMES
        )
        name_clauses.append("name IN ({})".format(
            ", ".join(f"'{n}'" for n in exact_names)
        ))

    name_filter = " OR ".join(name_clauses)
    where = (
        f"project_id = '{proj_id}' AND trace_id = '{trace_id}'"
        f" AND ({name_filter})"
    )

    # ------------------------------------------------------------------
    # SELECT first — log what will be deleted, bail early if nothing.
    # ------------------------------------------------------------------
    counts_raw = _ch_query(
        container, ch_user, ch_pass,
        f"SELECT name, count() AS cnt FROM default.scores "
        f"WHERE {where} GROUP BY name ORDER BY name",
    )
    if not counts_raw:
        logger.info(
            "clear_existing_evaluation_scores: nothing to delete for trace %s "
            "(no matching scores found).", trace_id,
        )
        return 0

    total = 0
    for line in counts_raw.splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            name, cnt_str = parts
            cnt = int(cnt_str)
            total += cnt
            logger.info(
                "  [cleanup] deleting %d × '%s'  (trace %s)", cnt, name, trace_id,
            )

    # ------------------------------------------------------------------
    # DELETE — lightweight synchronous delete (ClickHouse ≥ 22.8).
    # ------------------------------------------------------------------
    _ch_query(
        container, ch_user, ch_pass,
        f"DELETE FROM default.scores WHERE {where}",
    )
    logger.info(
        "clear_existing_evaluation_scores: deleted %d row(s) for trace %s.",
        total, trace_id,
    )
    return total


# ---------------------------------------------------------------------------
# 4. Orchestrator
# ---------------------------------------------------------------------------

def run_phase1_on_langfuse_trace(
    trace_id: str,
    fault_id: str,
    *,
    delete_existing: bool = True,
) -> None:
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
        delete_existing: When ``True`` (default), deletes any existing Phase 1
            scores for *fault_id* on this trace before pushing new ones.
            Set to ``False`` to accumulate scores across runs (not recommended
            for production — makes the trace ambiguous).
    """
    if delete_existing:
        try:
            clear_existing_evaluation_scores(trace_id, fault_ids=[fault_id])
        except Exception as exc:
            logger.warning(
                "Phase 1 bridge: could not clear existing scores for fault '%s' "
                "on trace %s: %s — proceeding without cleanup.",
                fault_id, trace_id, exc,
            )

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


# ---------------------------------------------------------------------------
# 5. run_phase0_then_phase1 — chained, single Langfuse fetch
# ---------------------------------------------------------------------------

def run_phase0_then_phase1(
    trace_id: str,
    *,
    delete_existing: bool = True,
) -> Dict[str, ExtractionResult]:
    """Chain Phase 0 → Phase 1 in one pass, reusing the in-memory FaultBucket objects.

    Compared with running Phase 0 and then calling run_phase1_on_langfuse_trace
    per fault, this function skips the per-fault Langfuse re-fetch and
    bucket_label score re-filter that the bridge otherwise performs.

    Sequence:
      1. Fetch all observations for *trace_id* from Langfuse — one round-trip,
         using the same paginated _list_observations helper as the bridge.
      2. Write them to a temporary file and run FaultBucketingPipeline.  Phase 0
         pushes bucket_label / bucket_confidence / fault_list scores to Langfuse
         as usual — that step is not shortened.  When *delete_existing* is True,
         Phase 0's push step first deletes all existing Phase 0 + Phase 1 scores
         before pushing fresh ones (full reset).
      3. For each FaultBucket in the result, call
         extract_metrics_from_trace_dict(bucket.to_dict()) directly — no second
         Langfuse fetch or score-filter step.
      4. Push Phase 1 scores via push_extraction_result_to_langfuse.

    FaultBucket.to_dict() is a strict superset of the bucket dict that
    build_bucket_json_from_langfuse produces: all fields consumed by
    load_trace_dict (fault_id, fault_name, injection_timestamp, namespace,
    target_pod, events, ground_truth, …) are present under the same keys.

    Args:
        trace_id: Langfuse trace ID to process.
        delete_existing: When ``True`` (default), deletes all existing Phase 0
            and Phase 1 scores for this trace before pushing fresh ones.
            Passed through to ``FaultBucketingPipeline`` which performs the
            cleanup inside its own score-push step.

    Returns:
        Mapping of fault_id → ExtractionResult for every non-empty bucket.
        Buckets with zero events are logged and skipped.

    Raises:
        RuntimeError: Langfuse env vars missing or package not installed.
        FaultBucketingError: Phase 0 pipeline failure.
        MetricsExtractorError: Phase 1 extraction failure for a fault (logged,
            remaining faults still processed).
    """
    import asyncio
    import json as _json
    import shutil
    import tempfile
    from pathlib import Path

    from fault_analyzer.scripts.fault_bucketing import FaultBucketingPipeline
    from main.services.trace_service import _list_observations, _format_observations

    # ------------------------------------------------------------------
    # Step 1 — fetch observations once
    # ------------------------------------------------------------------
    client = _get_langfuse_client()

    logger.info("Phase 0+1: fetching observations for trace %s.", trace_id)
    raw_obs: List[Any] = _list_observations(client, trace_id)
    if not raw_obs:
        logger.warning("No observations found for trace %s — nothing to process.", trace_id)
        return {}

    raw_dicts: List[Dict[str, Any]] = [
        (o.model_dump() if hasattr(o, "model_dump") else o.dict())
        for o in raw_obs
    ]
    all_events: List[Dict[str, Any]] = _format_observations(raw_dicts)
    logger.info("Phase 0+1: %d observations fetched.", len(all_events))

    tmp_dir = Path(tempfile.mkdtemp(prefix="phase01_"))
    try:
        # Write to a temp file — FaultBucketingPipeline requires a file path.
        trace_path = tmp_dir / f"{trace_id}.json"
        with open(trace_path, "w", encoding="utf-8") as fh:
            _json.dump(all_events, fh)

        # ------------------------------------------------------------------
        # Step 2 — Phase 0: bucket + score push (langfuse_scoring auto from env)
        # ------------------------------------------------------------------
        bucket_out = tmp_dir / "buckets"
        pipeline = FaultBucketingPipeline(
            trace_file_path=str(trace_path),
            output_dir=str(bucket_out),
            delete_existing=delete_existing,
        )
        buckets: Dict[str, Any] = asyncio.run(pipeline.run())
        logger.info(
            "Phase 0+1: Phase 0 complete — %d bucket(s) for trace %s.",
            len(buckets), trace_id,
        )

        # ------------------------------------------------------------------
        # Steps 3+4 — Phase 1 per bucket, score push per fault
        # ------------------------------------------------------------------
        results: Dict[str, ExtractionResult] = {}
        for fault_id, bucket in buckets.items():
            bucket_dict = bucket.to_dict()
            n_events = len(bucket_dict.get("events", []))
            if not n_events:
                logger.warning(
                    "Phase 0+1: bucket '%s' has no events — skipping Phase 1.",
                    fault_id,
                )
                continue

            logger.info(
                "Phase 0+1: extracting Phase 1 metrics for fault '%s' (%d events).",
                fault_id, n_events,
            )
            try:
                result: ExtractionResult = extract_metrics_from_trace_dict(bucket_dict)
            except Exception as exc:
                logger.error(
                    "Phase 0+1: Phase 1 extraction failed for fault '%s': %s — skipping.",
                    fault_id, exc, exc_info=True,
                )
                continue

            logger.info(
                "Phase 0+1: pushing Phase 1 scores for trace %s, fault '%s'.",
                trace_id, fault_id,
            )
            push_extraction_result_to_langfuse(trace_id, fault_id, result)
            results[fault_id] = result

        logger.info(
            "Phase 0+1: complete for trace %s — %d/%d fault(s) extracted.",
            trace_id, len(results), len(buckets),
        )
        return results

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
