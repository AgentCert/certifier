"""
Sub-Phase 2B -- Table builder.

What this script does:
  1. Reads Phase 1 parsed context (categories with numeric, derived, boolean, textual).
  2. Builds 13 tables, each as {"headers": [...], "rows": [[...], ...]}.
  3. Tables 1-11 are per-category (one row per fault category).
     Tables 12-13 merge items across all categories and sort them.

Tables produced:
  1. judge_models       -- Static LLM Council judges (from config)
  2. ttd_stats          -- Time-to-Detect statistics per category
  3. ttm_stats          -- Time-to-Mitigate statistics per category
  4. detection_rates    -- Detection/mitigation rates per category
  5. safety_summary     -- RAI, Security, PII, Hallucination flags
  6. action_correctness -- Action correctness stats per category
  7. reasoning_quality  -- Reasoning + response quality per category
  8. hallucination      -- Hallucination scores per category
  9. rai_compliance     -- RAI compliance details per category
  10. security_compliance -- Security compliance details per category
  11. token_usage       -- Token consumption per category
  12. limitations       -- Merged limitations across categories (sorted by severity)
  13. recommendations   -- Merged recommendations across categories (sorted by priority)

Input:  phase1_parsed_context.json
Output: {"tables": {"judge_models": {...}, "ttd_stats": {...}, ...}}
"""

import json
from pathlib import Path
from typing import Any

import yaml

from cert_builder.schema.intermediate import TablesResult

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "table_config.yaml"


def _load_config():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


CONFIG = _load_config()


def _safe_get(d, *keys, default=None):
    """Walk nested dicts safely, return default if any key missing."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


def _fmt_time(val):
    """Format a time value: round to 1 decimal, append 's'."""
    if val is None:
        return "N/A"
    return f"{val:.1f}s"


def _fmt_rate(val):
    """Format a rate as percentage: multiply by 100, append '%'."""
    if val is None:
        return "N/A"
    return f"{val * 100:.0f}%"


def _fmt_score(val, decimals=2):
    """Format a score to N decimals."""
    if val is None:
        return "N/A"
    return f"{val:.{decimals}f}"


# -- Individual table builders ------------------------------------------------

def _build_judge_models():
    return CONFIG["judge_models"]


def _build_ttd_stats(categories):
    headers = ["Category", "Runs", "Mean", "Median", "Std Dev", "P95", "Min", "Max"]
    rows = []
    for cat in categories:
        ttd = cat.get("numeric", {}).get("time_to_detect", {})
        rows.append([
            cat.get("label", "N/A"),
            cat.get("total_runs", 0),
            _fmt_time(ttd.get("mean")),
            _fmt_time(ttd.get("median")),
            _fmt_time(ttd.get("std_dev")),
            _fmt_time(ttd.get("p95")),
            _fmt_time(ttd.get("min")),
            _fmt_time(ttd.get("max")),
        ])
    return {"headers": headers, "rows": rows}


def _build_ttm_stats(categories):
    headers = ["Category", "Runs", "Mean", "Median", "Std Dev", "P95", "Min", "Max"]
    rows = []
    for cat in categories:
        ttm = cat.get("numeric", {}).get("time_to_mitigate", {})
        rows.append([
            cat.get("label", "N/A"),
            cat.get("total_runs", 0),
            _fmt_time(ttm.get("mean")),
            _fmt_time(ttm.get("median")),
            _fmt_time(ttm.get("std_dev")),
            _fmt_time(ttm.get("p95")),
            _fmt_time(ttm.get("min")),
            _fmt_time(ttm.get("max")),
        ])
    return {"headers": headers, "rows": rows}


def _build_detection_rates(categories):
    headers = ["Category", "Detection Rate", "False Negative", "False Positive", "Mitigation Rate"]
    rows = []
    for cat in categories:
        d = cat.get("derived", {})
        rows.append([
            cat.get("label", "N/A"),
            _fmt_rate(d.get("fault_detection_success_rate")),
            _fmt_rate(d.get("false_negative_rate")),
            _fmt_rate(d.get("false_positive_rate")),
            _fmt_rate(d.get("fault_mitigation_success_rate")),
        ])
    return {"headers": headers, "rows": rows}


def _build_safety_summary(categories):
    headers = ["Category", "RAI Rate", "Security Rate", "PII Detected", "Hallucination Detected"]
    rows = []
    for cat in categories:
        d = cat.get("derived", {})
        b = cat.get("boolean", {})
        rows.append([
            cat.get("label", "N/A"),
            _fmt_rate(d.get("rai_compliance_rate")),
            _fmt_rate(d.get("security_compliance_rate")),
            b.get("pii_detection", {}).get("any_detected", False),
            b.get("hallucination_detection", {}).get("any_detected", False),
        ])
    return {"headers": headers, "rows": rows}


def _build_action_correctness(categories):
    headers = ["Category", "Status", "Mean", "Median", "Std Dev"]
    rows = []
    for cat in categories:
        ac = cat.get("numeric", {}).get("action_correctness", {})
        if ac and "mean" in ac:
            mean_val = ac.get("mean")
            status = "Perfect" if mean_val == 1.0 else "Partial"
            rows.append([
                cat.get("label", "N/A"),
                status,
                mean_val,
                ac.get("median"),
                ac.get("std_dev"),
            ])
        else:
            rows.append([cat.get("label", "N/A"), "N/A", "N/A", "N/A", "N/A"])
    return {"headers": headers, "rows": rows}


def _build_reasoning_quality(categories):
    headers = ["Category", "Reasoning Mean", "Reasoning Median", "Response Mean", "Response Median"]
    rows = []
    for cat in categories:
        n = cat.get("numeric", {})
        rs = n.get("reasoning_score", {})
        rq = n.get("response_quality_score", {})
        rows.append([
            cat.get("label", "N/A"),
            _fmt_score(rs.get("mean")),
            _fmt_score(rs.get("median")),
            _fmt_score(rq.get("mean")),
            _fmt_score(rq.get("median")),
        ])
    return {"headers": headers, "rows": rows}


def _build_hallucination(categories):
    headers = ["Category", "Mean", "Max", "Flagged Runs", "Assessment"]
    rows = []
    for cat in categories:
        h = cat.get("numeric", {}).get("hallucination_score", {})
        hd = cat.get("boolean", {}).get("hallucination_detection", {})
        total_runs = cat.get("total_runs", 0)
        det_rate = hd.get("detection_rate", 0.0)
        flagged = int(round(det_rate * total_runs))
        max_val = h.get("max", 0.0) or 0.0
        assessment = "Clean" if max_val == 0 else "Minor" if max_val < 0.3 else "Significant"
        rows.append([
            cat.get("label", "N/A"),
            h.get("mean", 0.0),
            h.get("max", 0.0),
            f"{flagged}/{total_runs}",
            assessment,
        ])
    return {"headers": headers, "rows": rows}


def _build_rai_compliance(categories):
    headers = ["Category", "Status", "Rate", "Assessment", "Confidence", "Agreement"]
    rows = []
    for cat in categories:
        d = cat.get("derived", {})
        rai = _safe_get(cat, "textual", "rai_check_summary", default={})
        rate = d.get("rai_compliance_rate")
        status = "Pass" if rate is not None and rate >= 1.0 else "Fail"
        severity = rai.get("severity_label", "N/A")
        confidence = rai.get("confidence", "N/A")
        assessment = f"{severity} / {confidence}" if severity != "N/A" and confidence != "N/A" else "N/A"
        rows.append([
            cat.get("label", "N/A"),
            status,
            _fmt_rate(rate),
            assessment,
            confidence,
            rai.get("inter_judge_agreement", "N/A"),
        ])
    return {"headers": headers, "rows": rows}


def _build_security_compliance(categories):
    headers = ["Category", "Status", "Rate", "PII Instances", "Malicious Prompts", "Assessment", "Confidence"]
    rows = []
    for cat in categories:
        d = cat.get("derived", {})
        sec = _safe_get(cat, "textual", "security_compliance_summary", default={})
        rate = d.get("security_compliance_rate")
        status = "Pass" if rate is not None and rate >= 1.0 else "Fail"
        severity = sec.get("severity_label", "N/A")
        confidence = sec.get("confidence", "N/A")
        assessment = f"{severity} / {confidence}" if severity != "N/A" and confidence != "N/A" else "N/A"
        pii = cat.get("numeric", {}).get("pii_instances", {})
        mal = cat.get("numeric", {}).get("malicious_prompts", {})
        pii_val = int(pii["sum"]) if pii and "sum" in pii else "N/A"
        mal_val = int(mal["sum"]) if mal and "sum" in mal else "N/A"
        rows.append([
            cat.get("label", "N/A"),
            status,
            _fmt_rate(rate),
            pii_val,
            mal_val,
            assessment,
            confidence,
        ])
    return {"headers": headers, "rows": rows}


def _build_token_usage(categories):
    headers = ["Category", "Runs", "Avg Input", "Avg Output", "Total Input", "Total Output", "Total"]
    rows = []
    for cat in categories:
        n = cat.get("numeric", {})
        inp = n.get("input_tokens", {})
        out = n.get("output_tokens", {})
        inp_sum = inp.get("sum", 0) or 0
        out_sum = out.get("sum", 0) or 0
        rows.append([
            cat.get("label", "N/A"),
            cat.get("total_runs", 0),
            inp.get("mean", 0.0),
            out.get("mean", 0.0),
            int(inp_sum),
            int(out_sum),
            int(inp_sum + out_sum),
        ])
    return {"headers": headers, "rows": rows}


def _build_limitations(categories):
    """Merge limitations from all categories, sort by severity."""
    headers = ["#", "Limitation", "Category", "Severity", "Frequency"]
    sev_order = CONFIG.get("severity_order", ["High", "Medium", "Low"])
    items = []
    for cat in categories:
        ranked = _safe_get(cat, "textual", "known_limitations", "ranked_items", default=[])
        for item in ranked:
            items.append({
                "limitation": item["limitation"],
                "category": cat["label"],
                "severity": item.get("severity", "Medium"),
                "frequency": item.get("frequency", 0),
            })

    items.sort(key=lambda x: sev_order.index(x["severity"]) if x["severity"] in sev_order else 99)

    rows = []
    for i, item in enumerate(items, 1):
        rows.append([i, item["limitation"], item["category"], item["severity"], item["frequency"]])
    return {"headers": headers, "rows": rows}


def _build_recommendations(categories):
    """Merge recommendations from all categories, sort by priority."""
    headers = ["#", "Priority", "Recommendation", "Category"]
    pri_order = CONFIG.get("priority_order", ["Critical", "High", "Medium", "Low"])
    items = []
    for cat in categories:
        prio = _safe_get(cat, "textual", "recommendations", "prioritized_items", default=[])
        for item in prio:
            items.append({
                "recommendation": item["recommendation"],
                "category": cat["label"],
                "priority": item.get("priority", "Medium"),
            })

    items.sort(key=lambda x: pri_order.index(x["priority"]) if x["priority"] in pri_order else 99)

    rows = []
    for i, item in enumerate(items, 1):
        rows.append([i, item["priority"], item["recommendation"], item["category"]])
    return {"headers": headers, "rows": rows}


# -- Public API ---------------------------------------------------------------

def build_all_tables(categories):
    """Build all 13 tables from categories list."""
    result = TablesResult.model_validate({
        "tables": {
            "judge_models": _build_judge_models(),
            "ttd_stats": _build_ttd_stats(categories),
            "ttm_stats": _build_ttm_stats(categories),
            "detection_rates": _build_detection_rates(categories),
            "safety_summary": _build_safety_summary(categories),
            "action_correctness": _build_action_correctness(categories),
            "reasoning_quality": _build_reasoning_quality(categories),
            "hallucination": _build_hallucination(categories),
            "rai_compliance": _build_rai_compliance(categories),
            "security_compliance": _build_security_compliance(categories),
            "token_usage": _build_token_usage(categories),
            "limitations": _build_limitations(categories),
            "recommendations": _build_recommendations(categories),
        }
    })
    return result.model_dump(mode="json")


def build_from_file(path):
    """Load Phase 1 output and build all tables."""
    ctx = json.loads(Path(path).read_text(encoding="utf-8"))
    return build_all_tables(ctx["categories"])
