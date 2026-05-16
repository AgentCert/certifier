"""
Report Assembler — merges phase1 + phase2 + phase3 into a CertificationReport.

Reads the three JSON outputs, maps them into the 12-section report structure,
validates against CertificationReport (Pydantic), and writes the final JSON.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from typing import Any
import yaml
from pathlib import Path
from types import SimpleNamespace

from cert_builder.schema.certification_schema import CertificationReport
from cert_builder.scripts.narratives.sample_size_notice_builder import (
    build_sample_size_notice,
)
from cert_builder.scripts.narratives.hypothesis_overlay_builder import (
    HypothesisOverlay,
    build_hypothesis_overlay,
)
from cert_builder.scripts.narratives.llm_client import get_client, call_llm
from utils.load_config import ConfigLoader


# ── Helpers ──────────────────────────────────────────────────────────

def _text(body: str, style: str | None = None) -> dict:
    block = {"type": "text", "body": body}
    if style:
        block["style"] = style
    return block


def _heading(title: str, detail: str | None = None) -> dict:
    block = {"type": "heading", "title": title}
    if detail:
        block["detail"] = detail
    return block


def _findings(items: list[dict]) -> dict:
    return {"type": "findings", "items": items}


def _table(headers: list, rows: list, title: str | None = None) -> dict:
    block = {"type": "table", "headers": headers, "rows": rows}
    if title:
        block["title"] = title
    return block


def _card(items: list[dict], title: str | None = None) -> dict:
    block = {"type": "card", "items": items}
    if title:
        block["title"] = title
    return block


def _chart(chart_data: dict) -> dict:
    return {**chart_data, "type": "chart"}


def _run_async(coro):
    """Run an async coroutine, handling both nested and top-level event loops."""
    try:
        # Try to get the currently running event loop
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop, safe to use asyncio.run()
        return asyncio.run(coro)
    else:
        # Event loop already running, run in a new thread with its own loop
        def run_in_thread():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
        
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(run_in_thread).result()


_CATEGORY_LABEL_MAP = {
    "application_fault": "Application",
    "network_fault": "Network",
    "resource_fault": "Resource",
    "database_fault": "Database",
    "storage_fault": "Storage",
    "security_fault": "Security",
}


def _pretty_category(raw: str) -> str:
    if raw in _CATEGORY_LABEL_MAP:
        return _CATEGORY_LABEL_MAP[raw]
    return raw.replace("_", " ").title().replace(" Fault", "")


def _h01_ci_bar(per_category: list[dict], *, title: str, y_label: str,
                reference_lines: list[dict] | None = None) -> dict | None:
    """Build a `ci_bar` chart from H-01 per-category results (IQM ± BCa CI)."""
    points = []
    for row in per_category or []:
        if row.get("iqm") is None:
            continue
        points.append({
            "label": _pretty_category(row.get("category", "")),
            "value": float(row["iqm"]),
            "ci_low": float(row["ci_lower"]) if row.get("ci_lower") is not None else None,
            "ci_high": float(row["ci_upper"]) if row.get("ci_upper") is not None else None,
        })
    if not points:
        return None
    return {
        "chart_type": "ci_bar",
        "title": title,
        "y_label": y_label,
        "points": points,
        "reference_lines": reference_lines or [],
    }


def _h02_ci_bar(detection_pc: list[dict], mitigation_pc: list[dict], *,
                 title: str, y_label: str = "Rate (0–1)") -> dict | None:
    """Build a `ci_bar` chart from H-02 per-category Wilson rates (Detection + Mitigation)."""
    points = []
    def _add(rows, group_name):
        for row in rows or []:
            if row.get("rate") is None:
                continue
            points.append({
                "label": _pretty_category(row.get("category", "")),
                "value": float(row["rate"]),
                "ci_low": float(row["wilson_lower"]) if row.get("wilson_lower") is not None else None,
                "ci_high": float(row["wilson_upper"]) if row.get("wilson_upper") is not None else None,
                "group": group_name,
            })
    _add(detection_pc, "Detection")
    _add(mitigation_pc, "Mitigation")
    if not points:
        return None
    return {
        "chart_type": "ci_bar",
        "title": title,
        "y_label": y_label,
        "points": points,
        "reference_lines": [],
    }


def _h02_compliance_ci_bar(rai_pc: list[dict], security_pc: list[dict], *,
                           title: str, y_label: str = "Rate (0–1)") -> dict | None:
    """Build a `ci_bar` chart from H-02 per-category Wilson rates (RAI + Security Compliance)."""
    points = []
    def _add(rows, group_name):
        for row in rows or []:
            if row.get("rate") is None:
                continue
            points.append({
                "label": _pretty_category(row.get("category", "")),
                "value": float(row["rate"]),
                "ci_low": float(row["wilson_lower"]) if row.get("wilson_lower") is not None else None,
                "ci_high": float(row["wilson_upper"]) if row.get("wilson_upper") is not None else None,
                "group": group_name,
            })
    _add(rai_pc, "RAI Compliance")
    _add(security_pc, "Security Compliance")
    if not points:
        return None
    return {
        "chart_type": "ci_bar",
        "title": title,
        "y_label": y_label,
        "points": points,
        "reference_lines": [],
    }


def _phase1_h01_h02(phase1: dict) -> tuple[dict, dict]:
    """Return (h01, h02) result dicts from phase1 statistical_hypothesis, or empty dicts."""
    sh = phase1.get("statistical_hypothesis") or {}
    outer = sh.get("results") or {}
    inner = outer.get("results") if isinstance(outer.get("results"), dict) else outer
    if not isinstance(inner, dict):
        return {}, {}
    return (inner.get("h01") or {}), (inner.get("h02") or {})


def _scope_stats(items: list[dict]) -> dict:
    return {"type": "scope_stats", "items": items}


def _fault_pills(items: list[dict], title: str | None = None) -> dict:
    block = {"type": "fault_pills", "items": items}
    if title:
        block["title"] = title
    return block


def _part_banner(label: str, title: str) -> dict:
    return {"type": "part_banner", "label": label, "title": title}


def _interpretation_scale(bands: list[str], title: str | None = None) -> dict:
    block = {"type": "interpretation_scale", "bands": bands}
    if title:
        block["title"] = title
    return block


def _taxonomy_table(headers: list, rows: list, title: str | None = None,
                    footnote: str | None = None) -> dict:
    block = {"type": "taxonomy_table", "headers": headers, "rows": rows}
    if title:
        block["title"] = title
    if footnote:
        block["footnote"] = footnote
    return block


def _enumerated_item(*, kind: str, index: int, severity: str, scope: str,
                     body: str, tags: list[str] | None = None,
                     frequency: str | None = None) -> dict:
    block = {
        "type": "enumerated_item",
        "kind": kind,
        "index": index,
        "severity": severity,
        "scope": scope,
        "body": body,
        "tags": tags or [],
    }
    if frequency:
        block["frequency"] = frequency
    return block


def _verdict_for(value: float | None, *, good: float, fair: float,
                 lower_is_better: bool = False) -> str:
    """Map a numeric value to a strip verdict using two thresholds."""
    if value is None:
        return "inconclusive"
    if lower_is_better:
        if value <= good:
            return "pass"
        if value <= fair:
            return "inconclusive"
        return "flag"
    if value >= good:
        return "pass"
    if value >= fair:
        return "inconclusive"
    return "flag"


def _det_strip(*, hypothesis_id: str, metric_label: str, verdict: str,
               summary: str, method: str | None = None) -> dict:
    """Build a deterministic hypothesis_strip block (no facts, no LLM)."""
    block = {
        "type": "hypothesis_strip",
        "verdict": verdict,
        "hypothesis_id": hypothesis_id,
        "metric_label": metric_label,
        "facts": [],
        "summary": summary,
    }
    if method:
        block["method"] = method
    return block


def _scrub_kn_text(text: str) -> str:
    """Strip K/N fractions and similar count constructions from narrative text.

    Stat tables legitimately display K/N (e.g. "31/31"), but the secondary
    table-findings LLM should not echo them back in narrative bullets. This
    matches the same forbid-list applied to the qualitative findings LLM.
    """
    if not text:
        return text
    # Remove parenthetical K/N like " (31/31)", " (62/62, 100.0%)"
    text = re.sub(
        r"\s*\(?\s*\d{1,3}\s*/\s*\d{1,3}(?:,\s*\d{1,3}\.\d%?)?\s*\)?",
        "",
        text,
    )
    # Catch bare inline fractions, e.g. "0/31 and 0/62"
    text = re.sub(r"\b\d{1,3}\s*/\s*\d{1,3}\b", "", text)
    # Tidy double spaces / orphan punctuation
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def _generate_table_findings(table_dict: dict, metric_name: str) -> str:
    """Generate 2 key findings from a table using LLM.
    
    Args:
        table_dict: Dict with 'headers' and 'rows' keys (from phase2 tables)
        metric_name: Human-readable metric name (e.g., "Time-to-Detect")
    
    Returns:
        Formatted findings text with 2 bullet points or empty string on failure
    """
    try:
        # Format table data for LLM
        headers = table_dict.get("headers", [])
        rows = table_dict.get("rows", [])
        
        if not headers or not rows:
            return ""
        
        # Convert table to readable format
        table_text = " | ".join(headers) + "\n"
        for row in rows:
            table_text += " | ".join(str(c) for c in row) + "\n"
        
        # Load prompt template
        prompt_path = Path(__file__).parent.parent / "prompts" / "table_findings_prompt.yaml"
        with open(prompt_path, 'r') as f:
            prompt_yaml = yaml.safe_load(f)
        
        system_prompt = prompt_yaml.get("system_prompt", "")
        user_template = prompt_yaml.get("user_prompt_template", "")
        
        # Substitute template variables
        user_prompt = user_template.format(
            metric_name=metric_name,
            table_data=table_text
        )
        
        # Call LLM (synchronous)
        client = get_client()
        result = call_llm(
            client,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.7,
            max_tokens=400,
            expect_json=False
        )
        
        return _scrub_kn_text(str(result.get("content", "")).strip()) if result else ""
    
    except Exception as e:
        # Fallback on any error
        return ""


def _get_table_findings(table_dict: dict, metric_name: str) -> str:
    """Generate table findings (now synchronous, no wrapper needed)."""
    return _generate_table_findings(table_dict, metric_name)


def _findings_from_text(text: str) -> dict | None:
    """Convert LLM-generated findings text into a findings block.
    
    Args:
        text: Multi-line text with findings (each starting with ✓ or ⚠)
    
    Returns:
        Findings block dict or None if text is empty
    """
    if not text.strip():
        return None
    
    # Parse lines and convert to findings format
    findings_list = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        
        # Determine severity based on symbol, but keep symbol in text
        severity = "note"
        if line.startswith("✓"):
            severity = "good"
            # Remove symbol and whitespace, then add it back to preserve formatting
            line_content = line[2:].strip()
            line = f"✓ {line_content}"
        elif line.startswith("⚠"):
            severity = "concern"
            line_content = line[2:].strip()
            line = f"⚠ {line_content}"
        
        findings_list.append({"severity": severity, "text": line})
    
    if not findings_list:
        return None
    
    return {"type": "findings", "items": findings_list}


# ── Section builders ────────────────────────────────────────────────

def _section_executive_summary(phase1, phase2, phase3, overlay: HypothesisOverlay | None = None):
    """Section 1: Executive Summary (§1.1 Identity + §1.2 Experiment Scope)."""
    scope_text = phase3["scope_narrative"]["text"]
    meta = phase1["meta"]
    cats = phase1.get("categories", []) or []

    total_runs = meta.get("total_runs", 0)
    successful_runs = meta.get("successful_runs", 0)
    failed_runs = meta.get("failed_runs", 0)
    total_faults = meta.get("total_faults_tested", 0)
    total_categories = meta.get("total_fault_categories", 0)
    runs_per_fault = meta.get("runs_per_fault", 0)

    sh = phase1.get("statistical_hypothesis") or {"status": "not_requested"}
    sh_status = sh.get("status", "not_requested")
    if sh_status == "ok":
        adequacy_value = "Sufficient"
        results = sh.get("results") or {}
        # Handle potential double-nesting (mirrors _phase1_h01_h02 pattern)
        inner_results = results.get("results") if isinstance(results.get("results"), dict) else results
        executed_keys = sorted(
            k for k, v in inner_results.items()
            if isinstance(v, dict) and v.get("status") not in ("skipped", "not_requested")
        )
        executed = len(executed_keys)
        if executed:
            first_id = f"H-{executed_keys[0][1:]}"
            last_id = f"H-{executed_keys[-1][1:]}"
            hypotheses_tested = (
                f"{executed} ({first_id})"
                if first_id == last_id
                else f"{executed} ({first_id} \u2013 {last_id})"
            )
        else:
            hypotheses_tested = "\u2014"
    elif sh_status == "skipped":
        adequacy_value = "Insufficient"
        hypotheses_tested = "—"
    else:
        adequacy_value = "Not requested"
        hypotheses_tested = "—"

    scope_grid = _scope_stats([
        {"value": str(total_categories), "label": "Fault Categories"},
        {"value": str(total_faults), "label": "Faults Tested"},
        {"value": str(total_runs), "label": "Total Runs"},
        {"value": str(successful_runs), "label": "Successful Runs"},
        {"value": str(failed_runs), "label": "Failed Runs"},
        {"value": hypotheses_tested, "label": "Hypotheses Tested"},
        {"value": adequacy_value, "label": "Sample Adequacy"},
    ])

    if sh_status == "ok":
        min_req = sh.get("min_required") or runs_per_fault
        adequacy_note = (
            f"Statistical adequacy. Each fault category contains ≥ {min_req} "
            "independent runs, meeting the framework-mandated minimum for "
            "the H-01 – H-09 statistical hypothesis tests. All nine hypotheses "
            "were evaluated at full strength."
        )
    elif sh_status == "skipped":
        observed = sh.get("observed_per_category") or {}
        min_req = sh.get("min_required")
        adequacy_note = (
            f"Statistical adequacy. Per-category run counts {observed} fall below "
            f"the framework-mandated minimum (n ≥ {min_req}) for the H-01 – H-09 "
            "statistical hypothesis tests. Statistical inference is suppressed "
            "in this report."
        )
    else:
        adequacy_note = (
            "Statistical hypothesis testing was not requested for this run "
            "(use --advanced-analysis to enable the H-01 – H-09 framework)."
        )

    # Sample-adequacy table per category.
    adequacy_rows = []
    for cat in cats:
        runs = cat.get("total_runs", 0)
        faults = cat.get("faults_tested") or []
        n_per_fault = (runs // len(faults)) if faults else runs
        if sh_status == "ok":
            status = "Sufficient"
        elif sh_status == "skipped":
            status = "Insufficient"
        else:
            status = "—"
        adequacy_rows.append([
            cat.get("label", cat.get("fault_category", "")),
            len(faults),
            runs,
            n_per_fault,
            status,
        ])
    adequacy_table = _table(
        ["Category", "Faults", "Total Runs", "Runs / Fault", "Adequacy"],
        adequacy_rows,
        title="Sample Adequacy by Category",
    )

    # Fault-category pill row (one pill per category summarising fault list + runs).
    content = [
        _heading("1.1 Agent Identity"),
        _card(phase2["cards"]["identity_card"]["items"]),
        _heading("1.2 Experiment Scope"),
        _text(scope_text),
        scope_grid,
        _text(adequacy_note, style="info"),
    ]

    # Fault Categories Tested card (one row of pills, one per category).
    if cats:
        fc_items = []
        for cat in cats:
            label = cat.get("label", cat.get("fault_category", ""))
            faults = cat.get("faults_tested") or []
            runs = cat.get("distinct_runs", cat.get("total_runs", 0))
            fault_list = ", ".join(faults) if faults else "—"
            fc_items.append({
                "label": f"{label} Fault",
                "value": f"{fault_list} ({runs} runs)",
            })
        content.append(_card(fc_items, title="Fault Categories Tested"))

    # Phase D: skip-path notice (only when --advanced-analysis was requested
    # but the gate failed).
    notice = build_sample_size_notice(phase1.get("statistical_hypothesis"))
    if notice is not None:
        content.append(notice)

    # §1.3 Experiment Findings — consolidates scorecard snapshot and key findings from old Section 3
    content.extend([
        _heading("1.3 Experiment Findings"),
        _heading("1.3.1 Agent Performance Summary"),
    ])
    
    # Generate Agent Performance Summary via LLM call
    try:
        context = _build_agent_performance_summary_context(phase1, phase2, phase3, overlay)
        summary_text = _run_async(_generate_agent_performance_summary(context))
        content.append(_text(summary_text))
    except Exception as e:
        # Fallback: use placeholder if LLM call fails
        print(f"Warning: Agent Performance Summary generation failed: {e}")
        content.append(_text(
            "Agent performance summary could not be generated. "
            "See cert_builder/prompts/agent_performance_summary_prompt.yaml for the prompt template and required context."
        ))
    
    # Add §1.3.2 Scorecard Snapshot and §1.3.3 Key Findings (moved from old Section 3)
    content.extend(_section_experiment_findings_scorecard_content(phase2, phase3, phase1, overlay))

    return {
        "id": "executive_summary",
        "number": 1,
        "part": None,
        "title": "Executive Summary",
        "intro": "",
        "content": content,
    }


_HYPOTHESIS_TAXONOMY_ROWS = [
    ["H-01", "CI for Continuous Metrics",
     "How fast does the agent respond — and how confident are we?",
     "Both",
     "IQM, Bootstrap BCa CI (B=10,000)"],
    ["H-02", "Success Rate with Safety Floor",
     "What % of faults are caught — and what's the worst-case guarantee?",
     "Both",
     "Wilson CI"],
    ["H-03", "Cross-Category Comparison",
     "Does the agent handle all fault types equally well?",
     "Both",
     "Kruskal-Wallis, Mann-Whitney U, A₁₂, Holm-Bonferroni"],
    ["H-04", "Success Rate Uniformity",
     "Does the agent fail some fault types more often?",
     "Both",
     "Chi-Square Test (Fisher's Exact fallback)"],
    ["H-05", "Consistency & Predictability",
     "Is the agent reliable every time, or erratic?",
     "Both",
     "Levene's Test, Coefficient of Variation"],
    ["H-06", "SLA Threshold Compliance",
     "Can we prove this agent meets the SLA?",
     "SLA-Aware",
     "Wilcoxon signed-rank, TOST, Bootstrap BCa CI"],
    ["H-07", "SLA Breach Rate",
     "Is the SLA violation rate below the allowed limit?",
     "SLA-Aware",
     "Exact Binomial, Wilson CI"],
    ["H-08", "Tail Risk Analysis",
     "When the agent fails, how badly does it fail?",
     "Both*",
     "CVaR₉₅, CVaR/IQM ratio"],
    ["H-09", "Temporal Stability",
     "Is the agent getting worse over time?",
     "Both*",
     "CUSUM, EWMA control charts"],
]


def _section_methodology(phase2, overlay: HypothesisOverlay | None = None):
    """Section 2: Methodology (top bullets + §2.1 Judges)."""
    intros = phase2["hardcoded"]["section_intros"]
    bullets = phase2["hardcoded"]["methodology_bullets"]
    
    # Filter out statistical hypothesis testing bullet when advanced analysis is suppressed
    if overlay is not None and overlay.suppressed:
        bullets = [b for b in bullets if "Statistical hypothesis testing" not in b]
    
    method_findings = [{"severity": "note", "text": b} for b in bullets]

    content = [
        _findings(method_findings),
        _heading("2.1 LLM Council \u2014 Judge Models"),
        _text(intros.get("methodology", "")),
        _table(**phase2["tables"]["judge_models"]),
    ]

    return {
        "id": "methodology",
        "number": 2,
        "part": None,
        "title": "Evaluation Methodology",
        "intro": intros.get("methodology", ""),
        "content": content,
    }


def _build_statistical_findings(overlay: HypothesisOverlay | None,
                                phase1: dict) -> list[dict]:
    """Synthesize §3.3 deterministic statistical findings from the overlay.

    Returns a list of FindingItem dicts (severity + text). Pulls headline
    facts from H-02, H-03, H-04, H-05 results when available. Returns an
    empty list when overlay is suppressed.
    """
    if overlay is None or overlay.suppressed:
        return []
    sh = phase1.get("statistical_hypothesis") or {}
    outer = (sh.get("results") or {})
    inner = outer.get("results") if isinstance(outer.get("results"), dict) else outer
    if not isinstance(inner, dict):
        return []

    findings: list[dict] = []

    # H-02: weakest certified floor on detection-rate.
    h02 = (inner.get("h02") or {}).get("fault_detection_success_rate") or {}
    per_cat_h02 = h02.get("per_category") or []
    if per_cat_h02:
        worst = min(per_cat_h02, key=lambda c: c.get("wilson_lower", 1.0))
        cat_label = (worst.get("category") or "").replace("_fault", "").title() or "—"
        floor = (worst.get("wilson_lower") or 0.0) * 100.0
        upper = (worst.get("wilson_upper") or 0.0) * 100.0
        findings.append({
            "severity": "concern" if floor < 60.0 else "note",
            "text": (
                f"H-02 — weakest certified detection-rate floor: {cat_label} "
                f"Wilson 95% CI [{floor:.1f}%, {upper:.1f}%]."
            ),
        })

    # H-04: chi-square verdict on cross-category uniformity.
    h04 = (inner.get("h04") or {}).get("fault_detection_success_rate") or {}
    if h04:
        sig = h04.get("significant")
        chi = h04.get("statistic")
        p = h04.get("p_value")
        weakest = (h04.get("weakest_category") or "").replace("_fault", "").title()
        if sig:
            text = (
                f"H-04 — detection-rate disparity is statistically significant "
                f"(χ² = {chi:.2f}, p = {p:.3f}); weakest category: {weakest}."
            )
            sev = "concern"
        else:
            text = (
                f"H-04 — detection rates are statistically uniform across categories "
                f"(χ² = {chi:.2f}, p = {p:.3f})."
            )
            sev = "good"
        findings.append({"severity": sev, "text": text})

    # H-03: cross-category latency disparity (TTD).
    h03_ttd = (inner.get("h03") or {}).get("time_to_detect") or {}
    if h03_ttd.get("omnibus_significant"):
        findings.append({
            "severity": "concern",
            "text": (
                f"H-03 — time-to-detect differs significantly across categories "
                f"(Kruskal-Wallis p = {_p_str(h03_ttd.get('omnibus_p'))}). "
                "Aggregated mean understates per-category dispersion."
            ),
        })

    # H-05: variance instability.
    h05 = (inner.get("h05") or {}).get("time_to_detect") or {}
    unstable = h05.get("unstable_categories") or []
    if unstable:
        names = ", ".join((c.replace("_fault", "").title() for c in unstable))
        findings.append({
            "severity": "concern",
            "text": (
                f"H-05 — variance instability detected in {names} "
                f"(Levene p = {_p_str(h05.get('levene_p'))})."
            ),
        })

    return findings


def _p_str(p):
    if p is None:
        return "—"
    try:
        v = float(p)
    except Exception:
        return str(p)
    return "< 0.001" if v < 0.001 else f"{v:.3f}"


def _section_experiment_findings_scorecard_content(phase2, phase3, phase1, overlay: HypothesisOverlay | None = None):
    """Helper to generate §1.3.2 and §1.3.3 content (scorecard snapshot + key findings).
    
    This was previously Section 3 (Scorecard Snapshot). Now it's part of §1.3.
    Returns a list of content blocks to be appended to the executive summary.
    """
    # §1.3.2 Scorecard Snapshot
    content = [
        _heading("1.3.2 Scorecard Snapshot"),
        _chart(phase2["charts"]["scorecard_radar"]),
        _text(
            "**Benchmark Comparison:** The purple filled radar shows this agent's actual performance. "
            "The green dashed line shows the 'Performance Threshold' (1.0 for Safety and Security, 0.75 for other dimensions) across all dimensions. "
            "Overlap indicates areas where the agent meets or exceeds expectations; extending beyond shows exceptional performance; falling short reveals improvement opportunities."
        ),
    ]

    # §1.3.3 Key Findings (synthesized with statistical findings already integrated in the LLM call)
    key_findings = [
        {"severity": f["severity"], "text": f"{f['headline']}: {f['detail']}"}
        for f in phase3["key_findings"]["items"]
    ]
    
    content.append(_heading("1.3.3 Key Findings"))
    content.append(_findings(key_findings))

    return content


def _build_agent_performance_summary_context(phase1: dict, phase2: dict, 
                                             phase3: dict, 
                                             overlay: HypothesisOverlay | None = None) -> dict:
    """Build context blocks for the Agent Performance Summary LLM call.
    
    Args:
        phase1: Phase 1 context (meta, categories, statistical_hypothesis)
        phase2: Phase 2 computed content (charts, cards, hardcoded)
        phase3: Phase 3 narratives (scope_narrative, key_findings, etc.)
        overlay: HypothesisOverlay with statistical results
        
    Returns:
        Dict with keys: agent_name, agent_id, certification_date, 
        scorecard_dimensions_block, category_performance_block, 
        key_metrics_block, statistical_summary_block
    """
    meta = phase1.get("meta", {})
    categories = phase1.get("categories", [])
    sh = phase1.get("statistical_hypothesis", {})
    
    # Agent identity
    agent_name = meta.get("agent_name", "Unknown Agent")
    agent_id = meta.get("agent_id", "unknown")
    certification_date = meta.get("certification_date", "Unknown")
    
    # Extract scorecard dimensions from radar chart
    # chart_builder._build_scorecard_radar emits {"dimensions": [{"dimension": ..., "value": ...}, ...]}
    radar_data = phase2.get("charts", {}).get("scorecard_radar", {})
    scorecard_dimensions = []
    for point in radar_data.get("dimensions", []):
        label = point.get("dimension", "")
        value = point.get("value")
        if value is not None and isinstance(value, (int, float)):
            if 0 <= value <= 1:
                scorecard_dimensions.append(f"{label}: {value*100:.0f}%")
            else:
                scorecard_dimensions.append(f"{label}: {value:.1f}")
    
    scorecard_dimensions_block = "\n".join(scorecard_dimensions) if scorecard_dimensions else "No scorecard data available"
    
    # Category performance block
    category_items = []
    for cat in categories:
        cat_name = cat.get("label", cat.get("fault_category", "Unknown"))
        cat_faults = cat.get("faults_tested", [])
        cat_runs = cat.get("distinct_runs", cat.get("total_runs", 0))
        
        # Detection / mitigation rates live under cat["derived"] as 0-1 floats
        # (see ingestion/ingestor.py: derived_metrics → cat["derived"]).
        derived = cat.get("derived", {}) or {}
        detection_rate = derived.get("fault_detection_success_rate")
        mitigation_rate = derived.get("fault_mitigation_success_rate")
        detection_pct = f"{detection_rate*100:.0f}%" if isinstance(detection_rate, (int, float)) else "—"
        mitigation_pct = f"{mitigation_rate*100:.0f}%" if isinstance(mitigation_rate, (int, float)) else "—"
            
        cat_str = f"{cat_name}: {len(cat_faults)} faults, {cat_runs} successful runs, {detection_pct} detection, {mitigation_pct} mitigation"
        category_items.append(cat_str)
    
    category_performance_block = "\n".join(category_items) if category_items else "No category data available"
    
    # Key metrics summary — quote ONLY the successful-run count to keep
    # narrative framing consistent with §1.2 Experiment Scope.
    total_runs = meta.get("total_runs", 0)
    successful_runs = meta.get("successful_runs", total_runs)
    total_faults = meta.get("total_faults_tested", 0)
    total_categories = meta.get("total_fault_categories", 0)
    success_rate = meta.get("success_rate_pct")
    
    key_metrics = []
    key_metrics.append(f"Successful Runs: {successful_runs} (of {total_runs} attempted)")
    key_metrics.append(f"Fault Categories: {total_categories}")
    key_metrics.append(f"Total Faults Tested: {total_faults}")
    if success_rate is not None:
        if isinstance(success_rate, (int, float)):
            key_metrics.append(f"Overall Success Rate: {success_rate:.1f}%")
    
    key_metrics_block = ", ".join(key_metrics)
    
    return {
        "agent_name": agent_name,
        "agent_id": agent_id,
        "certification_date": certification_date,
        "scorecard_dimensions_block": scorecard_dimensions_block,
        "category_performance_block": category_performance_block,
        "key_metrics_block": key_metrics_block,
    }


async def _generate_agent_performance_summary(context: dict) -> str:
    """Call LLM to generate the Agent Performance Summary text.
    
    Args:
        context: Dict with agent_name, agent_id, certification_date, and context blocks
        
    Returns:
        Generated summary text from LLM, or fallback text if LLM call fails
    """
    try:
        # Load the prompt template
        prompt_path = Path(__file__).parent.parent / "prompts" / "agent_performance_summary_prompt.yaml"
        if not prompt_path.exists():
            # Fallback: return placeholder
            return ("Agent performance summary generation is not available. "
                   "Please ensure agent_performance_summary_prompt.yaml exists in cert_builder/prompts/")
        
        with open(prompt_path, 'r') as f:
            prompt_config = yaml.safe_load(f)
        
        system_prompt = prompt_config.get("system_prompt", "")
        user_template = prompt_config.get("user_prompt_template", "")
        
        # Substitute context into user prompt template
        user_prompt = user_template.format(**context)
        
        # Get the LLM client and call LLM
        client = get_client()
        result = call_llm(
            client,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.7,
            max_tokens=500,
            expect_json=False
        )
        
        if result and result.get("content"):
            return str(result["content"]).strip()
        else:
            return "Agent performance summary generation failed. Summary unavailable."
            
    except Exception as e:
        # Log error but don't fail the report
        print(f"Warning: Agent Performance Summary LLM call failed: {e}")
        return (f"Agent performance summary could not be generated due to: {str(e)}. "
               "Proceeding with other report sections.")


def _section_scorecard(phase2, phase3, phase1, overlay: HypothesisOverlay | None = None):
    """DEPRECATED: Section 3 Scorecard has been moved to §1.3 Experiment Findings.
    
    This function is kept for backward compatibility but should not be called.
    The content is now generated by _section_experiment_findings_scorecard_content()
    which is integrated into the executive summary.
    """
    return None


def _section_appendix(overlay: HypothesisOverlay | None = None):
    """Appendix A1: Statistical Hypothesis Framework (H-01 – H-09)."""
    if overlay is None or overlay.suppressed:
        return None

    content = [
        _heading("A1. Statistical Hypothesis Framework (H-01 – H-09)"),
        _text(
            "Beyond descriptive statistics, this certification applies a formal "
            "<strong>9-hypothesis inference framework</strong> grounded in 20 peer-reviewed papers (NeurIPS, ICLR, AAAI, ACL, "
            "ICSE, CCS, JRSS-B, JASA, Biometrika, Mathematical Finance). Each hypothesis replaces single-number "
            "summaries with probabilistic inference — confidence intervals, effect sizes, and worst-case guarantees — "
            "enabling defensible <strong>pass / conditional / fail</strong> decisions. Hypotheses <strong>H-01 to H-05</strong> are "
            "always active; <strong>H-06 & H-07</strong> activate only when SLA thresholds are provided; "
            "<strong>H-08 & H-09</strong> provide informational tail-risk and temporal-stability analysis in both modes."
        ),
        _taxonomy_table(
            headers=["ID", "Hypothesis", "Question", "Mode", "Primary Methods"],
            rows=_HYPOTHESIS_TAXONOMY_ROWS,
            title="H-01 – H-09 Framework Reference",
            footnote=(
                "* H-08 and H-09 provide informational analysis even without SLAs."
            ),
        ),
    ]

    return {
        "id": "appendix",
        "number": 0,  # Appendix sections don't get sequential numbers
        "part": None,
        "title": "Statistical Evaluation Overview",
        "intro": "",
        "content": content,
    }


def _section_appendix_a2(overlay: HypothesisOverlay | None = None):
    """Appendix A2: Statistical Evidence — Performance & Safety Validation (H-01 – H-02).
    
    Collects hypothesis strips from sections 5 (Detection & Response), 6 (Reasoning & Quality),
    and 7 (Safety & Compliance) and organizes them into H-01 and H-02 subsections.
    
    Details are derived from the strips themselves, not hardcoded.
    """
    if overlay is None or overlay.suppressed:
        return None
    
    strips = (overlay.inline_strips if overlay else {}) or {}
    
    # Collect H-01 strips: continuous metrics with confidence intervals
    h01_content = [
        _heading("A2. Statistical Evidence: Performance & Safety Validation (H-01 – H-02)"),
        _heading("H-01: Confidence Intervals for Continuous Metrics", detail="Timing, quality, and hallucination via IQM ± Bootstrap BCa 95% CI"),
    ]
    
    # Time-to-Detect (from section 5)
    ttd_strips = strips.get("time_to_detect", []) or []
    if ttd_strips:
        h01_content.append(_heading("Time-to-Detect"))
        h01_content.extend(ttd_strips)
    
    # Time-to-Mitigate (from section 5)
    ttm_strips = strips.get("time_to_mitigate", []) or []
    if ttm_strips:
        h01_content.append(_heading("Time-to-Mitigate"))
        h01_content.extend(ttm_strips)
    
    # Response & Reasoning Quality (from section 6)
    reas_strips = strips.get("reasoning_quality_score", []) or []
    if reas_strips:
        h01_content.append(_heading("Response & Reasoning Quality"))
        h01_content.extend(reas_strips)
    
    # Hallucination (from section 6)
    halluc_strips = strips.get("hallucination_score", []) or []
    if halluc_strips:
        h01_content.append(_heading("Hallucination"))
        h01_content.extend(halluc_strips)
    
    # Collect H-02 strips: success rates with safety floor
    h01_content.append(_heading("H-02: Success Rates with Safety Floor", detail="Detection, mitigation, and compliance rates via Wilson 95% CI"))
    
    # Detection & Mitigation Rates (from section 5)
    det_strips = strips.get("fault_detection_success_rate", []) or []
    mit_strips = strips.get("fault_mitigation_success_rate", []) or []
    if det_strips or mit_strips:
        h01_content.append(_heading("Detection & Mitigation Rates"))
        h01_content.extend(det_strips)
        h01_content.extend(mit_strips)
    
    # RAI Compliance (from section 7)
    rai_strips = strips.get("rai_compliance_rate", []) or []
    if rai_strips:
        h01_content.append(_heading("RAI Compliance"))
        h01_content.extend(rai_strips)
    
    # Security Compliance (from section 7)
    sec_strips = strips.get("security_compliance_rate", []) or []
    if sec_strips:
        h01_content.append(_heading("Security Compliance"))
        h01_content.extend(sec_strips)
    
    # If no strips found at all, return None
    if len(h01_content) <= 2:  # Just the main headings
        return None
    
    return {
        "id": "appendix_a2",
        "number": 0,
        "part": None,
        "title": "Statistical Evidence: Performance & Safety Validation (H-01 \u2013 H-02)",
        "intro": "",
        "content": h01_content,
    }


def _section_qualitative_findings(phase1, phase2, phase3):
    """Section 4: Overall Qualitative Findings (LLM Council).

    Each subsection is capped at the most-salient ~2 items to mirror the
    framework HTML target (4.1: 2 items, 4.2: 2–3 items, 4.3: 1–2 items).
    The safety summary table is intentionally NOT emitted here — it lives
    in §8 Safety & Compliance — but §4.2 receives a synthesized 1-line
    bullet summarizing that table as its 3rd item.
    """
    intros = phase2["hardcoded"]["section_intros"]
    qf = phase3["qualitative_findings"]

    # 4.1 Response & Reasoning Quality — reasoning dim only, capped at 2.
    group1: list[dict] = []
    for f in qf.get("reasoning", [])[:2]:
        group1.append({"severity": f["severity"], "text": f"{f['headline']}: {f['detail']}"})

    # 4.2 Safety & Compliance — RAI + security (≤2 LLM bullets) plus a
    # synthesized 3rd bullet that summarizes the §8 safety/security table.
    group2: list[dict] = []
    safety_items = qf.get("safety", [])[:1]
    security_items = qf.get("security", [])[:1]
    for f in safety_items + security_items:
        group2.append({"severity": f["severity"], "text": f"{f['headline']}: {f['detail']}"})

    # Synthesized table-summary bullet (3rd item).
    cats = (phase1 or {}).get("categories", []) or []
    if cats:
        rai_vals = [(c.get("derived") or {}).get("rai_compliance_rate") for c in cats]
        sec_vals = [(c.get("derived") or {}).get("security_compliance_rate") for c in cats]
        rai_vals = [v for v in rai_vals if isinstance(v, (int, float))]
        sec_vals = [v for v in sec_vals if isinstance(v, (int, float))]
        rai_rate = (sum(rai_vals) / len(rai_vals)) if rai_vals else None
        sec_rate = (sum(sec_vals) / len(sec_vals)) if sec_vals else None
        pii_any = any(
            ((c.get("boolean") or {}).get("pii_detection") or {}).get("any_detected")
            for c in cats
        )
        if rai_rate is not None and sec_rate is not None:
            sev = "good" if (rai_rate >= 0.95 and sec_rate >= 0.95 and not pii_any) else "note"
            pii_clause = "zero PII detections" if not pii_any else "PII detected in at least one category"
            summary_text = (
                f"Compliance summary — {rai_rate * 100:.0f}% RAI and {sec_rate * 100:.0f}% security "
                f"compliance across {len(cats)} categories with {pii_clause}."
            )
            group2.append({"severity": sev, "text": summary_text})
    group2 = group2[:3]

    # 4.3 Hallucination Assessment — capped at 2.
    group3: list[dict] = []
    for f in qf.get("hallucination", [])[:2]:
        group3.append({"severity": f["severity"], "text": f"{f['headline']}: {f['detail']}"})

    return {
        "id": "qualitative_findings",
        "number": 3,
        "part": None,
        "title": "Overall Qualitative Findings (LLM Council Output)",
        "intro": intros.get("reasoning", "Cross-category consensus from the LLM Council."),
        "content": [
            _heading("4.1 Response & Reasoning Quality"),
            _findings(group1),
            _heading("4.2 Safety & Compliance"),
            _findings(group2),
            _heading("4.3 Hallucination Assessment"),
            _findings(group3),
        ],
    }


def _combine_h02_rate_strips(det_strips: list[dict],
                             mit_strips: list[dict]) -> dict | None:
    """Merge the H-02 detection-rate and mitigation-rate strips into one.

    The framework HTML presents §5.3 with a single ``H-02 Detection &
    Mitigation Rates`` strip whose chips and prose cover both metrics
    together. The overlay builder, however, emits one strip per metric.
    This helper folds them into a single composite strip:

    * ``facts`` are concatenated; each fact's label is prefixed with the
      metric kind ("Detection — Application", "Mitigation — Network", …)
      so the chips remain self-describing.
    * ``verdict`` is the worst (flag > inconclusive > pass) across inputs.
    * ``findings`` are concatenated (a blank line between the two prose
      bodies). When only one strip has findings the other side is omitted.
    * ``method`` falls back to the detection strip's method.

    Returns ``None`` if neither input has any strip — caller can then skip
    rendering entirely.
    """
    det = det_strips[0] if det_strips else None
    mit = mit_strips[0] if mit_strips else None
    if det is None and mit is None:
        return None

    def _label_facts(strip: dict | None, kind: str) -> list[dict]:
        if not strip:
            return []
        out = []
        for f in strip.get("facts") or []:
            out.append({
                "label": f"{kind} — {f.get('label', '')}",
                "text": f.get("text", ""),
                "tone": f.get("tone", "good"),
            })
        return out

    facts = _label_facts(det, "Detection") + _label_facts(mit, "Mitigation")

    severity_rank = {"flag": 2, "inconclusive": 1, "pass": 0}
    verdicts = [s.get("verdict", "pass") for s in (det, mit) if s]
    verdict = max(verdicts, key=lambda v: severity_rank.get(v, 0))

    findings_parts: list[str] = []
    for strip, label in ((det, "Detection."), (mit, "Mitigation.")):
        if strip and strip.get("findings"):
            findings_parts.append(f"{label} {strip['findings']}")
    findings = "\n\n".join(findings_parts) or None

    method = (det or mit or {}).get("method")

    return {
        "type": "hypothesis_strip",
        "verdict": verdict,
        "hypothesis_id": "H-02",
        "metric_label": "Detection & Mitigation Rates",
        "facts": facts,
        "method": method,
        "summary": f"H-02 Detection & Mitigation Rates — verdict: {verdict}.",
        "findings": findings,
    }


def _section_detection_response(phase2, phase1: dict | None = None,
                                  overlay: HypothesisOverlay | None = None):
    """Section 5: Detection & Response (§5.1 TTD, §5.2 TTM, §5.3 Rates folded in).

    When `phase1` is provided and contains H-01/H-02 results, BCa/Wilson CI
    bar charts are appended after each grouped-bar chart — mirroring the
    "TTD/TTM Statistical Inference" pair shown in the framework HTML.
    """
    defs = phase2["hardcoded"]["definitions"]
    stats = phase2["hardcoded"]["statistics"]
    strips = (overlay.inline_strips if overlay else {}) or {}

    h01, h02 = _phase1_h01_h02(phase1 or {})
    ttd_pc = (h01.get("time_to_detect") or {}).get("per_category") or []
    ttm_pc = (h01.get("time_to_mitigate") or {}).get("per_category") or []
    fdsr_pc = (h02.get("fault_detection_success_rate") or {}).get("per_category") or []
    fmsr_pc = (h02.get("fault_mitigation_success_rate") or {}).get("per_category") or []

    ttd_ref = phase2["charts"]["ttd_bar"].get("reference_lines") or []
    ttm_ref = phase2["charts"]["ttm_bar"].get("reference_lines") or []

    ttd_ci = _h01_ci_bar(
        ttd_pc,
        title="TTD Statistical Inference (IQM · BCa 95% CI)",
        y_label="Seconds",
        reference_lines=ttd_ref,
    )
    ttm_ci = _h01_ci_bar(
        ttm_pc,
        title="TTM Statistical Inference (IQM · BCa 95% CI)",
        y_label="Seconds",
        reference_lines=ttm_ref,
    )
    rates_ci = _h02_ci_bar(
        fdsr_pc, fmsr_pc,
        title="Detection & Mitigation Rates (Wilson 95% CI)",
    )

    content = [
        _text(defs["ttd"], style="info"),
        _text(defs["ttm"], style="info"),
        _heading("5.1 Time-to-Detect"),
        _chart(phase2["charts"]["ttd_bar"]),
    ]
    if ttd_ci is not None:
        content.append(_chart(ttd_ci))
    content.append(_table(**phase2["tables"]["ttd_stats"]))
    ttd_findings_text = _get_table_findings(phase2["tables"]["ttd_stats"], "Time-to-Detect")
    if ttd_findings_text:
        ttd_findings_block = _findings_from_text(ttd_findings_text)
        if ttd_findings_block:
            content.append(ttd_findings_block)
    content.extend([
        _heading("5.2 Time-to-Mitigate"),
        _chart(phase2["charts"]["ttm_bar"]),
    ])
    if ttm_ci is not None:
        content.append(_chart(ttm_ci))
    content.append(_table(**phase2["tables"]["ttm_stats"]))
    ttm_findings_text = _get_table_findings(phase2["tables"]["ttm_stats"], "Time-to-Mitigate")
    if ttm_findings_text:
        ttm_findings_block = _findings_from_text(ttm_findings_text)
        if ttm_findings_block:
            content.append(ttm_findings_block)
    content.extend([
        _heading("5.3 Detection & Mitigation Rates"),
        _text(
            (
                "**Detection Rate** \u2014 percentage of runs where the agent's "
                "detection signal correctly identified the injected fault before "
                "remediation began. **False Negative** \u2014 percentage of runs "
                "where the fault was present but the agent did not detect it. "
                "**False Positive** \u2014 percentage of runs where the agent "
                "reported a fault that was not actually injected. "
                "**Mitigation Rate** \u2014 percentage of runs where the fault "
                "was successfully remediated, regardless of whether the agent "
                "explicitly detected it (includes platform-level recovery)."
            ),
            style="info",
        ),
        _chart(phase2["charts"]["rates_bar"]),
    ])
    if rates_ci is not None:
        content.append(_chart(rates_ci))
    content.append(_table(**phase2["tables"]["detection_rates"]))
    rates_findings_text = _get_table_findings(phase2["tables"]["detection_rates"], "Detection & Mitigation Rates")
    if rates_findings_text:
        rates_findings_block = _findings_from_text(rates_findings_text)
        if rates_findings_block:
            content.append(rates_findings_block)
    content.extend([
        _text(stats["median_p95"], style="info"),
        _text(stats["detection_vs_mitigation"], style="info"),
    ])

    return {
        "id": "detection_response",
        "number": 4,
        "part": None,
        "title": "Detection & Response Performance",
        "intro": "",
        "content": content,
    }


def _section_accuracy(phase2, overlay: HypothesisOverlay | None = None):
    """Section 6: Accuracy & Efficiency."""
    defs = phase2["hardcoded"]["definitions"]
    strips = (overlay.inline_strips if overlay else {}) or {}

    content = [
        _chart(phase2["charts"]["accuracy_heatmap"]),
        _heading("Action Correctness"),
        _interpretation_scale(
            bands=[
                "0.0 – 0.3 Poor",
                "0.3 – 0.6 Fair",
                "0.6 – 0.8 Good",
                "0.8 – 0.95 Strong",
                "0.95 – 1.0 Excellent",
            ],
            title="Action Correctness Scale (0.0 – 1.0)",
        ),
        _table(**phase2["tables"]["action_correctness"]),
        _text(defs["na_explanation"], style="info"),
    ]
    content.extend(strips.get("tool_calls", []))

    return {
        "id": "accuracy_efficiency",
        "number": 6,
        "part": "Agent Capability Assessment",
        "title": "Accuracy & Efficiency",
        "intro": defs["action_correctness"],
        "content": content,
    }


def _section_reasoning(phase1, phase2, overlay: HypothesisOverlay | None = None):
    """Section 7: Reasoning & Quality (§6.1 + §6.2 with H-01 strips)."""
    defs = phase2["hardcoded"]["definitions"]
    intros = phase2["hardcoded"]["section_intros"]
    cats = phase1.get("categories", []) or []
    strips = (overlay.inline_strips if overlay else {}) or {}

    # Extract H-01 reasoning_quality_score and hallucination_score data for CI charts
    h01, _ = _phase1_h01_h02(phase1)
    reas_pc = (h01.get("reasoning_quality_score") or {}).get("per_category") or []
    halluc_pc = (h01.get("hallucination_score") or {}).get("per_category") or []
    
    # Build CI charts
    reas_ci = _h01_ci_bar(
        reas_pc,
        title="Reasoning IQM with BCa 95% CI",
        y_label="Score (0-10)",
    )
    
    halluc_ci = _h01_ci_bar(
        halluc_pc,
        title="Hallucination IQM with BCa 95% CI",
        y_label="Score (0-10)",
    )

    return {
        "id": "reasoning_quality",
        "number": 5,
        "part": None,
        "title": "Reasoning & Quality",
        "intro": intros.get("reasoning", ""),
        "content": [
            _heading("6.1 Reasoning & Response Quality"),
            _text(defs["reasoning_scale"], style="info"),
            _interpretation_scale(
                bands=[
                    "0 – 3 Poor",
                    "4 – 5 Fair",
                    "6 – 7 Good",
                    "8 – 9 Strong",
                    "10 Excellent",
                ],
                title="Reasoning / Response Quality Scale (0 – 10)",
            ),
            _chart(phase2["charts"]["reasoning_bar"]),
            *([_chart(reas_ci)] if reas_ci is not None else []),
            _table(**phase2["tables"]["reasoning_quality"]),
            *((reasoning_findings_block,) if (reasoning_findings_block := _findings_from_text(_get_table_findings(phase2["tables"]["reasoning_quality"], "Reasoning & Response Quality"))) else ()),
            _heading("6.2 Hallucination Assessment"),
            _text(defs["hallucination_score"], style="info"),
            _chart(phase2["charts"]["hallucination_bar"]),
            *([_chart(halluc_ci)] if halluc_ci is not None else []),
            _table(**phase2["tables"]["hallucination"]),
            *((halluc_findings_block,) if (halluc_findings_block := _findings_from_text(_get_table_findings(phase2["tables"]["hallucination"], "Hallucination Assessment"))) else ()),
        ],
    }


def _section_safety(phase1, phase2, overlay: HypothesisOverlay | None = None):
    """Section 8: Safety & Compliance (§7.1 + §7.2 with H-02 data and dynamic strips)."""
    intros = phase2["hardcoded"]["section_intros"]
    strips = (overlay.inline_strips if overlay else {}) or {}
    
    h01, h02 = _phase1_h01_h02(phase1 or {})
    rai_pc = (h02.get("rai_compliance_rate") or {}).get("per_category") or []
    sec_pc = (h02.get("security_compliance_rate") or {}).get("per_category") or []

    compliance_ci = _h02_compliance_ci_bar(
        rai_pc, sec_pc,
        title="RAI & Security Compliance — Wilson 95% CI",
    )

    return {
        "id": "safety_compliance",
        "number": 6,
        "part": None,
        "title": "Safety & Compliance",
        "intro": intros.get("safety", ""),
        "content": [
            _chart(phase2["charts"]["compliance_bar"]),
            *([_chart(compliance_ci)] if compliance_ci is not None else []),
            _heading("7.1 RAI Compliance"),
            _table(**phase2["tables"]["rai_compliance"]),
            *((rai_findings_block,) if (rai_findings_block := _findings_from_text(_get_table_findings(phase2["tables"]["rai_compliance"], "RAI Compliance"))) else ()),
            _heading("7.2 Security Compliance"),
            _table(**phase2["tables"]["security_compliance"]),
            *((sec_findings_block,) if (sec_findings_block := _findings_from_text(_get_table_findings(phase2["tables"]["security_compliance"], "Security Compliance"))) else ()),
        ],
    }


def _section_resource(phase2):
    """Section 9: Resource Utilization."""
    defs = phase2["hardcoded"]["definitions"]
    intros = phase2["hardcoded"]["section_intros"]

    return {
        "id": "resource_utilization",
        "number": 7,
        "part": None,
        "title": "Resource Utilization",
        "intro": "",
        "content": [
            _text(intros.get("token_usage", ""), style="info"),
            _chart(phase2["charts"]["token_stacked"]),
            _table(**phase2["tables"]["token_usage"]),
            _text("Data quality note: Token counts are derived from aggregated LLM telemetry across all runs. Zero or missing values in any category may indicate incomplete instrumentation or silent failures in token tracking. Token metrics should be treated as representative estimates rather than exact values.", style="info"),
        ],
    }


def _section_fault_analysis(phase1, phase2, phase3):
    """Section 13: Fault Category Analysis (CategoryPanelBlock per category).

    Each category is preceded by a numbered sub-heading (e.g. "13.1
    Application Faults"). The section number prefix uses a "{N}" placeholder
    that is patched in ``ReportAssembler.assemble()`` after the global
    section renumbering pass.
    """
    intros = phase2["hardcoded"]["section_intros"]
    categories = phase1["categories"]
    assessments = phase2["assessments"]

    content: list[dict] = []
    sub_idx = 0
    for cat in categories:
        label = cat["label"]
        sub_idx += 1
        content.append(_heading(f"{{N}}.{sub_idx} {label} Faults"))

        faults = cat.get("faults_tested") or []
        runs = cat.get("distinct_runs", cat.get("total_runs", 0))
        derived = cat.get("derived") or {}
        numeric = cat.get("numeric") or {}

        det_rate = derived.get("fault_detection_success_rate")
        mit_rate = derived.get("fault_mitigation_success_rate")
        det_pct = (det_rate * 100.0) if isinstance(det_rate, (int, float)) else None
        mit_pct = (mit_rate * 100.0) if isinstance(mit_rate, (int, float)) else None

        reas = (numeric.get("reasoning_score") or {}).get("mean")
        rq = (numeric.get("response_quality_score") or {}).get("mean")

        # Build dimension blocks from phase2.assessments[label].
        cat_assessments = assessments.get(label, [])
        dimensions: list[dict] = []
        for a in cat_assessments:
            dimensions.append({
                "title": a.get("title", ""),
                "rating": a.get("rating"),
                "confidence": a.get("confidence", "Medium"),
                "agreement": a.get("agreement", 0.0),
                "body": a.get("body", ""),
            })
        if not dimensions:
            dimensions.append({
                "title": "Agent Summary",
                "rating": None,
                "confidence": "Medium",
                "agreement": 0.0,
                "body": "No assessment available.",
            })

        content.append({
            "type": "category_panel",
            "category": label,
            "fault": ", ".join(faults) if faults else "—",
            "runs": runs,
            "detection_rate_pct": det_pct,
            "mitigation_rate_pct": mit_pct,
            "reasoning_score": reas,
            "response_quality_score": rq,
            "dimensions": dimensions,
        })

    return {
        "id": "fault_category_analysis",
        "number": 8,
        "part": None,
        "title": "Fault Category Analysis",
        "intro": intros.get("fault_analysis", ""),
        "content": content,
    }


def _section_limitations(phase2, phase3,
                         overlay: HypothesisOverlay | None = None,
                         phase1: dict | None = None):
    """Section 12: Limitations — Statistical Inference items (from §3.3) first, then LLM Council items.
    
    Cap total at 10 items. If stat items not available (LLM call failed), use only Council items.
    """
    intros = phase2["hardcoded"]["section_intros"]
    council_items = phase3["limitations_enriched"]["items"]

    # Get statistical limitations from overlay (generated in §3.3)
    stat_items: list[dict] = []
    if overlay is not None and not overlay.suppressed and overlay.stat_limitations:
        stat_items = [
            {
                "severity": s["severity"],
                "scope": s["scope"],
                "body": s["body"],
                "tags": s.get("tags", ["Statistical Inference"]),
            }
            for s in overlay.stat_limitations
        ]

    content: list[dict] = []
    next_idx = 1
    
    # Add stat items first (if available)
    for s in stat_items:
        content.append(_enumerated_item(
            kind="limitation", index=next_idx,
            severity=s["severity"], scope=s["scope"],
            body=s["body"], tags=s.get("tags"),
        ))
        next_idx += 1
    
    # Add Council items, capped at 10 total limitations
    remaining_slots = 10 - len(stat_items)
    for item in council_items[:remaining_slots]:
        content.append(_enumerated_item(
            kind="limitation", index=next_idx,
            severity=item.get("severity", "Medium"),
            scope=item.get("category", "—"),
            body=item["limitation"],
            tags=[item["label"]] if item.get("label") else None,
            frequency=item.get("frequency"),
        ))
        next_idx += 1

    # FALLBACK: if stat_limitations failed (empty), use all Council items up to 10
    if not stat_items:
        content = []
        next_idx = 1
        for item in council_items[:10]:
            content.append(_enumerated_item(
                kind="limitation", index=next_idx,
                severity=item.get("severity", "Medium"),
                scope=item.get("category", "—"),
                body=item["limitation"],
                tags=[item["label"]] if item.get("label") else None,
                frequency=item.get("frequency"),
            ))
            next_idx += 1

    return {
        "id": "limitations",
        "number": 9,
        "part": None,
        "title": "Known Limitations",
        "intro": intros.get("limitations", ""),
        "content": content,
    }


def _section_recommendations(phase2, phase3,
                             overlay: HypothesisOverlay | None = None,
                             phase1: dict | None = None):
    """Section 13: Recommendations — Statistical Inference item (from §3.3) first, then LLM Council items.
    
    Cap total at 8 items. If stat item not available (LLM call failed), use only Council items.
    """
    intros = phase2["hardcoded"]["section_intros"]
    council_items = phase3["recommendations_enriched"]["items"]

    # Get statistical recommendation from overlay (generated in §3.3)
    stat_item: dict | None = None
    if overlay is not None and not overlay.suppressed and overlay.stat_recommendation:
        stat_item = {
            "severity": overlay.stat_recommendation["severity"],
            "scope": overlay.stat_recommendation["scope"],
            "body": overlay.stat_recommendation["body"],
            "tags": overlay.stat_recommendation.get("tags", ["Statistical Inference"]),
        }

    content: list[dict] = []
    next_idx = 1
    
    # Add stat item first (if available)
    if stat_item:
        content.append(_enumerated_item(
            kind="recommendation", index=next_idx,
            severity=stat_item["severity"], scope=stat_item["scope"],
            body=stat_item["body"], tags=stat_item.get("tags"),
        ))
        next_idx += 1
    
    # Add Council items, capped at 8 total recommendations
    remaining_slots = 8 - (1 if stat_item else 0)
    for item in council_items[:remaining_slots]:
        content.append(_enumerated_item(
            kind="recommendation", index=next_idx,
            severity=item.get("priority", "Medium"),
            scope=item.get("category", "—"),
            body=item["recommendation"],
            tags=[item["label"]] if item.get("label") else None,
        ))
        next_idx += 1

    # FALLBACK: if stat_recommendation failed (empty), use all Council items up to 8
    if not stat_item:
        content = []
        next_idx = 1
        for item in council_items[:8]:
            content.append(_enumerated_item(
                kind="recommendation", index=next_idx,
                severity=item.get("priority", "Medium"),
                scope=item.get("category", "—"),
                body=item["recommendation"],
                tags=[item["label"]] if item.get("label") else None,
            ))
            next_idx += 1

    return {
        "id": "recommendations",
        "number": 10,
        "part": None,
        "title": "Recommendations",
        "intro": intros.get("recommendations", ""),
        "content": content,
    }


# ── Phase E hypothesis sections ─────────────────────────────────────

def _section_appendix_a3(overlay: HypothesisOverlay | None = None):
    """Appendix A3: Statistical Inference — Cross-Category Comparison (H-03, H-04, H-05)."""
    if overlay is None or overlay.suppressed:
        return None
    
    blocks: list[dict] = []
    blocks.extend(overlay.h03_section_blocks)
    blocks.extend(overlay.h04_section_blocks)
    blocks.extend(overlay.h05_section_blocks)
    if not blocks:
        return None
    
    return {
        "id": "appendix_a3",
        "number": 0,
        "part": None,
        "title": "Statistical Inference: Cross-Category Uniformity & Variance (H-03 \u2013 H-05)",
        "intro": "",
        "content": [
            _heading("A3. Statistical Inference: Cross-Category Comparison (H-03, H-04, H-05)"),
            _text(
                "Does the agent handle all fault categories equally well? Three "
                "hypotheses jointly answer this question: <strong>H-03</strong> compares "
                "continuous metrics across categories using rank-based tests, "
                "<strong>H-04</strong> compares success rates with Chi-Square (Fisher's Exact "
                "fallback for sparse cells), and <strong>H-05</strong> tests variance homogeneity "
                "and per-category stability."
            ),
            *blocks,
        ],
    }


def _section_hypothesis_latency_compliance(overlay: HypothesisOverlay):
    """Optional Phase E section: H-03..H-05 (latency + compliance hypotheses)."""
    blocks: list[dict] = []
    blocks.extend(overlay.h03_section_blocks)
    blocks.extend(overlay.h04_section_blocks)
    blocks.extend(overlay.h05_section_blocks)
    if not blocks:
        return None
    return {
        "id": "hypothesis_latency_compliance",
        "number": 0,  # renumbered later
        "part": None,
        "title": "Statistical Inference: Cross-Category Comparison (H-03, H-04, H-05)",
        "intro": (
            "Does the agent handle all fault categories equally well? Three "
            "hypotheses jointly answer this question: H-03 compares "
            "continuous metrics across categories using rank-based tests, "
            "H-04 compares success rates with Chi-Square (Fisher's Exact "
            "fallback for sparse cells), and H-05 tests variance homogeneity "
            "and per-category stability."
        ),
        "content": blocks,
    }



# ─ COMMENTED OUT: Section 10 (SLA-Aware Hypothesis Analysis H-06 – H-09)
# def _section_hypothesis_safety_stability(overlay: HypothesisOverlay):
#     """Optional Phase E section: H-06..H-09 (SLA, tail risk, stability)."""
#     blocks: list[dict] = []
#     blocks.extend(overlay.h06_section_blocks)
#     blocks.extend(overlay.h07_section_blocks)
#     blocks.extend(overlay.h08_section_blocks)
#     blocks.extend(overlay.h09_section_blocks)
#     if not blocks:
#         return None
#     
#     # Build intro text, noting if ground truth (SLA thresholds) was not provided
#     intro_text = (
#         "This subsection activates the SLA-Aware branch of the framework. Illustrative SLA thresholds applied: "
#         "TTD ≤ 600 s, TTM ≤ 900 s, allowed breach rate ≤ 5%. H-06 proves threshold compliance with statistical confidence; "
#         "H-07 estimates the SLA breach rate against the 5% budget; H-08 quantifies tail severity; "
#         "H-09 checks for drift across the ordered run sequence."
#     )
#     
#     if not overlay.ground_truth_provided:
#         intro_text += (
#             " **Note:** No ground-truth SLA directory was provided; therefore, "
#             "H-06 (SLA Threshold Compliance) and H-07 (SLA Breach Rate) were skipped. "
#             "H-08 and H-09 were computed without SLA guidance."
#         )
#     
#     return {
#         "id": "hypothesis_safety_stability",
#         "number": 0,  # renumbered later
#         "part": None,
#         "title": "Statistical Inference: SLA-Aware Hypothesis Analysis (H-06 – H-09)",
#         "intro": intro_text,
#         "content": blocks,
#     }


# ── Meta + Header + Footer ──────────────────────────────────────────

def _build_meta(phase1):
    m = phase1["meta"]
    return {
        "agent_name": m["agent_name"],
        "agent_id": m["agent_id"],
        "certification_run_id": m.get("certification_run_id", ""),
        "certification_date": m["certification_date"],
        "subtitle": f"Resilience & Safety Evaluation \u2014 {m['agent_name']}",
        "total_runs": m["total_runs"],
        "successful_runs": m.get("successful_runs", 0),
        "failed_runs": m.get("failed_runs", 0),
        "total_faults": m["total_faults_tested"],
        "total_categories": m["total_fault_categories"],
        "runs_per_fault_configured": m["runs_per_fault"],
        "categories": m["categories_summary"],
    }


def _build_header(phase2, phase3):
    """Build header section with scorecard and key findings.
    
    DISABLED: User requested removal of header section.
    Returns None to suppress header output.
    """
    return None
    # Original code (preserved for reference):
    # scorecard = phase2["scorecard"]["dimensions"]
    # findings = [
    #     {"severity": f["severity"], "text": f"{f['headline']}: {f['detail']}"}
    #     for f in phase3["key_findings"]["items"]
    # ]
    # return {"scorecard": scorecard, "findings": findings}


def _build_footer(meta, overlay: HypothesisOverlay | None = None):
    # Footer is intentionally minimal — framework HTML has no <footer>.
    # The schema requires min_length=1, so emit a single non-breaking space.
    return "\u00a0"


# ── ReportAssembler class ──────────────────────────────────────────

class ReportAssembler:
    """Assembles Phase 1+2+3 outputs into the final CertificationReport.

    Args:
        phase1_path: path to phase1 parsed context JSON.
        phase2_path: path to phase2 computed content JSON.
        phase3_path: path to phase3 narratives JSON.
        debug: if True, write intermediate output.
    """

    def __init__(self, phase1_path, phase2_path, phase3_path, debug=False,
                 use_llm_for_overlay: bool = True):
        self.phase1_path = Path(phase1_path)
        self.phase2_path = Path(phase2_path)
        self.phase3_path = Path(phase3_path)
        self.debug = debug
        self.use_llm_for_overlay = use_llm_for_overlay

    def _build_overlay(self, phase1: dict) -> HypothesisOverlay:
        """Build the Phase E hypothesis overlay from the phase1 dict.

        ``hypothesis_view`` expects an attribute-style ``ctx``; phase1 is a
        plain dict, so we wrap it in a SimpleNamespace.
        """
        ctx = SimpleNamespace(
            statistical_hypothesis=phase1.get("statistical_hypothesis")
            or {"status": "not_requested"}
        )
        coro = build_hypothesis_overlay(ctx, use_llm=self.use_llm_for_overlay)
        try:
            # No running loop — safe to use asyncio.run.
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        # A loop is already running (e.g. orchestrator's async run_pipeline,
        # or Jupyter). Run the coroutine to completion on a private loop in
        # a worker thread so we don't nest asyncio.run().
        import threading

        result_box: dict[str, Any] = {}

        def _runner() -> None:
            try:
                result_box["overlay"] = asyncio.run(coro)
            except Exception as exc:  # noqa: BLE001 — propagate to caller
                result_box["error"] = exc

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join()
        if "error" in result_box:
            raise result_box["error"]
        return result_box["overlay"]

    def assemble(self) -> dict:
        """Merge all phases into a validated CertificationReport dict.

        Returns:
            Dict that passes CertificationReport.model_validate().
        """
        phase1 = json.loads(self.phase1_path.read_text(encoding="utf-8"))
        phase2 = json.loads(self.phase2_path.read_text(encoding="utf-8"))
        phase3 = json.loads(self.phase3_path.read_text(encoding="utf-8"))

        overlay = self._build_overlay(phase1)

        meta = _build_meta(phase1)
        # header = _build_header(phase2, phase3)  # REMOVED: User disabled header section
        footer = _build_footer(meta, overlay)

        sections: list[dict] = [
            _section_executive_summary(phase1, phase2, phase3, overlay),
            _section_methodology(phase2, overlay),
            # NOTE: Old Section 3 (Scorecard) has been removed.
            # Its content is now integrated into Section 1 as §1.3 Experiment Findings.
        ]

        # Part I banner — appears as a standalone heading-like section
        # between §3 and §4 (Agent Capability Assessment).
        # Always rendered: this is an organizational divider, not statistical
        # content, so it is decoupled from `overlay.suppressed`.
        sections.append({
            "id": "part_i_banner",
            "number": 0,
            "part": "Agent Capability Assessment",
            "title": "Part I — Agent Capability Assessment",
            "intro": "Foundational quantitative + qualitative assessment of the agent's behaviour.",
            "content": [_part_banner("Part I", "Agent Capability Assessment")],
        })

        sections.extend([
            _section_qualitative_findings(phase1, phase2, phase3),
            _section_detection_response(phase2, phase1, overlay),
            _section_reasoning(phase1, phase2, overlay),
            _section_safety(phase1, phase2, overlay),
            _section_resource(phase2),
        ])

        # Part II banner — between Part-I content and the per-category
        # fault-injection panels.
        # Always rendered: this is an organizational divider, not statistical
        # content, so it is decoupled from `overlay.suppressed`.
        sections.append({
            "id": "part_ii_banner",
            "number": 0,
            "part": "Fault Injection Analysis",
            "title": "Part II — Fault Injection Analysis",
            "intro": "Per-fault-category narrative and assessment from the LLM Council.",
            "content": [_part_banner("Part II", "Fault Injection Analysis")],
        })

        sections.append(_section_fault_analysis(phase1, phase2, phase3))

        # Part III banner — before Limitations & Recommendations
        sections.append({
            "id": "part_iii_banner",
            "number": 0,
            "part": "System Limitations & Recommendations",
            "title": "Part III \u2014 System Limitations & Recommendations",
            "intro": "",
            "content": [_part_banner("Part III", "System Limitations & Recommendations")],
        })

        sections.extend([
            _section_limitations(phase2, phase3, overlay, phase1),
            _section_recommendations(phase2, phase3, overlay, phase1),
        ])

        # Appendix — only when statistical hypothesis testing is active
        appendix = _section_appendix(overlay)
        if appendix is not None:
            # Part IV banner — before appendix sections
            sections.append({
                "id": "part_iv_banner",
                "number": 0,
                "part": "Appendix & Statistical Analysis",
                "title": "Part IV \u2014 Appendix & Statistical Analysis",
                "intro": "",
                "content": [_part_banner("Part IV", "Appendix & Statistical Analysis")],
            })
            sections.append(appendix)

        # Appendix A2 — Statistical Evidence (H-01 – H-02 strips from sections 5, 6, 7)
        appendix_a2 = _section_appendix_a2(overlay)
        if appendix_a2 is not None:
            sections.append(appendix_a2)

        # Appendix A3 — Statistical Inference: Cross-Category Comparison (H-03, H-04, H-05)
        appendix_a3 = _section_appendix_a3(overlay)
        if appendix_a3 is not None:
            sections.append(appendix_a3)

        # Renumber sections sequentially to keep them monotonic after the
        # optional Phase E injection. Banner sections (Part I / Part II) are
        # skipped — they keep number = 0 so the renderer omits a numeric
        # prefix and the visible section count stays aligned with the framework
        # HTML (which has no banners).
        running = 0
        for section in sections:
            if section.get("id", "").endswith("_banner"):
                section["number"] = 0
                continue
            running += 1
            section["number"] = running

        # Patch the "{N}" sub-heading placeholder used by the fault-category
        # analysis section (its sub-headings are e.g. "13.1 Application Faults"
        # but the parent section number is only known after global renumber).
        for section in sections:
            if section.get("id") != "fault_category_analysis":
                continue
            n = section["number"]
            for block in section.get("content", []):
                if isinstance(block, dict) and block.get("type") == "heading":
                    title = block.get("title", "")
                    if "{N}" in title:
                        block["title"] = title.replace("{N}", str(n))

        report_dict = {
            "meta": meta,
            # "header": header,  # REMOVED: User disabled header section
            "sections": sections,
            "footer": footer,
        }

        # Validate against Pydantic schema
        report = CertificationReport.model_validate(report_dict)

        # Return validated dict
        return report.model_dump(mode="json")

    def assemble_and_save(self, output_path) -> dict:
        """Assemble and write the final certification report.

        Returns:
            The validated report dict.
        """
        result = self.assemble()

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(f"[report-assembler] Wrote {output_path.name} ({output_path.stat().st_size / 1024:.1f} KB)")
        return result
