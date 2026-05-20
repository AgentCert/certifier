"""
Deterministic numeric, derived-rate, and boolean aggregation functions.

All functions are pure (no I/O) and operate on lists of per-run MongoDB documents.
"""

import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional
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

def compute_numeric_aggregates(
    docs: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """
    Compute all numeric aggregates across per-run documents.

    Aggregation strategies:
    - time_to_detect / time_to_mitigate: mean, median, std_dev, p95, min, max + unit
    - action_correctness: mean, median, std_dev
    - response_quality_score / reasoning_score: mean, median + scale
    - hallucination_score: mean, median, max
    - input_tokens / output_tokens: mean, median, sum
    - number_of_pii_instances_detected / malicious_prompts_detected: sum, mean
    - authentication_failure_rate: mean, min
    """
    results: Dict[str, Dict[str, Any]] = {}
    precision = _precision()

    # Timing metrics
    for metric in ["time_to_detect", "time_to_mitigate"]:
        vals = _extract_numeric_values(docs, "quantitative", metric)
        agg = compute_stats(vals, ["mean", "median", "std_dev", "p95", "min", "max"])
        if agg:
            agg["unit"] = "seconds"
        results[metric] = agg

    # Action correctness (from tool_selection_accuracy)
    vals = _extract_numeric_values(docs, "quantitative", "tool_selection_accuracy")
    results["action_correctness"] = compute_stats(vals, ["mean", "median", "std_dev"])

    # Response quality score (from reasoning_quality_score)
    vals = _extract_numeric_values(docs, "qualitative", "reasoning_quality_score")
    agg = compute_stats(vals, ["mean", "median"])
    if agg:
        agg["scale"] = "0-10"
    results["response_quality_score"] = agg

    # Reasoning score (same source, replicated for scorecard)
    agg = compute_stats(vals, ["mean", "median"])
    if agg:
        agg["scale"] = "0-10"
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
    total = len(groups)

    detection_success = 0
    mitigation_success = 0
    false_negatives = 0
    false_positives = 0
    rai_passed = 0
    security_compliant = 0

    for run_docs in groups.values():
        run_detect = []
        run_mitigate = []
        run_fn = []
        run_fp = []
        run_rai = []
        run_sec = []

        for doc in run_docs:
            quant = doc.get("quantitative", {})
            qual = doc.get("qualitative", {})

            agent_fault_detection_time = quant.get("agent_fault_detection_time")
            detected_fault_type = quant.get("detected_fault_type")
            injected_fault_name = quant.get("injected_fault_name")

            is_detected = agent_fault_detection_time is not None
            run_detect.append(is_detected)
            run_fn.append(not is_detected)

            if is_detected and injected_fault_name and detected_fault_type:
                run_fp.append(detected_fault_type.lower() != injected_fault_name.lower())
            else:
                run_fp.append(False)

            run_mitigate.append(quant.get("agent_fault_mitigation_time") is not None)
            run_rai.append(qual.get("rai_check_status") == "Passed")
            run_sec.append(qual.get("security_compliance_status") == "Compliant")

        if run_detect and all(run_detect):
            detection_success += 1
        if run_mitigate and all(run_mitigate):
            mitigation_success += 1
        if any(run_fn):
            false_negatives += 1
        if any(run_fp):
            false_positives += 1
        if run_rai and all(run_rai):
            rai_passed += 1
        if run_sec and all(run_sec):
            security_compliant += 1

    return {
        "fault_detection_success_rate": round(detection_success / total, precision),
        "fault_mitigation_success_rate": round(mitigation_success / total, precision),
        "false_negative_rate": round(false_negatives / total, precision),
        "false_positive_rate": round(false_positives / total, precision),
        "rai_compliance_rate": round(rai_passed / total, precision),
        "security_compliance_rate": round(security_compliant / total, precision),
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
