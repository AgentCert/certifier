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

Per-observation fault filtering
--------------------------------
To determine which observations belong to a given fault the bridge reads the
colleague's fault-classification Langfuse scores.  Each agent-observation
observation that Phase 0 would assign to a fault bucket must have had a score
posted against it by the colleague's evaluator:

  Score name  : "fault_classification"        (constant below)
  Data type   : CATEGORICAL
  Value       : JSON-encoded list of fault IDs
                e.g. '["pod-cpu-hog"]' or '["pod-cpu-hog", "pod-network-loss"]'
  Scope       : observation-level — create_score must be called with
                observation_id=<observation.id>

This mirrors Phase 0's EventClassification.related_faults field exactly.
If no such scores exist yet (colleague's evaluator has not run), all
non-injection observations are included in the bucket and a warning is logged.

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
# Convention shared with the colleague's fault-classification evaluator
# ---------------------------------------------------------------------------

# Name of the per-observation Langfuse score that encodes fault membership.
# Value must be a JSON-encoded list of fault IDs (CATEGORICAL data_type).
_FAULT_CLASSIFICATION_SCORE_NAME = "fault_classification"


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
# Internal: per-observation fault-classification score fetcher
# ---------------------------------------------------------------------------

def _fetch_fault_observation_ids(
    client: Any,
    trace_id: str,
    fault_id: str,
) -> Optional[Set[str]]:
    """Return the set of observation IDs classified as belonging to *fault_id*.

    Reads per-observation ``fault_classification`` Langfuse scores posted by
    the colleague's evaluator.  Each score's value is a JSON-encoded list of
    fault IDs (e.g. ``'["pod-cpu-hog"]'``); the observation is included in
    the returned set when *fault_id* appears in that list.

    Returns ``None`` when no ``fault_classification`` scores are found, which
    signals the caller to fall back to including all non-injection observations.
    """
    try:
        scores_resp = client.api.score.get_many(trace_id=trace_id, limit=500)
    except Exception as exc:
        logger.warning(
            "Could not fetch Langfuse scores for trace %s: %s. "
            "Will include all observations in bucket for fault '%s'.",
            trace_id, exc, fault_id,
        )
        return None

    matching_obs_ids: Set[str] = set()
    seen_classification_score = False

    for score in (scores_resp.data or []):
        if score.name != _FAULT_CLASSIFICATION_SCORE_NAME:
            continue
        if not score.observation_id:
            continue  # skip trace-level scores; we only want per-observation ones

        seen_classification_score = True

        try:
            related_faults = (
                json.loads(score.value) if isinstance(score.value, str) else score.value
            )
        except (json.JSONDecodeError, TypeError):
            related_faults = []

        if isinstance(related_faults, list) and fault_id in related_faults:
            matching_obs_ids.add(score.observation_id)

    if not seen_classification_score:
        logger.warning(
            "No '%s' scores found for trace %s. "
            "The colleague's fault classifier may not have run yet. "
            "All non-injection observations will be included in bucket for fault '%s'.",
            _FAULT_CLASSIFICATION_SCORE_NAME, trace_id, fault_id,
        )
        return None

    logger.info(
        "Found %d observation(s) classified as '%s' via Langfuse scores in trace %s.",
        len(matching_obs_ids), fault_id, trace_id,
    )
    return matching_obs_ids


# ---------------------------------------------------------------------------
# 0. list_fault_ids_in_trace
# ---------------------------------------------------------------------------

def list_fault_ids_in_trace(trace_id: str) -> List[str]:
    """Return all fault_ids present in a trace by scanning ``fault:*`` spans.

    The agent-sidecar logs one ``fault: <name>`` span per injected fault.
    This function collects those span names, strips the ``"fault: "`` prefix,
    and returns a deduplicated list of fault IDs in the order they appear.

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

    fault_ids: List[str] = []
    seen: Set[str] = set()
    for obs in events:
        name = obs.get("name") or ""
        if not (isinstance(name, str) and name.startswith("fault:")):
            continue
        # "fault: pod-cpu-hog" → "pod-cpu-hog"
        fault_id = name[len("fault:"):].strip()
        if fault_id and fault_id not in seen:
            fault_ids.append(fault_id)
            seen.add(fault_id)

    logger.info(
        "Found %d fault(s) in trace %s: %s", len(fault_ids), trace_id, fault_ids
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
    2. Fetch per-observation ``fault_classification`` Langfuse scores (posted by
       the colleague's evaluator) and use them to keep only the observations that
       belong to *fault_id*.  When no scores exist, all non-injection observations
       are included and a warning is emitted.
    3. Locate the ``"fault: <fault_id>"`` injection span to extract bucket
       metadata (fault_name, namespace, target_pod, injection_timestamp, …).
       The injection span itself is NOT placed in the ``"events"`` list.
    4. Return a dict matching the format ``TraceMetricsExtractor.load_trace_dict``
       accepts: ``{fault_id, fault_name, ..., events: [...]}``.

    Field name resolution is defensive: both camelCase and snake_case variants
    are tried for every metadata key because Langfuse SDK versions and OTel
    exporters differ in casing.

    Args:
        trace_id: Langfuse trace ID.
        fault_id: Fault identifier to scope the bucket to (e.g. ``"pod-cpu-hog"``).

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
    # Filter observations to those belonging to fault_id
    # ------------------------------------------------------------------
    matched_obs_ids: Optional[Set[str]] = _fetch_fault_observation_ids(
        client, trace_id, fault_id
    )

    if matched_obs_ids is not None:
        # Colleague's scores are available — keep only the classified set.
        # Injection spans (fault:*) are excluded: the colleague's classifier
        # should not have scored them, and even if it did, they must not
        # appear in bucket events (per Phase 0 semantics).
        events = [
            obs for obs in all_events
            if obs.get("id") in matched_obs_ids
            and not (
                isinstance(obs.get("name"), str)
                and obs["name"].startswith("fault:")
            )
        ]
        logger.info(
            "Filtered %d → %d observations for fault '%s' (trace %s).",
            len(all_events), len(events), fault_id, trace_id,
        )
    else:
        # Fallback: include all non-injection observations.
        events = [
            obs for obs in all_events
            if not (
                isinstance(obs.get("name"), str)
                and obs["name"].startswith("fault:")
            )
        ]
        logger.warning(
            "Fault classification scores unavailable; using all %d "
            "non-injection observations for fault '%s' (trace %s).",
            len(events), fault_id, trace_id,
        )

    # ------------------------------------------------------------------
    # Locate the injection span for this specific fault_id
    # ------------------------------------------------------------------
    # The agent-sidecar names it "fault: <fault_id>" (colon + space).
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
            "No injection span found for fault '%s' in trace %s. "
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

        fault_name = _coerce(
            merged, "fault_name", "faultName", "fault_type", "faultType"
        )
        if fault_name is not None:
            bucket_meta["fault_name"] = fault_name

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
       *trace_id* and filtering to those belonging to *fault_id*.
    2. Runs TraceMetricsExtractor on that dict (Phase 1).
    3. Pushes the resulting scores back to Langfuse, suffixed by *fault_id*.

    To process every fault in a trace:

        for fault_id in list_fault_ids_in_trace(trace_id):
            run_phase1_on_langfuse_trace(trace_id, fault_id)

    Args:
        trace_id: Langfuse trace ID to process.
        fault_id: Specific fault to process (e.g. ``"pod-cpu-hog"``).
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
