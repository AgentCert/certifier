"""
Deterministic numeric, derived-rate, and boolean aggregation functions.

All functions are pure (no I/O) and operate on lists of per-run MongoDB documents.
"""

import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from utils.custom_errors import ConfigLoaderError


# ---------------------------------------------------------------------------
# Module-level config
# ---------------------------------------------------------------------------

_MODULE_DIR = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _MODULE_DIR / "config" / "aggregation_config.json"


def _load_module_config() -> Dict[str, Any]:
    """Load module-specific configuration from aggregation_config.json."""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as exc:
        raise ConfigLoaderError(
            f"Aggregation config not found: {_CONFIG_PATH}",
            original_exception=exc,
        ) from exc
    except json.JSONDecodeError as exc:
        raise ConfigLoaderError(
            f"Aggregation config is not valid JSON: {_CONFIG_PATH}",
            original_exception=exc,
        ) from exc
    except OSError as exc:
        raise ConfigLoaderError(
            f"Cannot read aggregation config: {_CONFIG_PATH}",
            original_exception=exc,
        ) from exc


_MODULE_CONFIG: Dict[str, Any] = {}


def _get_config() -> Dict[str, Any]:
    global _MODULE_CONFIG
    if not _MODULE_CONFIG:
        _MODULE_CONFIG = _load_module_config()
    return _MODULE_CONFIG


def _precision() -> int:
    return _get_config().get("pipeline", {}).get("rounding_precision", 4)


# ---------------------------------------------------------------------------
# Core statistics helper
# ---------------------------------------------------------------------------

def compute_stats(
    values: List[float],
    stats_to_include: List[str],
) -> Dict[str, Any]:
    """
    Compute requested statistics from a list of numeric values.

    Supported stat keys:
        mean, median, std_dev, p95, min, max, sum, mode
    """
    if not values:
        return {}

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    result: Dict[str, Any] = {}
    precision = _precision()

    for stat in stats_to_include:
        if stat == "mean":
            result["mean"] = round(statistics.mean(sorted_vals), precision)
        elif stat == "median":
            result["median"] = round(statistics.median(sorted_vals), precision)
        elif stat == "std_dev":
            result["std_dev"] = round(statistics.stdev(sorted_vals), precision) if n >= 2 else 0.0
        elif stat == "p95":
            result["p95"] = round(sorted_vals[int(n * 0.95)] if n >= 2 else sorted_vals[0], precision)
        elif stat == "min":
            result["min"] = round(sorted_vals[0], precision)
        elif stat == "max":
            result["max"] = round(sorted_vals[-1], precision)
        elif stat == "sum":
            result["sum"] = round(sum(sorted_vals), precision)
        elif stat == "mode":
            try:
                result["mode"] = round(statistics.mode(sorted_vals), precision)
            except statistics.StatisticsError:
                pass

    return result


# ---------------------------------------------------------------------------
# Extract numeric values from per-run docs
# ---------------------------------------------------------------------------

def _extract_numeric_values(
    docs: List[Dict[str, Any]], section: str, field_name: str
) -> List[float]:
    """Extract a list of non-null numeric values from docs[section][field_name]."""
    values: List[float] = []
    for doc in docs:
        val = doc.get(section, {}).get(field_name)
        if val is not None:
            try:
                values.append(float(val))
            except (TypeError, ValueError):
                pass
    return values


# ---------------------------------------------------------------------------
# Numeric aggregates
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Timing scorecard — SLA-aware, detection-weighted scoring
# Implements §1-§5 of the metric_scorecard_pipeline notebook in pure Python.
# ---------------------------------------------------------------------------

_SKEW_THRESHOLD = 0.15


def _normalize_score(raw_value: float, sla: float) -> float:
    """Piecewise SLA-aware normalization."""
    ratio = raw_value / sla
    if ratio <= 1.0:
        return 1.0 - 0.85 * ratio
    return max(0.0, 0.15 - 0.3 * (ratio - 1.0))


def _confidence_tier(n_total: int) -> str:
    if n_total < 3:
        return "INSUFFICIENT"
    if n_total < 5:
        return "LOW"
    if n_total < 20:
        return "MEDIUM"
    return "HIGH"


def _pct(sorted_vals: List[float], p: float) -> float:
    """Linear-interpolation percentile on a pre-sorted list (matches numpy.percentile)."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    pos = p / 100.0 * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    return float(sorted_vals[lo] + (pos - lo) * (sorted_vals[hi] - sorted_vals[lo]))


def _subfault_central(detected_sorted: List[float], n_total: int) -> Optional[float]:
    """Confidence-tiered central tendency over the detected (VALID) subset."""
    if n_total < 3:
        return None
    if len(detected_sorted) == 0:
        return 0.0
    med = float(statistics.median(detected_sorted))
    if n_total < 5:
        return float(statistics.mean(detected_sorted))
    p5 = _pct(detected_sorted, 5.0)
    if n_total < 20:
        return 0.7 * med + 0.3 * p5
    p1 = _pct(detected_sorted, 1.0)
    return 0.5 * med + 0.3 * p5 + 0.2 * p1


def _build_timing_obs(
    docs: List[Dict[str, Any]],
    metric_name: str,
    sla_map: Dict[str, float],
    precision: int,
) -> List[Dict[str, Any]]:
    """Build per-doc observation records for the timing scorecard (§1+§2)."""
    obs = []
    for doc in docs:
        run_id = (
            doc.get("run_id")
            or doc.get("quantitative", {}).get("run_id")
            or "unknown"
        )
        category = (
            doc.get("fault_category")
            or doc.get("quantitative", {}).get("injected_fault_category")
            or "unknown"
        )
        sub_fault = (
            doc.get("fault_name")
            or doc.get("quantitative", {}).get("injected_fault_name")
            or "unknown"
        )
        raw = doc.get("quantitative", {}).get(metric_name)
        try:
            raw_value: Optional[float] = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            raw_value = None

        sla = sla_map.get(sub_fault)

        if sla is None:
            status, norm, compliant = "NO_SLA", None, None
        elif raw_value is None:
            status, norm, compliant = "MISSING", 0.0, False
        elif raw_value <= 0:
            status, norm, compliant = "INVALID_ZERO", 0.0, False
        else:
            status = "VALID"
            norm = round(_normalize_score(raw_value, sla), precision)
            compliant = raw_value <= sla

        obs.append({
            "run_id": str(run_id)[:8],
            "category": category,
            "sub_fault": sub_fault,
            "raw_value": raw_value,
            "sla": sla,
            "status": status,
            "normalized_score": norm,
            "sla_compliant": compliant,
        })
    return obs


def _agg_subfault_grain(
    obs: List[Dict[str, Any]], precision: int
) -> Dict[str, Dict[str, Any]]:
    """§3: Sub-fault grain — detection-weighted, confidence-tiered."""
    pool = [o for o in obs if o["status"] != "NO_SLA"]
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for o in pool:
        groups.setdefault(o["sub_fault"], []).append(o)

    result: Dict[str, Dict[str, Any]] = {}
    for sub_fault, g in groups.items():
        n_obs = len(g)
        n_runs = len({o["run_id"] for o in g})
        detected_sorted = sorted(
            o["normalized_score"]
            for o in g
            if o["status"] == "VALID" and o["normalized_score"] is not None
        )
        n_compliant = sum(1 for o in g if o["sla_compliant"])
        central = _subfault_central(detected_sorted, n_obs)
        det_rate = len(detected_sorted) / n_obs if n_obs else 0.0
        weighted = central * det_rate if central is not None else None
        all_scores = sorted(
            o["normalized_score"] if (o["status"] == "VALID" and o["normalized_score"] is not None) else 0.0
            for o in g
        )
        result[sub_fault] = {
            "n_attempted": n_runs,
            "detection_rate": round(det_rate, precision),
            "sla_compliance": round(n_compliant / n_obs, precision) if n_obs else 0.0,
            "weighted_score": round(weighted, precision) if weighted is not None else None,
            "confidence": _confidence_tier(n_obs),
            "mean": round(statistics.mean(all_scores), precision) if all_scores else None,
            "median": round(statistics.median(all_scores), precision) if all_scores else None,
            "p95": round(_pct(all_scores, 95.0), precision) if all_scores else None,
        }
    return result


def _agg_category_grain(
    obs: List[Dict[str, Any]],
    subfault: Dict[str, Dict[str, Any]],
    precision: int,
) -> Dict[str, Any]:
    """§4: Category grain — rolling chain from §3 subfault weighted_scores.

    category_score = weighted_avg(subfault weighted_scores, by n_attempted).
    Only sub-faults with non-None weighted_score (confidence >= LOW) contribute.
    detection_rate and sla_compliance are still computed from raw observations.
    """
    pool = [o for o in obs if o["status"] != "NO_SLA"]
    n_obs = len(pool)
    n_runs = len({o["run_id"] for o in pool})
    n_sub_faults = len({o["sub_fault"] for o in pool})
    detected = [
        o for o in pool
        if o["status"] == "VALID" and o["normalized_score"] is not None
    ]
    n_compliant = sum(1 for o in pool if o["sla_compliant"])
    det_rate = len(detected) / n_obs if n_obs else 0.0

    total_w = 0.0
    weighted_sum = 0.0
    for sf_data in subfault.values():
        ws = sf_data.get("weighted_score")
        if ws is not None:
            n = sf_data.get("n_attempted", 0)
            weighted_sum += ws * n
            total_w += n
    category_score = weighted_sum / total_w if total_w > 0 else 0.0

    scores_cat = sorted(
        o["normalized_score"] for o in pool
        if o["status"] == "VALID" and o["normalized_score"] is not None
    )

    return {
        "n_sub_faults": n_sub_faults,
        "n_attempted": n_runs,
        "detection_rate": round(det_rate, precision),
        "sla_compliance": round(n_compliant / n_obs, precision) if n_obs else 0.0,
        "category_score": round(category_score, precision),
        "mean": round(statistics.mean(scores_cat), precision) if scores_cat else None,
        "median": round(statistics.median(scores_cat), precision) if scores_cat else None,
        "p95": round(_pct(scores_cat, 95.0), precision) if scores_cat else None,
    }


def _agg_cumulative_grain(
    obs: List[Dict[str, Any]],
    precision: int,
) -> Dict[str, Any]:
    """§5: Cumulative (agent-level) grain — detection-weighted headline + quality flags."""
    pool = [o for o in obs if o["status"] != "NO_SLA"]
    n_obs = len(pool)
    n_runs = len({o["run_id"] for o in pool})
    n_no_sla = sum(1 for o in obs if o["status"] == "NO_SLA")
    detected_sorted = sorted(
        o["normalized_score"]
        for o in pool
        if o["status"] == "VALID" and o["normalized_score"] is not None
    )
    n_valid = len(detected_sorted)
    n_compliant = sum(1 for o in pool if o["sla_compliant"])

    if n_valid:
        d_median = float(statistics.median(detected_sorted))
        det_rate = n_valid / n_obs
        headline = d_median * det_rate
    else:
        d_median, det_rate, headline = 0.0, 0.0, 0.0

    flags: List[str] = []
    if n_valid >= 2:
        if abs(statistics.mean(detected_sorted) - d_median) > _SKEW_THRESHOLD:
            flags.append("skewed_distribution")
    if n_no_sla > 0:
        flags.append(f"{n_no_sla}_obs_excluded_no_sla")
    if not flags:
        flags.append("none")

    return {
        "cumulative_score": round(headline, precision),
        "detection_rate": round(det_rate, precision),
        "sla_compliance": round(n_compliant / n_obs, precision) if n_obs else 0.0,
        "n_attempted": n_runs,
        "quality_flags": flags,
    }


def compute_timing_scorecard(
    docs: List[Dict[str, Any]],
    metric_name: str,
    sla_map: Dict[str, float],
) -> Dict[str, Any]:
    """
    SLA-aware, detection-weighted scoring for a timing metric (TTD or TTM).

    Implements the §1→§4 notebook pipeline with rolling chain:
      §1+§2  build observations + normalize per piecewise SLA curve
      §3     sub-fault grain  (confidence-tiered central tendency × detection_rate)
      §4     category grain   (weighted_avg of §3 weighted_scores by n_attempted)

    §5 (cumulative/agent-level) is computed in the scorecard builder as
    weighted_avg of §4 category_scores across all categories.

    Returns a dict with keys: subfault, category.
    """
    precision = _precision()
    obs = _build_timing_obs(docs, metric_name, sla_map, precision)
    subfault = _agg_subfault_grain(obs, precision)
    category = _agg_category_grain(obs, subfault, precision)
    return {
        "subfault": subfault,
        "category": category,
    }


def compute_numeric_aggregates(
    docs: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """
    Compute all numeric aggregates across per-run documents.

    Aggregation strategies:
    - time_to_detect / time_to_mitigate: SLA-aware detection-weighted scorecard
      (subfault / category / cumulative grains) when SLA config is present;
      falls back to mean, median, std_dev, p95, min, max otherwise
    - action_correctness: mean, median, std_dev
    - response_quality_score / reasoning_score: mean, median + scale
    - hallucination_score: mean, median, max
    - input_tokens / output_tokens: mean, median, sum
    - number_of_pii_instances_detected / malicious_prompts_detected: sum, mean
    - authentication_failure_rate: mean, min
    """
    results: Dict[str, Dict[str, Any]] = {}
    precision = _precision()

    # Timing metrics — SLA-aware, detection-weighted scoring
    cfg = _get_config()
    timing_sla_cfg = cfg.get("sla", {})
    for metric in ["time_to_detect", "time_to_mitigate"]:
        sla_map = timing_sla_cfg.get(metric, {})
        results[metric] = compute_timing_scorecard(docs, metric, sla_map)

    # Action correctness (from tool_selection_accuracy)
    vals = _extract_numeric_values(docs, "quantitative", "tool_selection_accuracy")
    results["action_correctness"] = compute_stats(vals, ["mean", "median", "std_dev"])

    # Response quality score (from reasoning_quality_score)
    # Note: Scale changed from 0-10 to 0-1; values are normalized if needed
    vals = _extract_numeric_values(docs, "qualitative", "reasoning_quality_score")
    # Normalize any 0-10 scale values to 0-1 (backwards compatibility)
    vals = [v / 10.0 if v > 1.0 else v for v in vals]
    agg = compute_stats(vals, ["mean", "median"])
    if agg:
        agg["scale"] = "0-1"
    results["response_quality_score"] = agg

    # Reasoning score (same source, replicated for scorecard)
    agg = compute_stats(vals, ["mean", "median"])
    if agg:
        agg["scale"] = "0-1"
    results["reasoning_score"] = agg

    # Hallucination score
    vals = _extract_numeric_values(docs, "qualitative", "hallucination_score")
    results["hallucination_score"] = compute_stats(vals, ["mean", "median", "max"])

    # Token metrics
    for metric in ["input_tokens", "output_tokens"]:
        vals = _extract_numeric_values(docs, "quantitative", metric)
        results[metric] = compute_stats(vals, ["mean", "median", "sum"])

    # Count metrics
    for metric in ["number_of_pii_instances_detected", "malicious_prompts_detected"]:
        vals = _extract_numeric_values(docs, "quantitative", metric)
        results[metric] = compute_stats(vals, ["sum", "mean"])

    # Authentication failure rate
    vals = _extract_numeric_values(docs, "quantitative", "authentication_failure_rate")
    if not vals:
        success_vals = _extract_numeric_values(docs, "quantitative", "authentication_success_rate")
        if success_vals:
            vals = [round(1.0 - v, precision) for v in success_vals]
    results["authentication_failure_rate"] = compute_stats(vals, ["mean", "min"])

    # Remove empty entries
    return {k: v for k, v in results.items() if v}


# ---------------------------------------------------------------------------
# Derived rate metrics
# ---------------------------------------------------------------------------

def _group_docs_by_run(docs: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group per-fault metric docs by run_id.

    A single run can produce multiple metric docs (one per injected fault).
    For rate calculations we want the denominator to be distinct *runs*, not
    fault evaluations, so the agent isn't unfairly counted twice when a run
    exercises multiple faults of the same category.

    Docs without an extractable run_id each form their own pseudo-run group
    (legacy semantics) so older fixtures and tests continue to work.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    fallback_idx = 0
    for doc in docs:
        rid = doc.get("run_id") or doc.get("quantitative", {}).get("run_id")
        if not rid:
            rid = f"__no_run_id_{fallback_idx}__"
            fallback_idx += 1
        groups.setdefault(rid, []).append(doc)
    return groups


def compute_derived_rates(docs: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """
    Compute derived rates at *distinct-run* grain.

    Each run may contribute multiple per-fault metric docs. We collapse those
    to a single per-run boolean before computing the rate so the denominator
    matches the actual number of agent runs.

    Aggregation rules per run:
    - detection / mitigation / RAI / security ``success`` → AND across docs
      (a run is a "success" only if every fault evaluation in it succeeded)
    - false_negative / false_positive → OR across docs (any miss flags the run)

    Returns:
    - fault_detection_success_rate
    - fault_mitigation_success_rate
    - false_negative_rate / false_positive_rate
    - rai_compliance_rate / security_compliance_rate
    """
    precision = _precision()

    if not docs:
        return {
            "fault_detection_success_rate": None,
            "fault_mitigation_success_rate": None,
            "false_negative_rate": None,
            "false_positive_rate": None,
            "rai_compliance_rate": None,
            "security_compliance_rate": None,
        }

    groups = _group_docs_by_run(docs)

    detection_success = 0
    mitigation_success = 0
    false_negatives = 0
    false_positives = 0
    rai_passed = 0
    security_compliant = 0
    total_faults = 0

    for run_docs in groups.values():
        for doc in run_docs:
            quant = doc.get("quantitative", {})
            qual = doc.get("qualitative", {})

            agent_fault_detection_time = quant.get("agent_fault_detection_time")
            detected_fault_type = quant.get("detected_fault_type")
            injected_fault_name = quant.get("injected_fault_name")

            is_detected = agent_fault_detection_time is not None
            if is_detected:
                detection_success += 1
            else:
                false_negatives += 1

            if is_detected and injected_fault_name and detected_fault_type:
                is_fp = detected_fault_type.lower() != injected_fault_name.lower()
                if is_fp:
                    false_positives += 1

            if quant.get("agent_fault_mitigation_time") is not None:
                mitigation_success += 1

            if qual.get("rai_check_status") == "Passed":
                rai_passed += 1

            if qual.get("security_compliance_status") == "Compliant":
                security_compliant += 1

            total_faults += 1

    return {
        "fault_detection_success_rate": round(detection_success / total_faults, precision) if total_faults > 0 else 0,
        "fault_mitigation_success_rate": round(mitigation_success / total_faults, precision) if total_faults > 0 else 0,
        "false_negative_rate": round(false_negatives / total_faults, precision) if total_faults > 0 else 0,
        "false_positive_rate": round(false_positives / total_faults, precision) if total_faults > 0 else 0,
        "rai_compliance_rate": round(rai_passed / total_faults, precision) if total_faults > 0 else 0,
        "security_compliance_rate": round(security_compliant / total_faults, precision) if total_faults > 0 else 0,
    }


# ---------------------------------------------------------------------------
# Boolean / status aggregates
# ---------------------------------------------------------------------------

def compute_boolean_aggregates(
    docs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Aggregate PII / hallucination flags at *distinct-run* grain.

    A run is flagged if ANY of its per-fault docs reports the condition. The
    denominator is the number of distinct runs (not fault evaluations) so a
    run with multiple faults of the same category is not double-counted.

    Returns:
    - pii_detection: { any_detected, detection_rate }
    - hallucination_detection: { any_detected, detection_rate }
    """
    precision = _precision()

    if not docs:
        return {
            "pii_detection": {"any_detected": None, "detection_rate": None},
            "hallucination_detection": {"any_detected": None, "detection_rate": None},
        }

    groups = _group_docs_by_run(docs)
    total = len(groups)

    pii_count = 0
    hallucination_count = 0

    for run_docs in groups.values():
        any_pii = any(d.get("quantitative", {}).get("pii_detection") is True for d in run_docs)
        any_hallu = any(
            (d.get("qualitative", {}).get("hallucination_score") or 0) > 0
            for d in run_docs
        )
        if any_pii:
            pii_count += 1
        if any_hallu:
            hallucination_count += 1

    return {
        "pii_detection": {
            "any_detected": pii_count > 0,
            "detection_rate": round(pii_count / total, precision),
        },
        "hallucination_detection": {
            "any_detected": hallucination_count > 0,
            "detection_rate": round(hallucination_count / total, precision),
        },
    }
