"""
Phase 3B — Key Findings Builder.

Synthesizes Phase 2's 13 raw findings into ~5-7 cross-cutting findings
with headline, detail, and severity. This is LLM Call 2 of 6 (JSON output).

Input:  Phase 1 parsed context + Phase 2 computed content.
Output: {"key_findings": {"items": [...], "source": ..., "model": ..., "tokens_used": ...}}
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from cert_builder.schema.certification_schema import FindingSeverity
from cert_builder.scripts.narratives.llm_client import get_client, call_llm

# ---------------------------------------------------------------------------
# Load prompt config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "key_findings_prompt.yaml"
_CONFIG = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Pydantic models (intermediate — not part of certified report)
# ---------------------------------------------------------------------------

class KeyFinding(BaseModel):
    """Single synthesized finding from the LLM."""
    severity: FindingSeverity
    headline: str = Field(..., min_length=1, max_length=60)
    detail:   str = Field(..., min_length=1)


class KeyFindingsResponse(BaseModel):
    """Schema enforced on the LLM response via structured output."""
    items: list[KeyFinding] = Field(..., min_length=5, max_length=7)


class KeyFindingsSynthesis(BaseModel):
    """Envelope for Call 2 output."""
    items:       list[KeyFinding] = Field(..., min_length=5, max_length=7)
    source:      Literal["llm", "fallback"] = "llm"
    model:       str | None = None
    tokens_used: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def _build_findings_context(phase1: dict, phase2: dict) -> tuple[str, dict]:
    """Build the context block and return (context_str, template_vars)."""
    meta = phase1["meta"]
    cats = phase1["categories"]

    # Scorecard
    dims = phase2["scorecard"]["dimensions"]
    sc_lines = "\n".join(f"    {d['dimension']:30s} {d['value']}" for d in dims)

    # Raw findings
    findings = phase2["findings"]
    rf_lines = "\n".join(
        f"    [{f['severity']:7s}] {f['text']}" for f in findings
    )

    # Per-category metrics table
    labels = [c["label"] for c in cats]
    header = f"    {'':14s} " + "  ".join(f"{l:>8s}" for l in labels)
    rows = []

    def _row(name, values):
        vals = "  ".join(f"{v:>8s}" for v in values)
        return f"    {name:14s} {vals}"

    for key, label, fmt in [
        ("fault_detection_success_rate", "Detection %", lambda v: f"{v*100:.0f}%"),
        ("fault_mitigation_success_rate", "Mitigation %", lambda v: f"{v*100:.0f}%"),
        ("false_negative_rate", "False Neg %", lambda v: f"{v*100:.0f}%"),
    ]:
        rows.append(_row(label, [fmt(c["derived"][key]) for c in cats]))

    def _safe(c, key, sub, fmt):
        # Aggregator omits whole metric blocks when no run produced the
        # underlying value (e.g. time_to_detect when nothing was detected),
        # so guard against the missing key/sub combination.
        v = (c.get("numeric", {}).get(key) or {}).get(sub)
        return fmt(v) if v is not None else "N/A"

    for key, sub, label, fmt in [
        ("time_to_detect", "median", "TTD median", lambda v: f"{v:.0f}s"),
        ("time_to_mitigate", "median", "TTM median", lambda v: f"{v:.0f}s"),
        ("reasoning_score", "mean", "Reasoning", lambda v: f"{v:.2f}"),
        ("hallucination_score", "mean", "Halluc mean", lambda v: f"{v:.3f}"),
        ("hallucination_score", "max", "Halluc max", lambda v: f"{v:.2f}"),
    ]:
        rows.append(_row(label, [_safe(c, key, sub, fmt) for c in cats]))

    for key, sub, label in [
        ("rai_compliance_rate", None, "RAI rate"),
        ("security_compliance_rate", None, "Security"),
    ]:
        rows.append(_row(label, [f"{c['derived'][key]*100:.0f}%" for c in cats]))

    table = "\n".join([header] + rows)

    # Overall stats
    total_runs = meta["total_runs"]
    det_rates = [c["derived"]["fault_detection_success_rate"] for c in cats]
    runs_per = [c["total_runs"] for c in cats]
    detected_count = sum(int(r * n) for r, n in zip(det_rates, runs_per))
    overall_det = (detected_count / total_runs * 100) if total_runs else 0
    reasoning_means = [
        (c.get("numeric", {}).get("reasoning_score") or {}).get("mean")
        for c in cats
    ]
    reasoning_means = [v for v in reasoning_means if v is not None]
    avg_reasoning = (sum(reasoning_means) / len(reasoning_means)
                     if reasoning_means else 0.0)

    # Extract raw statistical hypothesis data for LLM to use directly
    sh = phase1.get("statistical_hypothesis") or {}
    hypothesis_data = sh.get("results") or {}

    context = (
        f"SCORECARD (7 dimensions):\n{sc_lines}\n\n"
        f"RAW FINDINGS ({len(findings)} items from Phase 2):\n{rf_lines}\n\n"
        f"PER-CATEGORY METRICS:\n{table}\n\n"
        f"Total runs: {total_runs}\n"
        f"Overall detection rate: {overall_det:.1f}% ({detected_count} of {total_runs} runs)\n"
        f"Overall mitigation rate: 100% ({total_runs} of {total_runs} runs)\n"
        f"Avg reasoning score: {avg_reasoning:.2f}/10"
    )

    template_vars = {
        "findings_context_block": context,
        "hypothesis_results_json": _format_hypothesis_results_for_llm(hypothesis_data),
        "overall_detection_pct": f"{overall_det:.1f}",
        "detected_count": detected_count,
        "total_runs": total_runs,
    }

    return context, template_vars


# ---------------------------------------------------------------------------
# Statistical findings extraction
# ---------------------------------------------------------------------------

def _format_hypothesis_results_for_llm(hypothesis_data: dict) -> str:
    """
    Format raw statistical hypothesis results as a readable text block for the LLM.
    Preserves full precision (CIs, p-values, effect sizes).
    Extracts ALL available metrics, not just pre-selected ones.
    """
    if not hypothesis_data:
        return "(No statistical hypothesis results available.)"
    
    results = hypothesis_data.get("results") if isinstance(hypothesis_data.get("results"), dict) else hypothesis_data
    if not isinstance(results, dict) or not results:
        return "(Statistical hypothesis results not available.)"
    
    lines = []
    
    # H-01: Continuous metrics with confidence intervals
    h01 = results.get("h01") or {}
    if h01:
        lines.append("CONTINUOUS METRICS WITH CONFIDENCE INTERVALS (IQM + BCa 95% CI):")
        # Extract ALL metrics from H-01
        for metric_name, metric_data in h01.items():
            if isinstance(metric_data, dict) and metric_data:
                metric_label = metric_name.replace("_", "-").title()
                iqm = metric_data.get('iqm_estimate')
                ci_lower = metric_data.get('ci_lower')
                ci_upper = metric_data.get('ci_upper')
                if iqm is not None and ci_lower is not None and ci_upper is not None:
                    if isinstance(iqm, (int, float)) and iqm > 100:
                        # Likely a percentage (0-100 scale)
                        lines.append(f"  {metric_label}: {iqm:.1f}% [95% CI: {ci_lower:.1f}%, {ci_upper:.1f}%]")
                    else:
                        # Likely seconds or other unit
                        lines.append(f"  {metric_label}: {iqm:.1f}s [95% CI: {ci_lower:.1f}s, {ci_upper:.1f}s]")
    
    # H-02: Success rates with safety floor (Wilson 95% CI)
    h02 = results.get("h02") or {}
    if h02:
        lines.append("\nSUCCESS RATES WITH SAFETY FLOOR (Wilson 95% CI):")
        # Extract ALL metrics from H-02
        for metric_name, metric_data in h02.items():
            if isinstance(metric_data, dict) and metric_data:
                metric_label = metric_name.replace("_", "-").title()
                
                # Check if per-category breakdown exists
                per_cat = metric_data.get("per_category") or []
                if per_cat:
                    lines.append(f"  {metric_label} (per category):")
                    for cat in per_cat:
                        cat_label = (cat.get("category") or "").replace("_fault", "").title() or "—"
                        wilson_l = (cat.get("wilson_lower", 0.0) * 100.0)
                        wilson_u = (cat.get("wilson_upper", 1.0) * 100.0)
                        lines.append(f"    {cat_label}: [{wilson_l:.1f}%, {wilson_u:.1f}%] (95% CI)")
                else:
                    # Overall value
                    wilson_l = (metric_data.get("wilson_lower", 0.0) * 100.0)
                    wilson_u = (metric_data.get("wilson_upper", 1.0) * 100.0)
                    lines.append(f"  {metric_label} (overall): [{wilson_l:.1f}%, {wilson_u:.1f}%] (95% CI)")
    
    # H-03: Cross-category uniformity tests
    h03 = results.get("h03") or {}
    if h03:
        lines.append("\nCROSS-CATEGORY UNIFORMITY TESTS (Kruskal-Wallis):")
        for metric_name, metric_data in h03.items():
            if isinstance(metric_data, dict) and metric_data:
                metric_label = metric_name.replace("_", "-").title()
                kw_p = metric_data.get("omnibus_p", "unknown")
                sig = metric_data.get("omnibus_significant", False)
                lines.append(f"  {metric_label}: p={kw_p}, Significant={sig}")
    
    # H-04: Detection rate uniformity (Chi-squared)
    h04 = results.get("h04") or {}
    if h04:
        lines.append("\nDETECTION RATE UNIFORMITY (Chi-Squared Test):")
        for metric_name, metric_data in h04.items():
            if isinstance(metric_data, dict) and metric_data:
                metric_label = metric_name.replace("_", "-").title()
                chi = metric_data.get("statistic", "unknown")
                p = metric_data.get("p_value", "unknown")
                sig = metric_data.get("significant", False)
                lines.append(f"  {metric_label}: χ²={chi}, p={p}, Significant={sig}")
    
    # H-05: Variance stability (Levene test)
    h05 = results.get("h05") or {}
    if h05:
        lines.append("\nVARIANCE STABILITY (Levene Test):")
        for metric_name, metric_data in h05.items():
            if isinstance(metric_data, dict) and metric_data:
                metric_label = metric_name.replace("_", "-").title()
                levene_p = metric_data.get("levene_p", "unknown")
                unstable = metric_data.get("unstable_categories") or []
                if unstable:
                    cat_names = ", ".join((c.replace("_fault", "").title() for c in unstable))
                    lines.append(f"  {metric_label}: Unstable in [{cat_names}] (Levene p={levene_p})")
                else:
                    lines.append(f"  {metric_label}: All stable (Levene p={levene_p})")
    
    return "\n".join(lines) if lines else "(No statistical hypothesis results available.)"


def _build_statistical_findings_block(phase1: dict) -> str:
    """Extract statistical hypothesis results from phase1 and format as text block."""
    sh = phase1.get("statistical_hypothesis") or {}
    
    # If hypothesis testing was not run, return empty string
    if not sh or sh.get("status") == "not_requested":
        return "(Statistical hypothesis testing was not requested for this run.)"
    
    # If suppressed due to sample size, note that
    if sh.get("status") == "suppressed":
        return "(Statistical hypothesis testing was suppressed due to insufficient sample size.)"
    
    results = (sh.get("results") or {})
    inner = results.get("results") if isinstance(results.get("results"), dict) else results
    if not isinstance(inner, dict):
        return "(Statistical hypothesis results not available.)"
    
    findings_text = []
    
    # H-01: Detection rate floor
    h01 = (inner.get("h01") or {}).get("fault_detection_success_rate") or {}
    if h01:
        floor = (h01.get("wilson_lower") or 0.0) * 100.0
        findings_text.append(f"H-01: Detection rate certified floor is {floor:.1f}%")
    
    # H-02: Weakest per-category floor
    h02 = (inner.get("h02") or {}).get("fault_detection_success_rate") or {}
    per_cat_h02 = h02.get("per_category") or []
    if per_cat_h02:
        worst = min(per_cat_h02, key=lambda c: c.get("wilson_lower", 1.0))
        cat_label = (worst.get("category") or "").replace("_fault", "").title() or "—"
        floor = (worst.get("wilson_lower") or 0.0) * 100.0
        findings_text.append(f"H-02: Weakest per-category detection floor is {cat_label} at {floor:.1f}%")
    
    # H-03: Latency disparity
    h03_ttd = (inner.get("h03") or {}).get("time_to_detect") or {}
    if h03_ttd.get("omnibus_significant"):
        p = h03_ttd.get("omnibus_p", "unknown")
        findings_text.append(f"H-03: Detection latency differs significantly across categories (Kruskal-Wallis p={p})")
    
    # H-04: Cross-category uniformity
    h04 = (inner.get("h04") or {}).get("fault_detection_success_rate") or {}
    if h04:
        sig = h04.get("significant")
        chi = h04.get("statistic", "unknown")
        p = h04.get("p_value", "unknown")
        if sig:
            findings_text.append(f"H-04: Detection rates vary significantly across categories (χ²={chi}, p={p})")
        else:
            findings_text.append(f"H-04: Detection rates are uniform across categories (χ²={chi}, p={p})")
    
    # H-05: Variance stability
    h05 = (inner.get("h05") or {}).get("time_to_detect") or {}
    unstable = h05.get("unstable_categories") or []
    if unstable:
        names = ", ".join((c.replace("_fault", "").title() for c in unstable))
        p = h05.get("levene_p", "unknown")
        findings_text.append(f"H-05: Latency variance is unstable in {names} (Levene p={p})")
    
    return "\n".join(findings_text) if findings_text else "(No statistical findings available.)"


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

def _fallback_findings(phase2: dict) -> list[dict]:
    """Reformat raw Phase 2 findings as fallback."""
    items = []
    for f in phase2["findings"]:
        words = f["text"].split()
        headline = " ".join(words[:5])
        detail = " ".join(words[5:]) if len(words) > 5 else f["text"]
        items.append({
            "severity": f["severity"],
            "headline": headline[:60],
            "detail": detail,
        })
    # Trim to 7 max, pad severity mix isn't guaranteed but it's a fallback
    return items[:7]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_key_findings(phase1: dict, phase2: dict) -> dict:
    """
    Synthesize key findings from Phase 1 + Phase 2 data.

    Returns:
        {"key_findings": {"items": [...], "source": ..., "model": ..., "tokens_used": ...}}
    """
    context_block, template_vars = _build_findings_context(phase1, phase2)
    user_prompt = _CONFIG["user_prompt_template"].format(**template_vars)

    try:
        client = get_client()
        result = call_llm(
            client,
            _CONFIG["system_prompt"],
            user_prompt,
            response_schema=KeyFindingsResponse,
        )

        parsed = result["content"]  # already validated Pydantic model

        synthesis = KeyFindingsSynthesis(
            items=parsed.items,
            source="llm",
            model=result.get("model"),
            tokens_used=result.get("tokens_used", 0),
        )

    except Exception as exc:
        print(f"[phase3b] LLM call failed: {exc}")
        print("[phase3b] Using fallback findings.")
        synthesis = KeyFindingsSynthesis(
            items=_fallback_findings(phase2),
            source="fallback",
        )

    return {"key_findings": synthesis.model_dump(mode="json")}
