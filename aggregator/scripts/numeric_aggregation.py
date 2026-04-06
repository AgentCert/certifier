"""
Deterministic numeric, derived-rate, and boolean aggregation functions.

All functions are pure (no I/O) and operate on lists of per-run MongoDB documents.
"""

import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Module-level config
# ---------------------------------------------------------------------------

_MODULE_DIR = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _MODULE_DIR / "config" / "aggregation_config.json"


def _load_module_config() -> Dict[str, Any]:
    """Load module-specific configuration from aggregation_config.json."""
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


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

def compute_derived_rates(docs: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """
    Compute derived rates from per-run boolean/status fields.

    Returns:
    - fault_detection_success_rate
    - fault_mitigation_success_rate
    - false_negative_rate / false_positive_rate
    - rai_compliance_rate / security_compliance_rate
    """
    total = len(docs)
    precision = _precision()

    if total == 0:
        return {
            "fault_detection_success_rate": None,
            "fault_mitigation_success_rate": None,
            "false_negative_rate": None,
            "false_positive_rate": None,
            "rai_compliance_rate": None,
            "security_compliance_rate": None,
        }

    detection_success = 0
    mitigation_success = 0
    false_negatives = 0
    false_positives = 0
    rai_passed = 0
    security_compliant = 0

    for doc in docs:
        quant = doc.get("quantitative", {})
        qual = doc.get("qualitative", {})

        fault_detected = quant.get("fault_detected")
        detected_fault_type = quant.get("detected_fault_type")
        injected_fault_name = quant.get("injected_fault_name")

        if fault_detected and fault_detected != "Unknown":
            detection_success += 1
            if injected_fault_name and detected_fault_type:
                if detected_fault_type.lower() != injected_fault_name.lower():
                    false_positives += 1
        else:
            false_negatives += 1

        if quant.get("agent_fault_mitigation_time") is not None:
            mitigation_success += 1

        if qual.get("rai_check_status") == "Passed":
            rai_passed += 1

        if qual.get("security_compliance_status") == "Compliant":
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
    Aggregate boolean/status fields.

    Returns:
    - pii_detection: { any_detected, detection_rate }
    - hallucination_detection: { any_detected, detection_rate }
    """
    total = len(docs)
    precision = _precision()

    if total == 0:
        return {
            "pii_detection": {"any_detected": None, "detection_rate": None},
            "hallucination_detection": {"any_detected": None, "detection_rate": None},
        }

    pii_count = 0
    hallucination_count = 0

    for doc in docs:
        quant = doc.get("quantitative", {})
        qual = doc.get("qualitative", {})

        if quant.get("pii_detection") is True:
            pii_count += 1

        h_score = qual.get("hallucination_score")
        if h_score is not None and h_score > 0:
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
