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


def _h01_per_cat_lookup(sh, metric_key):
    """Return {category: per-category record} from H-01 for a given metric.

    Returns empty dict when statistical_hypothesis is missing or skipped.
    """
    if not sh or sh.get("status") != "ok":
        return {}
    inner = _safe_get(sh, "results", "results", default={}) or {}
    if not isinstance(inner, dict):
        return {}
    h01 = (inner.get("h01") or {}).get(metric_key) or {}
    return {rec.get("category"): rec for rec in (h01.get("per_category") or [])}


def _h02_per_cat_lookup(sh, metric_key):
    """Return {category: per-category record} from H-02 for a given metric."""
    if not sh or sh.get("status") != "ok":
        return {}
    inner = _safe_get(sh, "results", "results", default={}) or {}
    if not isinstance(inner, dict):
        return {}
    h02 = (inner.get("h02") or {}).get(metric_key) or {}
    return {rec.get("category"): rec for rec in (h02.get("per_category") or [])}


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


def _build_ttd_stats(categories, sh=None):
    h01 = _h01_per_cat_lookup(sh, "time_to_detect")
    has_h01 = bool(h01)
    headers = ["Category", "Runs", "IQM", "Median", "P95", "BCa CI Lower", "BCa CI Upper"] \
        if has_h01 else ["Category", "Runs", "Mean", "Median", "Std Dev", "P95", "Min", "Max"]
    rows = []
    for cat in categories:
        ttd = cat.get("numeric", {}).get("time_to_detect", {})
        if has_h01:
            cat_key = cat.get("fault_category") or cat.get("label", "").lower() + "_fault"
            rec = h01.get(cat_key) or {}
            rows.append([
                cat.get("label", "N/A"),
                cat.get("total_runs", 0),
                _fmt_time(rec.get("iqm") if rec else ttd.get("mean")),
                _fmt_time(ttd.get("median")),
                _fmt_time(ttd.get("p95")),
                _fmt_time(rec.get("ci_lower")),
                _fmt_time(rec.get("ci_upper")),
            ])
        else:
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


def _build_ttm_stats(categories, sh=None):
    h01 = _h01_per_cat_lookup(sh, "time_to_mitigate")
    has_h01 = bool(h01)
    headers = ["Category", "Runs", "IQM", "Median", "P95", "BCa CI Lower", "BCa CI Upper"] \
        if has_h01 else ["Category", "Runs", "Mean", "Median", "Std Dev", "P95", "Min", "Max"]
    rows = []
    for cat in categories:
        ttm = cat.get("numeric", {}).get("time_to_mitigate", {})
        if has_h01:
            cat_key = cat.get("fault_category") or cat.get("label", "").lower() + "_fault"
            rec = h01.get(cat_key) or {}
            rows.append([
                cat.get("label", "N/A"),
                cat.get("total_runs", 0),
                _fmt_time(rec.get("iqm") if rec else ttm.get("mean")),
                _fmt_time(ttm.get("median")),
                _fmt_time(ttm.get("p95")),
                _fmt_time(rec.get("ci_lower")),
                _fmt_time(rec.get("ci_upper")),
            ])
        else:
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


def _build_detection_rates(categories, sh=None):
    h02 = _h02_per_cat_lookup(sh, "fault_detection_success_rate")
    has_h02 = bool(h02)
    if has_h02:
        headers = [
            "Category", "Detection Rate", "Wilson Lower", "Wilson Upper",
            "Certified Floor", "False Negative", "False Positive", "Mitigation Rate",
        ]
    else:
        headers = [
            "Category", "Detection Rate", "False Negative", "False Positive", "Mitigation Rate",
        ]
    rows = []
    for cat in categories:
        d = cat.get("derived", {})
        if has_h02:
            cat_key = cat.get("fault_category") or cat.get("label", "").lower() + "_fault"
            rec = h02.get(cat_key) or {}
            rows.append([
                cat.get("label", "N/A"),
                _fmt_rate(d.get("fault_detection_success_rate")),
                _fmt_rate(rec.get("wilson_lower")),
                _fmt_rate(rec.get("wilson_upper")),
                _fmt_rate(rec.get("certified_floor")),
                _fmt_rate(d.get("false_negative_rate")),
                _fmt_rate(d.get("false_positive_rate")),
                _fmt_rate(d.get("fault_mitigation_success_rate")),
            ])
        else:
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


def _build_reasoning_quality(categories, sh=None):
    h01 = _h01_per_cat_lookup(sh, "reasoning_quality_score")
    has_h01 = bool(h01)
    headers = ["Category", "Reasoning Mean", "Reasoning Median", "Reasoning IQM", "Reasoning 95% BCA CI", "Response Mean", "Response Median"] \
        if has_h01 else ["Category", "Reasoning Mean", "Reasoning Median", "Response Mean", "Response Median"]
    rows = []
    for cat in categories:
        n = cat.get("numeric", {})
        rs = n.get("reasoning_score", {})
        rq = n.get("response_quality_score", {})
        
        if has_h01:
            cat_key = cat.get("fault_category") or cat.get("label", "").lower() + "_fault"
            rec = h01.get(cat_key) or {}
            iqm_val = rec.get("iqm")
            ci_lower = rec.get("ci_lower")
            ci_upper = rec.get("ci_upper")
            if iqm_val is not None and ci_lower is not None and ci_upper is not None:
                ci_str = f"[{ci_lower:.3f}, {ci_upper:.3f}]"
            else:
                ci_str = "N/A"
            rows.append([
                cat.get("label", "N/A"),
                _fmt_score(rs.get("mean")),
                _fmt_score(rs.get("median")),
                _fmt_score(iqm_val),
                ci_str,
                _fmt_score(rq.get("mean")),
                _fmt_score(rq.get("median")),
            ])
        else:
            rows.append([
                cat.get("label", "N/A"),
                _fmt_score(rs.get("mean")),
                _fmt_score(rs.get("median")),
                _fmt_score(rq.get("mean")),
                _fmt_score(rq.get("median")),
            ])
    return {"headers": headers, "rows": rows}


def _build_hallucination(categories, sh=None):
    h01 = _h01_per_cat_lookup(sh, "hallucination_score")
    has_h01 = bool(h01)
    headers = ["Category", "Mean", "IQM", "BCA 95% CI", "Certified Ceiling", "Max (Worst Case)", "Flagged Runs"] \
        if has_h01 else ["Category", "Mean", "Max", "Flagged Runs", "Assessment"]
    rows = []
    for cat in categories:
        h = cat.get("numeric", {}).get("hallucination_score", {})
        hd = cat.get("boolean", {}).get("hallucination_detection", {})
        total_runs = cat.get("successful_runs") or cat.get("total_runs", 0)
        det_rate = hd.get("detection_rate", 0.0)
        flagged = int(round(det_rate * total_runs))
        max_val = h.get("max", 0.0) or 0.0
        
        if has_h01:
            cat_key = cat.get("fault_category") or cat.get("label", "").lower() + "_fault"
            rec = h01.get(cat_key) or {}
            iqm_val = rec.get("iqm")
            ci_lower = rec.get("ci_lower")
            ci_upper = rec.get("ci_upper")
            if iqm_val is not None and ci_lower is not None and ci_upper is not None:
                ci_str = f"[{ci_lower:.3f}, {ci_upper:.3f}]"
            else:
                ci_str = "N/A"
            rows.append([
                cat.get("label", "N/A"),
                _fmt_score(h.get("mean"), decimals=3),
                _fmt_score(iqm_val, decimals=3),
                ci_str,
                _fmt_score(ci_upper, decimals=3),
                _fmt_score(max_val, decimals=2),
                f"{flagged}/{total_runs}",
            ])
        else:
            assessment = "Clean" if max_val == 0 else "Minor" if max_val < 0.3 else "Significant"
            rows.append([
                cat.get("label", "N/A"),
                h.get("mean", 0.0),
                h.get("max", 0.0),
                f"{flagged}/{total_runs}",
                assessment,
            ])
    return {"headers": headers, "rows": rows}


def _build_rai_compliance(categories, sh=None):
    h02 = _h02_per_cat_lookup(sh, "rai_compliance_rate")
    has_h02 = bool(h02)
    headers = ["Category", "Rate (K/N)", "95% Wilson CI", "Certified Floor", "Assessment", "Agreement"] \
        if has_h02 else ["Category", "Status", "Rate", "Assessment", "Confidence", "Agreement"]
    rows = []
    for cat in categories:
        d = cat.get("derived", {})
        rai = _safe_get(cat, "textual", "rai_check_summary", default={})
        rate = d.get("rai_compliance_rate")
        
        if has_h02:
            cat_key = cat.get("fault_category") or cat.get("label", "").lower() + "_fault"
            rec = h02.get(cat_key) or {}
            rate_rec = rec.get("rate")
            wilson_lower = rec.get("wilson_lower")
            wilson_upper = rec.get("wilson_upper")

            # Format Rate (K/N) using successful runs for exact integer counts
            total_runs = cat.get("successful_runs") or cat.get("total_runs", 0)
            passed = int(round((rate or 0) * total_runs)) if rate is not None else 0
            rate_str = f"{passed}/{total_runs}" if total_runs > 0 else "N/A"
            
            # Format Wilson CI
            if wilson_lower is not None and wilson_upper is not None:
                ci_str = f"[{wilson_lower:.1%}, {wilson_upper:.1%}]"
            else:
                ci_str = "N/A"
            
            # Certified Floor is the lower bound
            floor_str = f"{wilson_lower:.1%}" if wilson_lower is not None else "N/A"
            
            severity = rai.get("severity_label", "N/A")
            assessment = severity if severity != "N/A" else "N/A"
            
            rows.append([
                cat.get("label", "N/A"),
                rate_str,
                ci_str,
                floor_str,
                assessment,
                rai.get("inter_judge_agreement", "N/A"),
            ])
        else:
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


def _build_security_compliance(categories, sh=None):
    h02 = _h02_per_cat_lookup(sh, "security_compliance_rate")
    has_h02 = bool(h02)
    headers = ["Category", "Rate (K/N)", "95% Wilson CI", "Certified Floor", "PII Instances", "Malicious Prompts"] \
        if has_h02 else ["Category", "Status", "Rate", "PII Instances", "Malicious Prompts", "Assessment", "Confidence"]
    rows = []
    for cat in categories:
        d = cat.get("derived", {})
        sec = _safe_get(cat, "textual", "security_compliance_summary", default={})
        rate = d.get("security_compliance_rate")
        pii = cat.get("numeric", {}).get("pii_instances", {})
        mal = cat.get("numeric", {}).get("malicious_prompts", {})
        pii_val = int(pii["sum"]) if pii and "sum" in pii else "N/A"
        mal_val = int(mal["sum"]) if mal and "sum" in mal else "N/A"
        
        if has_h02:
            cat_key = cat.get("fault_category") or cat.get("label", "").lower() + "_fault"
            rec = h02.get(cat_key) or {}
            rate_rec = rec.get("rate")
            wilson_lower = rec.get("wilson_lower")
            wilson_upper = rec.get("wilson_upper")
            
            # Format Rate (K/N) using successful runs for exact integer counts
            total_runs = cat.get("successful_runs") or cat.get("total_runs", 0)
            passed = int(round((rate or 0) * total_runs)) if rate is not None else 0
            rate_str = f"{passed}/{total_runs}" if total_runs > 0 else "N/A"
            
            # Format Wilson CI
            if wilson_lower is not None and wilson_upper is not None:
                ci_str = f"[{wilson_lower:.1%}, {wilson_upper:.1%}]"
            else:
                ci_str = "N/A"
            
            # Certified Floor is the lower bound
            floor_str = f"{wilson_lower:.1%}" if wilson_lower is not None else "N/A"
            
            rows.append([
                cat.get("label", "N/A"),
                rate_str,
                ci_str,
                floor_str,
                pii_val,
                mal_val,
            ])
        else:
            status = "Pass" if rate is not None and rate >= 1.0 else "Fail"
            severity = sec.get("severity_label", "N/A")
            confidence = sec.get("confidence", "N/A")
            assessment = f"{severity} / {confidence}" if severity != "N/A" and confidence != "N/A" else "N/A"
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

def build_all_tables(categories, sh=None):
    """Build all 13 tables from categories list.

    When ``sh`` (statistical_hypothesis dict from parsed_context) is provided
    AND has status == 'ok', TTD/TTM stats tables include IQM + BCa CI columns
    from H-01, and the detection-rates table includes Wilson CI + Certified
    Floor columns from H-02.
    """
    result = TablesResult.model_validate({
        "tables": {
            "judge_models": _build_judge_models(),
            "ttd_stats": _build_ttd_stats(categories, sh),
            "ttm_stats": _build_ttm_stats(categories, sh),
            "detection_rates": _build_detection_rates(categories, sh),
            "safety_summary": _build_safety_summary(categories),
            "action_correctness": _build_action_correctness(categories),
            "reasoning_quality": _build_reasoning_quality(categories, sh),
            "hallucination": _build_hallucination(categories, sh),
            "rai_compliance": _build_rai_compliance(categories, sh),
            "security_compliance": _build_security_compliance(categories, sh),
            "token_usage": _build_token_usage(categories),
            "limitations": _build_limitations(categories),
            "recommendations": _build_recommendations(categories),
        }
    })
    return result.model_dump(mode="json")


def build_from_file(path):
    """Load Phase 1 output and build all tables."""
    ctx = json.loads(Path(path).read_text(encoding="utf-8"))
    return build_all_tables(ctx["categories"], ctx.get("statistical_hypothesis"))
