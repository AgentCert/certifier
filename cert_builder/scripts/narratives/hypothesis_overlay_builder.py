"""
Phase E — Hypothesis Overlay Builder.

Consumes ``ParsedContext.statistical_hypothesis`` (the block emitted by the
hypothesis framework when run with ``--advanced-analysis``) and produces the
extra content blocks needed in §5–§7 (inline strips after metric chart-pairs)
and §9–§10 (dedicated H-03..H-09 sections).

Two passes:

* Pass A — DETERMINISTIC skeleton. Pure-Python formatting/threshold rules.
  Emits ``HypothesisStripBlock`` dicts with verdict, metric_label, facts and
  method populated. ``summary`` is a fallback one-liner.

* Pass B — LLM findings. Calls Azure OpenAI once per strip to produce the
  ``summary`` (≤ 25 words) and ``findings`` (3–6 sentences) fields. The
  deterministic facts/verdict/method are passed in but never recomputed by
  the model. On any LLM failure the fallback summary is kept and ``findings``
  remains ``None``.

When ``statistical_hypothesis.status != "ok"`` the overlay is empty
(``suppressed=True``); the upstream §1 sample-size notice (Phase D) already
explains why.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from cert_builder.scripts.ingestion import hypothesis_view
from cert_builder.scripts.narratives.llm_client import call_llm, get_client


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "prompts"
    / "hypothesis_findings_prompt.yaml"
)
_PROMPT = yaml.safe_load(_PROMPT_PATH.read_text(encoding="utf-8"))

_STAT_FINDINGS_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "prompts"
    / "statistical_findings_prompt.yaml"
)
_STAT_FINDINGS_PROMPT = yaml.safe_load(
    _STAT_FINDINGS_PROMPT_PATH.read_text(encoding="utf-8")
)

# Wide-CI threshold (Phase 2 framework convention).
_WIDE_CI_RATIO = 0.25

# Pretty category labels for prose display.
_CATEGORY_LABELS = {
    "application_fault": "Application",
    "network_fault": "Network",
    "resource_fault": "Resource",
}

# Hypothesis metadata: title shown in strip + section, and the canonical
# method line (deterministic — never written by the LLM).
_HYP_META: dict[str, dict[str, str]] = {
    "H-01": {
        "name": "Confidence Intervals for Continuous Metrics",
        "method": "IQM (25% trimmed mean) + Bootstrap BCa 95% CI, B = 10,000.",
    },
    "H-02": {
        "name": "Success Rate Estimation with Safety Floor",
        "method": "Wilson score interval with continuity correction at α = 0.05.",
    },
    "H-03": {
        "name": "Cross-Category Latency Significance",
        "method": "Two-sided Mann-Whitney U with rank-biserial effect size.",
    },
    "H-04": {
        "name": "RAI Compliance",
        "method": "Wilson score interval with continuity correction at α = 0.05.",
    },
    "H-05": {
        "name": "Security Compliance",
        "method": "Wilson score interval with continuity correction at α = 0.05.",
    },
    "H-06": {
        "name": "SLA Threshold Compliance",
        "method": "Bootstrap of the per-run SLA-compliance fraction at α = 0.05.",
    },
    "H-07": {
        "name": "SLA Breach Rate",
        "method": "Bootstrap of the breach-rate proportion at α = 0.05.",
    },
    "H-08": {
        "name": "Tail-Risk Analysis",
        "method": "CVaR at the 95th percentile.",
    },
    "H-09": {
        "name": "Temporal Stability",
        "method": "CUSUM + EWMA change-point detection on the run sequence.",
    },
}


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass
class HypothesisOverlay:
    """Aggregate of all hypothesis-derived blocks produced from one run.

    The dict-valued ``inline_strips`` key is metric_id → list of strip blocks
    (each block is a JSON-serialisable dict matching ``HypothesisStripBlock``).
    The ``h0X_section_blocks`` lists hold the blocks for the dedicated §9/§10
    hypothesis sections (table + strip per H0X).
    """
    inline_strips: dict[str, list[dict]] = field(default_factory=dict)
    h03_section_blocks: list[dict] = field(default_factory=list)
    h04_section_blocks: list[dict] = field(default_factory=list)
    h05_section_blocks: list[dict] = field(default_factory=list)
    h06_section_blocks: list[dict] = field(default_factory=list)
    h07_section_blocks: list[dict] = field(default_factory=list)
    h08_section_blocks: list[dict] = field(default_factory=list)
    h09_section_blocks: list[dict] = field(default_factory=list)
    statistical_findings: list[dict] = field(default_factory=list)
    stat_limitations: list[dict] = field(default_factory=list)
    stat_recommendation: dict | None = None
    ground_truth_provided: bool = True  # True if GT was available for SLA tests
    suppressed: bool = False
    suppression_reason: str | None = None
    fallbacks_used: bool = False
    errors: list[str] = field(default_factory=list)


class _LLMFindings(BaseModel):
    """Structured response from the LLM findings call.

    Only ``findings`` is rendered in the report (under the STATISTICAL
    FINDINGS heading of each hypothesis strip). The deterministic
    ``summary`` placeholder set in the skeleton is never displayed, so we
    no longer ask the LLM to produce one.
    """
    findings: str = Field(..., min_length=1)


class _StatFindingItem(BaseModel):
    severity: str = Field(default="note", pattern="^(good|note|concern)$")
    text: str = Field(..., min_length=1, max_length=600)


class _StatLimitation(BaseModel):
    severity: str
    scope: str
    body: str
    tags: list[str] = Field(default_factory=lambda: ["Statistical Inference"])


class _StatRecommendation(BaseModel):
    severity: str
    scope: str
    body: str
    tags: list[str] = Field(default_factory=lambda: ["Statistical Inference"])


class _StatFindingsResp(BaseModel):
    findings: list[_StatFindingItem] = Field(..., min_length=1, max_length=8)
    stat_limitations: list[_StatLimitation] = Field(..., min_length=2, max_length=2)
    stat_recommendation: _StatRecommendation



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _category_label(name: str) -> str:
    return _CATEGORY_LABELS.get(name, name.replace("_", " ").title())


def _category_short(name: str) -> str:
    """Compact 3-letter category prefix used in H-04 contingency tables."""
    full = _category_label(name)
    return {"Application": "App", "Network": "Net", "Resource": "Res"}.get(
        full, full[:3]
    )


def _metric_label(metric_key: str) -> str:
    return {
        "time_to_detect": "Time-to-Detect",
        "time_to_mitigate": "Time-to-Mitigate",
        "tool_calls": "Tool Calls",
        "fault_detection_success_rate": "Fault Detection Success Rate",
        "fault_mitigation_success_rate": "Fault Mitigation Success Rate",
    }.get(metric_key, metric_key.replace("_", " ").title())


def _is_continuous_metric(metric_key: str) -> bool:
    return metric_key in {"time_to_detect", "time_to_mitigate", "tool_calls"}


def _round_int(x: float) -> int:
    return int(round(x))


def _fmt_seconds(value: float) -> str:
    return f"{_round_int(value)} s"


def _fmt_count(value: float) -> str:
    return f"{value:.1f}"


# ---------------------------------------------------------------------------
# Pass A — Deterministic skeleton builders (one per hypothesis)
# ---------------------------------------------------------------------------

def _h01_strip(metric_key: str, h01_metric: dict) -> dict | None:
    """H-01: per-category IQM + BCa CI; STABLE / WIDE-CI tag."""
    per_cat = h01_metric.get("per_category") or []
    if not per_cat:
        return None

    fmt_value = _fmt_seconds if metric_key != "tool_calls" else _fmt_count
    facts: list[dict] = []
    any_wide = False
    for cat in per_cat:
        iqm = cat.get("iqm")
        lo = cat.get("ci_lower")
        hi = cat.get("ci_upper")
        if iqm is None or lo is None or hi is None:
            continue
        half_width = (hi - lo) / 2.0
        ratio = (half_width / iqm) if iqm > 0 else float("inf")
        stable = ratio < _WIDE_CI_RATIO
        any_wide = any_wide or (not stable)
        tag = "STABLE" if stable else "WIDE-CI"
        tone = "good" if stable else "flag"
        facts.append({
            "label": _category_label(cat["category"]),
            "text": (
                f"IQM {fmt_value(iqm)}, CI [{fmt_value(lo)}, {fmt_value(hi)}] "
                f"(±{_round_int(ratio * 100)}% — {tag})"
            ),
            "tone": tone,
        })

    if not facts:
        return None

    verdict = "flag" if any_wide else "pass"
    metric_label = _metric_label(metric_key)
    return {
        "type": "hypothesis_strip",
        "verdict": verdict,
        "hypothesis_id": "H-01",
        "metric_label": metric_label,
        "facts": facts,
        "method": _HYP_META["H-01"]["method"],
        "summary": f"H-01 {metric_label} — verdict: {verdict}.",
    }


def _h02_strip(metric_key: str, h02_metric: dict) -> dict | None:
    """H-02: per-category success rate with Wilson lower (certified floor)."""
    per_cat = h02_metric.get("per_category") or []
    if not per_cat:
        return None

    facts: list[dict] = []
    floors: list[float] = []
    for cat in per_cat:
        rate = cat.get("rate")
        wlow = cat.get("wilson_lower")
        whigh = cat.get("wilson_upper")
        successes = cat.get("successes")
        trials = cat.get("trials")
        if rate is None or wlow is None or trials is None:
            continue
        floors.append(wlow)
        # Tone heuristic: a >= 90% certified floor is "good", < 70% is "flag",
        # otherwise "warn".
        if wlow >= 0.90:
            tone = "good"
        elif wlow < 0.70:
            tone = "flag"
        else:
            tone = "warn"
        facts.append({
            "label": _category_label(cat["category"]),
            "text": (
                f"{rate * 100:.1f}% ({successes}/{trials}), "
                f"Wilson 95% CI [{wlow * 100:.1f}%, {whigh * 100:.1f}%]"
            ),
            "tone": tone,
        })

    if not facts:
        return None

    worst = min(floors) if floors else 0.0
    if worst >= 0.90:
        verdict = "pass"
    elif worst < 0.70:
        verdict = "flag"
    else:
        verdict = "flag" if worst < 0.80 else "pass"

    metric_label = _metric_label(metric_key)
    return {
        "type": "hypothesis_strip",
        "verdict": verdict,
        "hypothesis_id": "H-02",
        "metric_label": metric_label,
        "facts": facts,
        "method": _HYP_META["H-02"]["method"],
        "summary": f"H-02 {metric_label} — verdict: {verdict}.",
    }


def _generic_strip(
    hypothesis_id: str,
    metric_key: str,
    metric_block: dict,
) -> dict | None:
    """Minimal strip used for H-03..H-09: verdict + assessment summary fact."""
    if not isinstance(metric_block, dict) or metric_block.get("status") == "skipped":
        return None
    verdict_text = (
        metric_block.get("overall_assessment")
        or metric_block.get("verdict")
        or metric_block.get("status")
        or "result available"
    )
    if str(verdict_text).lower() in {"insufficient_groups", "insufficient_data", "no_data"}:
        verdict = "inconclusive"
    elif "significant" in str(verdict_text).lower() or "flag" in str(verdict_text).lower():
        verdict = "flag"
    else:
        verdict = "pass"

    metric_label = _metric_label(metric_key)
    facts = [{
        "label": metric_label,
        "text": str(verdict_text),
        "tone": {"pass": "good", "flag": "flag", "inconclusive": "warn"}[verdict],
    }]
    return {
        "type": "hypothesis_strip",
        "verdict": verdict,
        "hypothesis_id": hypothesis_id,
        "metric_label": metric_label,
        "facts": facts,
        "method": _HYP_META.get(hypothesis_id, {}).get("method"),
        "summary": f"{hypothesis_id} {metric_label} — verdict: {verdict}.",
    }


# ---------------------------------------------------------------------------
# Deterministic detail tables (H-03..H-09)
# ---------------------------------------------------------------------------

def _heading_block(title: str, detail: str | None = None) -> dict:
    block = {"type": "heading", "title": title}
    if detail:
        block["detail"] = detail
    return block


def _table_block(headers: list, rows: list, title: str | None = None) -> dict:
    block = {"type": "table", "headers": headers, "rows": rows}
    if title:
        block["title"] = title
    return block


def _fmt_p(p: Any) -> str:
    if p is None:
        return "—"
    try:
        pv = float(p)
    except Exception:
        return str(p)
    if pv < 0.001:
        return "< 0.001"
    return f"{pv:.4f}"


def _fmt_num(x: Any, digits: int = 2) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):.{digits}f}"
    except Exception:
        return str(x)


def _fmt_pct(x: Any, digits: int = 1) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x) * 100:.{digits}f}%"
    except Exception:
        return str(x)


def _h03_table(metric_key: str, h03: dict) -> dict | None:
    """H-03: omnibus row + pairwise rows. One table per metric."""
    if not h03 or h03.get("status") == "skipped":
        return None
    metric_label = _metric_label(metric_key)
    test = h03.get("test_used", "kruskal_wallis").replace("_", " ").title()
    rows: list[list[Any]] = []
    rows.append([
        f"{test} (omnibus)",
        _fmt_num(h03.get("omnibus_statistic"), 3),
        _fmt_p(h03.get("omnibus_p")),
        "—",
        "Significant" if h03.get("omnibus_significant") else "Not significant",
    ])
    for pw in h03.get("pairwise") or []:
        rows.append([
            f"Mann–Whitney U: {pw.get('pair', '')}",
            _fmt_num(pw.get("u_statistic"), 1),
            _fmt_p(pw.get("p_value_adjusted") or pw.get("p_value_raw")),
            f"A12 = {_fmt_num(pw.get('a12'), 3)} ({pw.get('effect_magnitude','')})",
            "Significant" if pw.get("significant") else "Not significant",
        ])
    return _table_block(
        headers=["Test", "Statistic", "p-value", "Effect Size", "Interpretation"],
        rows=rows,
        title=f"H-03 — {metric_label} cross-category comparison",
    )


def _h04_table(metric_key: str, h04: dict) -> dict | None:
    """H-04: per-category contingency rates + Chi-Square omnibus row."""
    if not h04 or h04.get("status") == "skipped":
        return None
    metric_label = _metric_label(metric_key)
    rows: list[list[Any]] = []
    for cat in h04.get("per_category") or []:
        rows.append([
            _category_label(cat.get("category", "")),
            f"{cat.get('successes', 0)}/{cat.get('trials', 0)}",
            _fmt_pct(cat.get("rate")),
            "—",
            "—",
        ])
    rows.append([
        "Chi-square (omnibus)",
        "—",
        "—",
        _fmt_num(h04.get("statistic"), 3),
        _fmt_p(h04.get("p_value")),
    ])
    return _table_block(
        headers=["Category", "Successes / Trials", "Rate", "χ² statistic", "p-value"],
        rows=rows,
        title=f"H-04 — {metric_label} cross-category uniformity",
    )


def _h05_table(metric_key: str, h05: dict) -> dict | None:
    """H-05: per-category CV + Levene omnibus."""
    if not h05 or h05.get("status") == "skipped":
        return None
    metric_label = _metric_label(metric_key)
    rows: list[list[Any]] = []
    for cat in h05.get("per_category") or []:
        rows.append([
            _category_label(cat.get("category", "")),
            _fmt_num(cat.get("pooled_mean"), 2),
            _fmt_num(cat.get("pooled_std"), 2),
            _fmt_num(cat.get("pooled_cv"), 4),
            (cat.get("cv_flag") or "").replace("_", " ").title(),
        ])
    rows.append([
        "Levene (omnibus)",
        "—",
        "—",
        _fmt_num(h05.get("levene_statistic"), 3),
        _fmt_p(h05.get("levene_p")),
    ])
    return _table_block(
        headers=["Category", "Mean", "Std Dev", "CV", "Stability"],
        rows=rows,
        title=f"H-05 — {metric_label} variance / consistency",
    )


def _h06_combined_table(metric_results: list[tuple[str, dict]]) -> dict | None:
    """H-06 combined table covering TTD + TTM SLA compliance."""
    rows: list[list[Any]] = []
    for metric_key, h06 in metric_results:
        if not isinstance(h06, dict) or h06.get("status") == "skipped":
            continue
        m_label = _metric_label(metric_key)
        for cat in h06.get("per_category") or []:
            cat_label = _category_label(cat.get("category", ""))
            rows.append([
                cat_label,
                m_label,
                f"≤ {_fmt_num(h06.get('sla_threshold'), 1)} s" if h06.get('sla_threshold') else "—",
                _fmt_num(cat.get("wilcoxon_w"), 1),
                _fmt_p(cat.get("wilcoxon_p")),
                f"[{_fmt_num(cat.get('ci_lower'), 1)}, {_fmt_num(cat.get('ci_upper'), 1)}]" if cat.get('ci_lower') is not None else "—",
                "within SLA" if cat.get("tost_equivalent") else "inconclusive" if cat.get("inconclusive") else "rejected",
            ])
    if not rows:
        return None
    return _table_block(
        headers=[
            "Fault Category", "Metric", "SLA Threshold",
            "Wilcoxon W", "P-value", "BCA 95% CI vs SLA", "TOST Equivalence",
        ],
        rows=rows,
        title=(
            "H-06 — SLA Threshold Compliance (Wilcoxon signed-rank one-sample "
            "+ Bootstrap BCa CI + TOST equivalence)"
        ),
    )


def _h07_combined_table(metric_results: list[tuple[str, dict]]) -> dict | None:
    """H-07 combined table covering TTD + TTM breach rates."""
    rows: list[list[Any]] = []
    for metric_key, h07 in metric_results:
        if not isinstance(h07, dict) or h07.get("status") == "skipped":
            continue
        m_label = _metric_label(metric_key)
        target = h07.get("target_rate", 0.05)
        for cat in h07.get("per_category") or []:
            cat_label = _category_label(cat.get("category", ""))
            obs_rate = cat.get("observed_rate")
            breaches = cat.get("breaches", 0)
            trials = cat.get("trials", 0)
            budget_used = f"{(obs_rate / target * 100.0):.0f}%" if (obs_rate is not None and target) else "—"
            rows.append([
                cat_label,
                m_label,
                f"{breaches} / {trials}",
                _fmt_pct(obs_rate),
                f"[{_fmt_pct(cat.get('ci_lower'))}, {_fmt_pct(cat.get('ci_upper'))}]",
                _fmt_p(cat.get("binomial_p")),
                budget_used,
            ])
    if not rows:
        return None
    return _table_block(
        headers=[
            "Fault Category", "Metric", "Breaches / N", "Breach Rate",
            "95% Wilson CI", "Exact Binomial P (H₀: rate ≤ 5%)", "Error Budget Used",
        ],
        rows=rows,
        title=(
            "H-07 — SLA Breach Rate (Exact Binomial (Clopper-Pearson) on "
            "observed breaches vs allowed budget of 5%)"
        ),
    )


def _h08_combined_table(metric_results: list[tuple[str, dict]]) -> dict | None:
    """H-08 combined table covering TTD + TTM tail-risk (CVaR)."""
    rows: list[list[Any]] = []
    for metric_key, h08 in metric_results:
        if not isinstance(h08, dict) or h08.get("status") == "skipped":
            continue
        m_label = _metric_label(metric_key)
        for cat in h08.get("per_category") or []:
            cat_label = _category_label(cat.get("category", ""))
            rows.append([
                cat_label,
                m_label,
                _fmt_num(cat.get("iqm"), 1),
                _fmt_num(cat.get("p95"), 1),
                _fmt_num(cat.get("cvar"), 1),
                _fmt_num(cat.get("cvar_iqm_ratio"), 2),
                (cat.get("risk_level") or "").title(),
            ])
    if not rows:
        return None
    return _table_block(
        headers=[
            "Fault Category", "Metric", "IQM", "P95", "CVaR₉₅",
            "CVaR/IQM Ratio", "Risk Level",
        ],
        rows=rows,
        title=(
            "H-08 — Tail-Risk Analysis (CVaR₉₅ + CVaR/IQM ratio)"
        ),
    )


def _h09_combined_table(metric_results: list[tuple[str, dict]]) -> dict | None:
    """H-09 combined table covering TTD + TTM temporal stability."""
    rows: list[list[Any]] = []
    for metric_key, h09 in metric_results:
        if not isinstance(h09, dict) or h09.get("status") == "skipped":
            continue
        m_label = _metric_label(metric_key)
        for cat in h09.get("per_category") or []:
            cat_label = _category_label(cat.get("category", ""))
            cusum_alarm = cat.get("cusum_alarm")
            ewma_trend = cat.get("ewma_trend", "flat").title()
            ewma_alarm = cat.get("ewma_alarm")
            rows.append([
                cat_label,
                m_label,
                "Yes" if cusum_alarm else "No (max [S] ≤ 1.2)",
                ewma_trend,
                "Yes" if ewma_alarm else "No",
                cat.get("verdict", "STABLE"),
            ])
    if not rows:
        return None
    return _table_block(
        headers=[
            "Fault Category", "Metric", "CUSUM Alarm",
            "EWMA Trend (Δ = 0.2)", "EWMA Alarm", "Verdict",
        ],
        rows=rows,
        title=(
            "H-09 — Temporal Stability (CUSUM (threshold h ≈ 0.2) "
            "+ EWMA smoothing Δ = 0.2)"
        ),
    )


_TABLE_BUILDERS = {
    "h03": _h03_table,
    "h04": _h04_table,
    "h05": _h05_table,
    "h06_combined": _h06_combined_table,
    "h07_combined": _h07_combined_table,
    "h08_combined": _h08_combined_table,
    "h09_combined": _h09_combined_table,
}


# ---------------------------------------------------------------------------
# §9 combined builders (H-03 / H-04 / H-05) — single table + strip per
# hypothesis covering both relevant metrics.
# ---------------------------------------------------------------------------

# Metrics included in §9 combined tables. tool_calls is intentionally excluded.
_SECTION9_LATENCY_METRICS = ("time_to_detect", "time_to_mitigate")
_SECTION9_RATE_METRICS = (
    "fault_detection_success_rate",
    "fault_mitigation_success_rate",
)


def _h03_combined_table(metric_results: list[tuple[str, dict]]) -> dict | None:
    """H-03 combined table covering TTD + TTM with a Metric column."""
    rows: list[list[Any]] = []
    for metric_key, h03 in metric_results:
        if not isinstance(h03, dict) or h03.get("status") == "skipped":
            continue
        m_label = _metric_label(metric_key)
        n_groups = len(h03.get("per_category") or []) or 3
        df = max(n_groups - 1, 1)
        rows.append([
            m_label,
            f"Kruskal-Wallis ({n_groups} groups, df={df})",
            f"H = {_fmt_num(h03.get('omnibus_statistic'), 2)}",
            _fmt_p(h03.get("omnibus_p")),
            "—",
            "Significant" if h03.get("omnibus_significant") else "Not significant",
        ])
        for pw in h03.get("pairwise") or []:
            pair_raw = pw.get("pair", "")
            parts = [p.strip() for p in pair_raw.split("vs")]
            if len(parts) == 2:
                pair_label = (
                    f"{_category_label(parts[0])} vs "
                    f"{_category_label(parts[1])} (MW-U)"
                )
            else:
                pair_label = f"{pair_raw} (MW-U)"
            p_adj = pw.get("p_value_adjusted")
            p_disp = (
                f"{_fmt_p(p_adj)} (Holm)" if p_adj is not None
                else _fmt_p(pw.get("p_value_raw"))
            )
            rows.append([
                m_label,
                pair_label,
                f"U = {_fmt_num(pw.get('u_statistic'), 1)}",
                p_disp,
                f"A₁₂ = {_fmt_num(pw.get('a12'), 2)} ({pw.get('effect_magnitude','')})",
                "Significant" if pw.get("significant") else "Not significant",
            ])
    if not rows:
        return None
    return _table_block(
        headers=["Metric", "Test", "Statistic", "p-value", "Effect Size", "Interpretation"],
        rows=rows,
        title=(
            "H-03 — Time-to-Detect & Time-to-Mitigate across categories "
            "(Kruskal\u2013Wallis + pairwise Mann\u2013Whitney U with "
            "Holm-Bonferroni, Vargha-Delaney A\u2081\u2082 effect size)"
        ),
    )


def _h04_combined_table(metric_results: list[tuple[str, dict]]) -> dict | None:
    """H-04 combined table covering detection + mitigation rates.

    Mirrors the framework HTML format: one row per metric showing the
    contingency table inline plus the χ² omnibus statistic and p-value.
    """
    rows: list[list[Any]] = []
    for metric_key, h04 in metric_results:
        if not isinstance(h04, dict) or h04.get("status") == "skipped":
            continue
        m_label = _metric_label(metric_key)
        per_cat = h04.get("per_category") or []
        n_groups = len(per_cat) or 3
        df = max(n_groups - 1, 1)
        # Build inline contingency-table string: "App[52/60], Net[88/120], …"
        if per_cat:
            contingency_str = ", ".join(
                f"{_category_short(cat.get('category', ''))}"
                f"[{cat.get('successes', 0)}/{cat.get('trials', 0)}]"
                for cat in per_cat
            )
        else:
            contingency_str = "—"
        chi_stat = h04.get("statistic")
        chi_disp = (
            f"\u03c7\u00b2 = {_fmt_num(chi_stat, 2)} (df = {df})"
            if chi_stat is not None else "—"
        )
        rows.append([
            m_label,
            f"Chi-Square ({n_groups}\u00d73)",
            contingency_str,
            chi_disp,
            _fmt_p(h04.get("p_value")),
        ])
    if not rows:
        return None
    return _table_block(
        headers=[
            "Metric", "Test", "Contingency Table",
            "\u03c7\u00b2 statistic", "p-value",
        ],
        rows=rows,
        title=(
            "H-04 — Detection-rate & Mitigation-rate uniformity across "
            "categories (Chi-Square test on contingency table; Fisher's "
            "Exact fallback)"
        ),
    )


def _h05_combined_table(metric_results: list[tuple[str, dict]]) -> dict | None:
    """H-05 combined table covering TTD + TTM variance / CV.

    Mirrors the framework HTML format: a Levene omnibus row per metric
    followed by per-category CV rows tagged with stability interpretation.
    Columns: Test / Category | Metric | Statistic | p-value / CV |
    Interpretation.
    """
    rows: list[list[Any]] = []
    for metric_key, h05 in metric_results:
        if not isinstance(h05, dict) or h05.get("status") == "skipped":
            continue
        m_label = _metric_label(metric_key)
        per_cat = h05.get("per_category") or []
        n_groups = len(per_cat) or 3
        # Levene omnibus row
        levene_stat = h05.get("levene_statistic")
        levene_p = h05.get("levene_p")
        levene_sig = h05.get("levene_significant")
        if levene_sig is None and isinstance(levene_p, (int, float)):
            levene_sig = levene_p < 0.05
        rows.append([
            f"Levene's test ({n_groups} groups)",
            m_label,
            f"W = {_fmt_num(levene_stat, 2)}" if levene_stat is not None else "—",
            f"p = {_fmt_p(levene_p)}" if levene_p is not None else "—",
            ("Variances differ significantly" if levene_sig
             else "Variances homogeneous"),
        ])
        # Per-category CV rows
        for cat in per_cat:
            cv_val = cat.get("pooled_cv")
            flag = (cat.get("cv_flag") or "").lower()
            if "stable" in flag and "un" not in flag:
                interp = "Stable (low variability)"
            elif "moderate" in flag:
                interp = "Moderate variability"
            elif "unstable" in flag or "high" in flag:
                interp = "Unstable (high variability)"
            else:
                interp = (cat.get("cv_flag") or "").replace("_", " ").title() or "—"
            rows.append([
                _category_label(cat.get("category", "")),
                m_label,
                "\u03c3 / \u03bc",
                f"CV = {_fmt_num(cv_val, 2)}" if cv_val is not None else "—",
                interp,
            ])
    if not rows:
        return None
    return _table_block(
        headers=[
            "Test / Category", "Metric", "Statistic",
            "p-value / CV", "Interpretation",
        ],
        rows=rows,
        title=(
            "H-05 — Time-to-Detect & Time-to-Mitigate variance homogeneity "
            "& per-category stability (Levene's Test + Coefficient of "
            "Variation)"
        ),
    )


def _combined_section9_strip(
    hyp_id: str,
    combined_label: str,
    sub_strips: list[tuple[str, dict | None]],
) -> dict | None:
    """Merge per-metric strips for H-03 / H-04 / H-05 into one strip.

    Facts from each input strip are prefixed with the metric label so the
    chips remain self-describing. Verdict is the worst across inputs.
    """
    valid = [(m, s) for m, s in sub_strips if s is not None]
    if not valid:
        return None
    facts: list[dict] = []
    for metric_label, strip in valid:
        for f in strip.get("facts") or []:
            facts.append({
                "label": metric_label,
                "text": f.get("text", ""),
                "tone": f.get("tone", "good"),
            })
    rank = {"flag": 2, "inconclusive": 1, "pass": 0}
    verdict = max(
        (s.get("verdict", "pass") for _, s in valid),
        key=lambda v: rank.get(v, 0),
    )
    method = valid[0][1].get("method") or _HYP_META.get(hyp_id, {}).get("method")
    return {
        "type": "hypothesis_strip",
        "verdict": verdict,
        "hypothesis_id": hyp_id,
        "metric_label": combined_label,
        "facts": facts,
        "method": method,
        "summary": f"{hyp_id} {combined_label} — verdict: {verdict}.",
    }


# ---------------------------------------------------------------------------
# Skeleton orchestration (Pass A)
# ---------------------------------------------------------------------------

def build_overlay_skeleton(ctx: Any) -> HypothesisOverlay:
    """Pass A: build all strip skeletons deterministically.

    No LLM calls. Safe to run with no network.
    """
    overlay = HypothesisOverlay()

    if hypothesis_view.is_not_requested(ctx):
        overlay.suppressed = True
        overlay.suppression_reason = "not_requested"
        return overlay
    if hypothesis_view.is_skipped(ctx):
        overlay.suppressed = True
        overlay.suppression_reason = (
            hypothesis_view.skip_reason(ctx) or "skipped"
        )
        return overlay

    results = hypothesis_view.results(ctx) or {}
    
    # Extract ground_truth_provided flag if available
    # (set by run_full_certification_pipeline if GT directory was found/missing)
    hyp_block = getattr(ctx, "statistical_hypothesis", {}) or {}
    overlay.ground_truth_provided = hyp_block.get("ground_truth_provided", True)

    # ── H-01 (continuous metrics → §5 inline strips)
    for metric_key, h01_metric in (results.get("h01") or {}).items():
        if not isinstance(h01_metric, dict):
            continue
        strip = _h01_strip(metric_key, h01_metric)
        if strip is not None:
            overlay.inline_strips.setdefault(metric_key, []).append(strip)

    # ── H-02 (rate metrics → §6 inline strips)
    for metric_key, h02_metric in (results.get("h02") or {}).items():
        if not isinstance(h02_metric, dict):
            continue
        strip = _h02_strip(metric_key, h02_metric)
        if strip is not None:
            overlay.inline_strips.setdefault(metric_key, []).append(strip)

    # ── H-03 / H-04 / H-05 (§9) — ONE combined table + strip per
    #     hypothesis covering both relevant metrics. tool_calls is excluded.
    _section9_specs = [
        (
            "h03", "H-03", "h03_section_blocks",
            _SECTION9_LATENCY_METRICS,
            _h03_combined_table,
            "H-03 — Time-to-Detect & Time-to-Mitigate",
            "Cross-Category Latency Significance",
            "Time-to-Detect & Time-to-Mitigate",
        ),
        (
            "h04", "H-04", "h04_section_blocks",
            _SECTION9_RATE_METRICS,
            _h04_combined_table,
            "H-04 — Detection & Mitigation Rates",
            "Cross-Category Detection & Mitigation Uniformity",
            "Detection & Mitigation Rates",
        ),
        (
            "h05", "H-05", "h05_section_blocks",
            _SECTION9_LATENCY_METRICS,
            _h05_combined_table,
            "H-05 — Time-to-Detect & Time-to-Mitigate",
            "Variance Homogeneity & Stability",
            "Time-to-Detect & Time-to-Mitigate",
        ),
    ]
    for (hyp_key, hyp_id, target_attr, metric_keys, table_builder,
         heading_title, heading_detail, combined_label) in _section9_specs:
        block_list = getattr(overlay, target_attr)
        hyp_results = results.get(hyp_key) or {}
        metric_results = [(mk, hyp_results.get(mk) or {}) for mk in metric_keys]
        # Build one strip per metric, then merge.
        sub_strips = [
            (_metric_label(mk), _generic_strip(hyp_id, mk, mb))
            for mk, mb in metric_results
        ]
        combined_strip = _combined_section9_strip(
            hyp_id, combined_label, sub_strips,
        )
        if combined_strip is None:
            continue
        # No separate heading block — the table title serves as the heading.
        tbl = table_builder(metric_results)
        if tbl is not None:
            block_list.append(tbl)
        block_list.append(combined_strip)

    # ── H-06..H-09 (§10) — combined TTD + TTM tables (no tool_calls).
    _SECTION10_SPECS = [
        (
            "h06", "H-06", "h06_section_blocks",
            _SECTION9_LATENCY_METRICS,
            _h06_combined_table,
            "SLA Threshold Compliance",
        ),
        (
            "h07", "H-07", "h07_section_blocks",
            _SECTION9_LATENCY_METRICS,
            _h07_combined_table,
            "SLA Breach Rate",
        ),
        (
            "h08", "H-08", "h08_section_blocks",
            _SECTION9_LATENCY_METRICS,
            _h08_combined_table,
            "Tail-Risk Analysis",
        ),
        (
            "h09", "H-09", "h09_section_blocks",
            _SECTION9_LATENCY_METRICS,
            _h09_combined_table,
            "Temporal Stability",
        ),
    ]
    for (hyp_key, hyp_id, target_attr, metric_keys, table_builder,
         detail_label) in _SECTION10_SPECS:
        block_list = getattr(overlay, target_attr)
        hyp_results = results.get(hyp_key) or {}
        metric_results = [(mk, hyp_results.get(mk) or {}) for mk in metric_keys]
        # Build one strip per metric, then merge.
        sub_strips = [
            (_metric_label(mk), _generic_strip(hyp_id, mk, mb))
            for mk, mb in metric_results
        ]
        combined_strip = _combined_section9_strip(
            hyp_id, detail_label, sub_strips,
        )
        if combined_strip is None:
            continue
        tbl = table_builder(metric_results)
        if tbl is not None:
            block_list.append(tbl)
        block_list.append(combined_strip)

    return overlay


# ---------------------------------------------------------------------------
# Pass B — LLM findings
# ---------------------------------------------------------------------------

def _format_facts_block(facts: list[dict]) -> str:
    lines = []
    for f in facts:
        lines.append(f"  - [{f['tone']:>4s}] {f['label']}: {f['text']}")
    return "\n".join(lines)


def _llm_one_strip(client, strip: dict) -> tuple[str | None, dict | None]:
    """Call the LLM for one strip; return (findings, error).

    Only the ``findings`` prose is requested from the LLM — the
    deterministic skeleton already supplies a placeholder ``summary`` and
    that field is never rendered.
    """
    hyp_id = strip.get("hypothesis_id", "?")
    meta = _HYP_META.get(hyp_id, {})
    try:
        user_prompt = _PROMPT["user_prompt_template"].format(
            hypothesis_id=hyp_id,
            hypothesis_name=meta.get("name", hyp_id),
            metric_label=strip.get("metric_label") or "",
            verdict=strip.get("verdict", ""),
            method=strip.get("method") or meta.get("method", ""),
            facts_block=_format_facts_block(strip.get("facts") or []),
        )
        result = call_llm(
            client,
            _PROMPT["system_prompt"],
            user_prompt,
            response_schema=_LLMFindings,
            temperature=0.3,
            max_tokens=600,
        )
        parsed: _LLMFindings = result["content"]
        return parsed.findings.strip(), None
    except Exception as exc:  # noqa: BLE001 — fallback path
        err = {
            "phase": "hypothesis_overlay",
            "hypothesis_id": hyp_id,
            "metric_label": strip.get("metric_label"),
            "error": str(exc),
        }
        return None, err


async def _enrich_strips_with_llm(overlay: HypothesisOverlay) -> HypothesisOverlay:
    """Run the LLM findings pass over every strip in the overlay.

    The H-03..H-09 ``*_section_blocks`` lists contain a mix of headings,
    tables, and strips — we filter to ``hypothesis_strip`` blocks only
    before dispatching to the LLM.
    """
    # Collect every strip into a flat list for concurrent processing.
    all_strips: list[dict] = []
    for strips in overlay.inline_strips.values():
        all_strips.extend(
            s for s in strips
            if isinstance(s, dict) and s.get("type") == "hypothesis_strip"
        )
    for attr in (
        "h03_section_blocks", "h04_section_blocks", "h05_section_blocks",
        "h06_section_blocks", "h07_section_blocks", "h08_section_blocks",
        "h09_section_blocks",
    ):
        all_strips.extend(
            b for b in getattr(overlay, attr)
            if isinstance(b, dict) and b.get("type") == "hypothesis_strip"
        )

    if not all_strips:
        return overlay

    client = get_client()

    async def _one(strip):
        return await asyncio.to_thread(_llm_one_strip, client, strip)

    outcomes = await asyncio.gather(*[_one(s) for s in all_strips], return_exceptions=True)

    for strip, outcome in zip(all_strips, outcomes):
        if isinstance(outcome, BaseException):
            overlay.fallbacks_used = True
            overlay.errors.append(
                f"{strip.get('hypothesis_id', '?')} "
                f"{strip.get('metric_label', '')}: {outcome}"
            )
            continue
        findings, err = outcome
        if findings is not None:
            strip["findings"] = findings
        if err is not None:
            overlay.fallbacks_used = True
            overlay.errors.append(err.get("error", "unknown"))

    return overlay


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _stat_block_h01(inner: dict) -> str:
    h01 = inner.get("h01") or {}
    lines = []
    for metric in ("time_to_detect", "time_to_mitigate", "tool_calls"):
        rec = h01.get(metric) or {}
        per = rec.get("per_category") or []
        if not per:
            continue
        lines.append(f"  {metric}:")
        for c in per:
            iqm = c.get("iqm")
            if iqm is None:
                lines.append(f"    - {c.get('category','?')}: n/a")
                continue
            lines.append(
                f"    - {c.get('category','?')}: IQM={iqm:.2f}, "
                f"BCa CI=[{c.get('ci_lower', 0):.2f}, "
                f"{c.get('ci_upper', 0):.2f}], p95={c.get('p95', 0):.2f}"
            )
    return "\n".join(lines) or "  (no data)"


def _stat_block_h02(inner: dict) -> str:
    h02 = inner.get("h02") or {}
    lines = []
    for metric in ("fault_detection_success_rate", "fault_mitigation_success_rate"):
        rec = h02.get(metric) or {}
        per = rec.get("per_category") or []
        if not per:
            continue
        lines.append(f"  {metric}:")
        for c in per:
            lines.append(
                f"    - {c.get('category','?')}: rate={c.get('rate', 0)*100:.1f}%, "
                f"Wilson 95% CI=[{c.get('wilson_lower', 0)*100:.1f}%, "
                f"{c.get('wilson_upper', 0)*100:.1f}%], "
                f"floor={c.get('certified_floor', 0)*100:.1f}%"
            )
    return "\n".join(lines) or "  (no data)"


def _stat_block_h03(inner: dict) -> str:
    h03 = inner.get("h03") or {}
    lines = []
    for metric, rec in h03.items():
        if not isinstance(rec, dict):
            continue
        p = rec.get("omnibus_p")
        sig = rec.get("omnibus_significant")
        lines.append(
            f"  {metric}: Kruskal-Wallis p={_p_str(p)}, "
            f"omnibus_significant={sig}"
        )
    return "\n".join(lines) or "  (no data)"


def _stat_block_h04(inner: dict) -> str:
    h04 = inner.get("h04") or {}
    lines = []
    for metric, rec in h04.items():
        if not isinstance(rec, dict):
            continue
        stat = rec.get("statistic")
        if stat is None:
            lines.append(f"  {metric}: n/a")
            continue
        lines.append(
            f"  {metric}: chi2={stat:.2f}, "
            f"p={_p_str(rec.get('p_value'))}, "
            f"significant={rec.get('significant')}, "
            f"weakest={rec.get('weakest_category')}"
        )
    return "\n".join(lines) or "  (no data)"


def _stat_block_h05(inner: dict) -> str:
    h05 = inner.get("h05") or {}
    lines = []
    for metric, rec in h05.items():
        if not isinstance(rec, dict):
            continue
        cv = rec.get("cv_per_category") or {}
        cv_str = ", ".join(f"{k}={v:.2f}" for k, v in cv.items())
        lines.append(
            f"  {metric}: Levene p={_p_str(rec.get('levene_p'))}, "
            f"unstable={rec.get('unstable_categories') or []}, CV: {cv_str}"
        )
    return "\n".join(lines) or "  (no data)"


def _stat_block_generic(inner: dict, key: str) -> str:
    h = inner.get(key) or {}
    if not h:
        return "  (not configured / no data)"
    lines = []
    for metric, rec in h.items():
        if not isinstance(rec, dict):
            continue
        # Compact summary: list a few interesting top-level keys.
        keep = {k: v for k, v in rec.items()
                if k in ("overall_assessment", "verdict", "p_value",
                          "compliance_rate", "breach_rate",
                          "cvar_iqm_ratio", "alarms")
                and not isinstance(v, (dict, list))}
        if keep:
            kvs = ", ".join(f"{k}={v}" for k, v in keep.items())
            lines.append(f"  {metric}: {kvs}")
    return "\n".join(lines) or "  (no scalar summary)"


def _p_str(p):
    if p is None:
        return "—"
    try:
        v = float(p)
    except Exception:
        return str(p)
    return "< 0.001" if v < 0.001 else f"{v:.3f}"


def _build_stat_findings_facts(ctx: Any, inner: dict) -> dict:
    """Extract template fields for the §3.3 statistical-findings prompt."""
    meta = getattr(ctx, "meta", None) or (ctx.get("meta") if isinstance(ctx, dict) else {}) or {}
    cats = getattr(ctx, "categories", None)
    if cats is None and isinstance(ctx, dict):
        cats = ctx.get("categories") or []
    cats = cats or []

    cat_list = ", ".join(
        f"{c.get('label', c.get('fault_category', '?'))}({c.get('total_runs', 0)})"
        for c in cats
    )

    return {
        "agent_name": meta.get("agent_name", "agent"),
        "total_runs": meta.get("total_runs", 0),
        "category_list": cat_list,
        "h01_block": _stat_block_h01(inner),
        "h02_block": _stat_block_h02(inner),
        "h03_block": _stat_block_h03(inner),
        "h04_block": _stat_block_h04(inner),
        "h05_block": _stat_block_h05(inner),
        "h06_block": _stat_block_generic(inner, "h06"),
        "h07_block": _stat_block_generic(inner, "h07"),
        "h08_block": _stat_block_generic(inner, "h08"),
        "h09_block": _stat_block_generic(inner, "h09"),
    }


def _llm_statistical_findings(client, ctx: Any) -> tuple[list[dict], list[dict], dict | None, dict | None]:
    """Call the LLM once to synthesize §3.3 statistical findings, limitations, and recommendations.

    Returns (findings_list, limitations_list, recommendation_dict, error_dict).
    On success, all items populated; on failure, returns ([], [], None, err).
    If LLM call fails, caller should fallback to all-Council items.
    """
    sh = getattr(ctx, "statistical_hypothesis", None)
    if sh is None and isinstance(ctx, dict):
        sh = ctx.get("statistical_hypothesis")
    if not sh or sh.get("status") != "ok":
        return [], [], None, None

    outer = sh.get("results") or {}
    inner = outer.get("results") if isinstance(outer.get("results"), dict) else outer
    if not isinstance(inner, dict):
        return [], [], None, None

    facts = _build_stat_findings_facts(ctx, inner)
    user_prompt = _STAT_FINDINGS_PROMPT["user_prompt_template"].format(**facts)

    try:
        result = call_llm(
            client,
            _STAT_FINDINGS_PROMPT["system_prompt"],
            user_prompt,
            response_schema=_StatFindingsResp,
            temperature=0.3,
            max_tokens=800,
        )
        parsed: _StatFindingsResp = result["content"]
        findings = [item.model_dump() for item in parsed.findings]
        limitations = [item.model_dump() for item in parsed.stat_limitations]
        recommendation = parsed.stat_recommendation.model_dump()
        return findings, limitations, recommendation, None
    except Exception as exc:  # noqa: BLE001 — fallback path
        return [], [], None, {"phase": "statistical_findings", "error": str(exc)}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def build_hypothesis_overlay(
    ctx: Any,
    *,
    use_llm: bool = True,
) -> HypothesisOverlay:
    """Build the full Phase E hypothesis overlay.

    Args:
        ctx: ParsedContext (or any object exposing ``statistical_hypothesis``).
        use_llm: if False, only the deterministic skeleton is returned. Useful
            for tests and offline pipelines.
    """
    t0 = time.time()
    overlay = build_overlay_skeleton(ctx)

    if overlay.suppressed:
        return overlay

    if use_llm:
        try:
            overlay = await _enrich_strips_with_llm(overlay)
        except Exception as exc:  # noqa: BLE001 — defensive
            overlay.fallbacks_used = True
            overlay.errors.append(f"hypothesis_overlay: {exc}")

        # §3.3 Statistical Findings — single LLM call synthesizing H-01..H-09
        # headlines, PLUS 2 limitations and 1 recommendation. Errors and timeouts
        # are tolerated: assembler falls back to all-Council items when stat items unavailable.
        try:
            client = get_client()
            findings, limitations, recommendation, err = await asyncio.wait_for(
                asyncio.to_thread(_llm_statistical_findings, client, ctx),
                timeout=90.0,
            )
            overlay.statistical_findings = findings
            overlay.stat_limitations = limitations
            overlay.stat_recommendation = recommendation
            if err is not None:
                overlay.fallbacks_used = True
                overlay.errors.append(err.get("error", "stat_findings: unknown"))
        except asyncio.TimeoutError:
            overlay.fallbacks_used = True
            overlay.errors.append("statistical_findings: timeout after 90s")
        except Exception as exc:  # noqa: BLE001 — defensive
            overlay.fallbacks_used = True
            overlay.errors.append(f"statistical_findings: {exc}")

    elapsed = time.time() - t0
    print(f"[hypothesis-overlay] Done in {elapsed:.1f}s "
          f"(fallbacks_used={overlay.fallbacks_used}, "
          f"errors={len(overlay.errors)})")
    return overlay
