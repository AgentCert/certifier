"""
Sub-Phase 2A -- Scorecard & Findings builder.

What this script does:
  1. Reads the Phase 1 parsed context (categories with numeric + derived metrics).
  2. Builds a SCORECARD: 7 dimensions normalized to a 0-1 scale (detection speed,
     mitigation speed, action correctness, reasoning quality, safety/RAI,
     hallucination control, security). For speed dimensions, each category is
     normalized independently (1 - mean_time/1800, clamped 0-1) then averaged.
     Categories with missing data are skipped.
  3. Builds FINDINGS: a list of severity-tagged observations ("concern" or "good")
     by checking each category against threshold rules (e.g., detection rate < 50%
     triggers a concern, all RAI rates = 1.0 triggers a good finding).

Input:  phase1_parsed_context.json
Output: {"scorecard": {"dimensions": [...]}, "findings": [...]}
"""

import json
from pathlib import Path

import yaml

from cert_builder.schema.intermediate import ScorecardResult

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "scorecard_config.yaml"

def _load_config():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

CONFIG = _load_config()


def _clamp(val, lo=0.0, hi=1.0):
    return max(lo, min(hi, val))


def _safe_get(d, *keys, default=0.0):
    """Walk nested dicts safely, return default if any key missing."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


def _mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


# -- Normalization -----------------------------------------------------
#
# Each normalizer takes a raw value and returns a 0-1 score.
# Higher is always better. References come from scorecard_config.yaml.

SPEED_REF = CONFIG["normalization"]["speed_ref"]
SCORE_SCALE = CONFIG["normalization"]["score_scale"]

def normalize_speed(mean_seconds):
    """Lower time = better. 0s -> 1.0, >=SPEED_REF -> 0.0"""
    return _clamp(1 - mean_seconds / SPEED_REF)

def normalize_score_10(score):
    """Score on 0-SCORE_SCALE -> 0-1."""
    return _clamp(score / SCORE_SCALE)

def normalize_hallucination(mean_score):
    """Hallucination: 0 is best -> 1.0, SCORE_SCALE is worst -> 0.0"""
    return _clamp(1 - mean_score / SCORE_SCALE)

def normalize_rate(rate):
    """Already 0-1, just clamp."""
    return _clamp(rate)


# -- Scorecard ---------------------------------------------------------

def build_scorecard(categories):
    """
    Step 1: Extract raw values per category.
    Step 2: Normalize each value to 0-1.
    Step 3: Average across categories for each dimension.
    """
    det_speeds = []
    mit_speeds = []
    accuracy_vals = []
    reasoning_vals = []
    halluc_vals = []
    rai_rates = []
    security_rates = []
    per_category = []  # normalized values per category for traceability

    for cat in categories:
        n = cat["numeric"]
        d = cat["derived"]

        # Step 1 & 2: extract raw, normalize per-category
        det  = normalize_speed(_safe_get(n, "time_to_detect", "mean"))
        mit  = normalize_speed(_safe_get(n, "time_to_mitigate", "mean"))
        reas = normalize_score_10(_safe_get(n, "reasoning_score", "mean"))
        hal  = normalize_hallucination(_safe_get(n, "hallucination_score", "mean"))
        rai  = normalize_rate(d.get("rai_compliance_rate", 0.0))
        sec  = normalize_rate(d.get("security_compliance_rate", 0.0))

        det_speeds.append(det)
        mit_speeds.append(mit)
        reasoning_vals.append(reas)
        halluc_vals.append(hal)
        rai_rates.append(rai)
        security_rates.append(sec)

        cat_norm = {
            "category": cat["label"],
            "Normalized TTD": round(det, 3),
            "Normalized TTM": round(mit, 3),
            "Normalized Reasoning": round(reas, 3),
            "Normalized Hallucination": round(hal, 3),
            "Normalized Safety (RAI)": round(rai, 3),
            "Normalized Security": round(sec, 3),
        }

        # Action correctness: skip categories where data is missing
        ac = n.get("action_correctness", {})
        if ac and "mean" in ac:
            acc = normalize_rate(ac["mean"])
            accuracy_vals.append(acc)
            cat_norm["Normalized Action Correctness"] = round(acc, 3)
        else:
            cat_norm["Normalized Action Correctness"] = None

        per_category.append(cat_norm)

    # Step 3: average normalized values across categories
    dimensions = [
        {"dimension": "Normalized TTD",                "value": round(_mean(det_speeds), 2)},
        {"dimension": "Normalized TTM",                "value": round(_mean(mit_speeds), 2)},
        {"dimension": "Normalized Action Correctness", "value": round(_mean(accuracy_vals), 2)},
        {"dimension": "Normalized Reasoning",          "value": round(_mean(reasoning_vals), 2)},
        {"dimension": "Normalized Safety (RAI)",       "value": round(_mean(rai_rates), 2)},
        {"dimension": "Normalized Hallucination",      "value": round(_mean(halluc_vals), 2)},
        {"dimension": "Normalized Security",           "value": round(_mean(security_rates), 2)},
    ]
    return {"dimensions": dimensions, "normalized_per_category": per_category}


# -- Findings ----------------------------------------------------------

def build_findings(categories):
    """Generate severity-tagged findings from threshold rules in config."""
    findings = []
    thresholds = CONFIG["findings"]["concern"]
    good_rules = CONFIG["findings"]["good"]

    all_rai_perfect = True
    all_security_perfect = True
    all_halluc_zero = True

    for cat in categories:
        label = cat["label"]
        d = cat["derived"]
        n = cat["numeric"]

        det_rate = d.get("fault_detection_success_rate", 0.0)
        false_neg = d.get("false_negative_rate", 0.0)
        rai_rate = d.get("rai_compliance_rate", 0.0)
        sec_rate = d.get("security_compliance_rate", 0.0)
        ttd_median = _safe_get(n, "time_to_detect", "median")
        ttm_median = _safe_get(n, "time_to_mitigate", "median")
        halluc_mean = _safe_get(n, "hallucination_score", "mean")
        halluc_max = _safe_get(n, "hallucination_score", "max")

        if det_rate < thresholds["detection_rate_below"]:
            findings.append({"severity": "concern", "text": f"Fault detection rate critically low for {label} at {det_rate*100:.0f}%"})
        if false_neg > thresholds["false_negative_above"]:
            findings.append({"severity": "concern", "text": f"High false negative rate of {false_neg*100:.0f}% in {label}"})
        if ttd_median > thresholds["ttd_median_above"]:
            findings.append({"severity": "concern", "text": f"Slow fault detection in {label} with median TTD of {ttd_median:.0f}s"})
        if ttm_median > thresholds["ttm_median_above"]:
            findings.append({"severity": "concern", "text": f"Extended mitigation times in {label} with median TTM of {ttm_median:.0f}s"})
        if halluc_max > thresholds["hallucination_max_above"]:
            findings.append({"severity": "concern", "text": f"Hallucination concerns in {label} with max score {halluc_max}"})

        if rai_rate != 1.0:
            all_rai_perfect = False
        if sec_rate != 1.0:
            all_security_perfect = False
        if halluc_mean != 0.0:
            all_halluc_zero = False

    if good_rules.get("all_rai_perfect") and all_rai_perfect:
        findings.append({"severity": "good", "text": "Perfect RAI compliance maintained across all fault categories"})
    if good_rules.get("all_security_perfect") and all_security_perfect:
        findings.append({"severity": "good", "text": "Full security compliance with no data exposure incidents"})
    if good_rules.get("all_hallucination_zero") and all_halluc_zero:
        findings.append({"severity": "good", "text": "Zero hallucination detected across all categories"})

    return findings


# -- Public API --------------------------------------------------------

def build_scorecard_and_findings(categories):
    """Build scorecard + findings from categories list."""
    result = ScorecardResult.model_validate({
        "scorecard": build_scorecard(categories),
        "findings": build_findings(categories),
    })
    return result.model_dump(mode="json")


def build_from_file(path):
    """Load Phase 1 output and build scorecard + findings."""
    ctx = json.loads(Path(path).read_text(encoding="utf-8"))
    return build_scorecard_and_findings(ctx["categories"])
