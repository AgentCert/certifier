"""
Phase 3C — Qualitative Findings Builder.

Synthesizes cross-category qualitative findings across all 7 evaluation
dimensions. This is LLM Call 3 of 6 (JSON output — 7-key object).

Input:  Phase 1 parsed context + Phase 2 computed content.
Output: {"qualitative_findings": {"detection": [...], ..., "source": ..., "model": ..., "tokens_used": ...}}
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from cert_builder.schema.intermediate import (
    QualitativeFinding,
    QualitativeSynthesis,
    QualitativeSynthesisResponse,
)
from cert_builder.scripts.narratives.llm_client import get_client, call_llm

try:
    from aggregator.scripts.rai_scoring import privacy_security_for_category
except ImportError:
    def privacy_security_for_category(derived):
        d = derived or {}
        def _f(v, default=1.0):
            try: return float(v) if v is not None else default
            except Exception: return default
        return round(_f(d.get("security_compliance_rate")) * _f(d.get("pii_clean_rate")) * _f(d.get("adversarial_clean_rate")), 4)

# ---------------------------------------------------------------------------
# Load prompt config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "qualitative_prompt.yaml"
_CONFIG = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))

_DIMENSIONS = [
    "detection", "mitigation", "action_correctness",
    "reasoning", "safety", "hallucination", "security",
]


def _stat(c: dict, metric: str, sub: str, fmt: str = "{:.1f}", default: str = "N/A") -> str:
    """Safely format a numeric stat from a category's metric block.

    The aggregator omits a metric block entirely when no run produced the
    underlying value (e.g. ``time_to_detect`` is absent if no run ever
    detected the fault); blind ``c["numeric"][metric][sub]`` then KeyErrors.
    """
    block = (c.get("numeric") or {}).get(metric) or {}
    v = block.get(sub)
    return fmt.format(v) if isinstance(v, (int, float)) else default


def _bool(c: dict, metric: str, sub: str, default=None):
    """Safely fetch a boolean-block value (e.g. hallucination_detection.any_detected)."""
    return ((c.get("boolean") or {}).get(metric) or {}).get(sub, default)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
# ``QualitativeFinding``, ``QualitativeSynthesisResponse`` and
# ``QualitativeSynthesis`` are defined in
# ``cert_builder.schema.intermediate`` so every intermediate / LLM-facing
# Pydantic model in cert_builder lives in one place. They are re-exported
# below for backwards compatibility with any external import that already
# does ``from cert_builder.scripts.narratives.qualitative_builder import
# QualitativeSynthesis``.
__all__ = [
    "QualitativeFinding",
    "QualitativeSynthesisResponse",
    "QualitativeSynthesis",
    "build_qualitative_findings",
]


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

_CISO_SHAPED_CATEGORIES = {"ciso_fault"}


def _is_ciso(cat: dict) -> bool:
    return cat.get("fault_category", "") in _CISO_SHAPED_CATEGORIES


def _build_qualitative_context(phase1: dict, phase2: dict) -> str:
    """Build the 7-dimension context block for the LLM prompt."""
    cats = phase1["categories"]
    sre_cats = [c for c in cats if not _is_ciso(c)]
    meta = phase1.get("meta", {})
    scorecard = phase2["scorecard"]["dimensions"]
    sc_map = {d["dimension"]: d["value"] for d in scorecard}

    # The ONLY run count narratives may quote: number of distinct successful
    # trace runs (= meta.successful_runs at the top level). Per-fault sample
    # sizes (e.g. 62 for resource = 31 runs × 2 fault types) drive Wilson CIs
    # but must NOT be exposed to narrative LLMs as "runs".
    distinct_total = meta.get("successful_runs", 0) or sum(
        c.get("distinct_runs", c.get("total_runs", 0)) for c in cats
    )

    lines = ["QUALITATIVE SYNTHESIS CONTEXT - ALL 7 DIMENSIONS\n"]
    lines.append(
        f"Trace runs: {meta.get('total_runs', 0)} attempted, "
        f"{meta.get('successful_runs', 0)} successful, "
        f"{meta.get('failed_runs', 0)} failed. "
        f"Always frame counts as \"X successful runs\" (never as evaluations or samples).\n"
    )

    # 1. Detection
    lines.append("=== 1. DETECTION PERFORMANCE ===\n")
    lines.append("Per-category detection metrics (SRE categories):")
    for c in sre_cats:
        det = c["derived"]["fault_detection_success_rate"]
        cat_runs = c.get("distinct_runs", c.get("total_runs", 0))
        ttd_cat = ((c.get("numeric") or {}).get("time_to_detect") or {}).get("category", {}) or {}
        sla_pct_cat = ttd_cat.get("sla_compliance")
        sla_str = f"{sla_pct_cat*100:.0f}%" if sla_pct_cat is not None else "N/A"
        lines.append(
            f"  {c['label']} [{cat_runs} successful runs]: "
            f"detection_rate={det*100:.0f}%, "
            f"TTD SLA met={sla_str}, "
            f"TTD mean={_stat(c, 'time_to_detect', 'mean')}s, "
            f"median={_stat(c, 'time_to_detect', 'median')}s, "
            f"std={_stat(c, 'time_to_detect', 'std_dev')}s, "
            f"P95={_stat(c, 'time_to_detect', 'p95')}s"
        )
        # Subfault TTD breakdown
        ttd_sf = ((c.get("numeric") or {}).get("time_to_detect") or {}).get("subfault", {})
        if ttd_sf:
            for sf, td in sorted(ttd_sf.items()):
                sla = td.get("sla_seconds")
                sla_pct = td.get("sla_compliance")
                sla_pct_str = f", SLA met={sla_pct*100:.0f}%" if sla_pct is not None else ""
                lines.append(
                    f"    {sf}: TTD score={td.get('weighted_score', 'N/A')}, "
                    f"det_rate={td.get('detection_rate', 'N/A')}, "
                    f"n={td.get('n_attempted', 'N/A')}"
                    + (f", SLA={sla}s" if sla else "")
                    + sla_pct_str
                )
    lines.append(f"\nScorecard: Detection Rate = {sc_map.get('Detection Rate', 'N/A')}")
    # Compute weighted overall but expose only as percentage with run-level framing.
    eval_total = sum(c["total_runs"] for c in sre_cats)
    det_count = sum(int(c["derived"]["fault_detection_success_rate"] * c["total_runs"]) for c in sre_cats)
    overall_det = (det_count / eval_total * 100) if eval_total else 0
    lines.append(
        f"Overall detection rate: {overall_det:.1f}% "
        f"(across {distinct_total} successful runs, SRE categories only)\n"
    )

    # 2. Mitigation
    lines.append("=== 2. MITIGATION PERFORMANCE ===\n")
    lines.append("Per-category mitigation metrics (SRE categories):")
    for c in sre_cats:
        mit = c["derived"]["fault_mitigation_success_rate"]
        fp = c["derived"]["false_positive_rate"]
        cat_runs = c.get("distinct_runs", c.get("total_runs", 0))
        ttm_cat = ((c.get("numeric") or {}).get("time_to_mitigate") or {}).get("category", {}) or {}
        sla_pct_cat = ttm_cat.get("sla_compliance")
        sla_str = f"{sla_pct_cat*100:.0f}%" if sla_pct_cat is not None else "N/A"
        lines.append(
            f"  {c['label']} [{cat_runs} successful runs]: "
            f"mitigation_rate={mit*100:.0f}%, false_pos={fp*100:.0f}%, "
            f"TTM SLA met={sla_str}, "
            f"TTM mean={_stat(c, 'time_to_mitigate', 'mean')}s, "
            f"median={_stat(c, 'time_to_mitigate', 'median')}s, "
            f"std={_stat(c, 'time_to_mitigate', 'std_dev')}s"
        )
        # Subfault TTM breakdown
        ttm_sf = ((c.get("numeric") or {}).get("time_to_mitigate") or {}).get("subfault", {})
        if ttm_sf:
            for sf, tm in sorted(ttm_sf.items()):
                sla = tm.get("sla_seconds")
                sla_pct = tm.get("sla_compliance")
                sla_pct_str = f", SLA met={sla_pct*100:.0f}%" if sla_pct is not None else ""
                lines.append(
                    f"    {sf}: TTM score={tm.get('weighted_score', 'N/A')}, "
                    f"mit_rate={tm.get('detection_rate', 'N/A')}, "
                    f"n={tm.get('n_attempted', 'N/A')}"
                    + (f", SLA={sla}s" if sla else "")
                    + sla_pct_str
                )
    lines.append(f"\nScorecard: Mitigation Rate = {sc_map.get('Mitigation Rate', 'N/A')}")
    mit_count = sum(int(c["derived"]["fault_mitigation_success_rate"] * c["total_runs"]) for c in cats)
    overall_mit = (mit_count / eval_total * 100) if eval_total else 0
    lines.append(
        f"Overall mitigation rate: {overall_mit:.0f}% "
        f"(across {distinct_total} successful runs)\n"
    )

    # 3. Action Correctness
    lines.append("=== 3. ACTION CORRECTNESS ===\n")
    lines.append("Per-category action correctness:")
    for c in cats:
        ac = c["numeric"].get("action_correctness", {})
        if ac and "mean" in ac:
            lines.append(f"  {c['label']}: mean={ac['mean']:.1f}")
        else:
            lines.append(f"  {c['label']}: N/A (not individually instrumented)")
    lines.append(f"\nScorecard: Action Correctness = {sc_map.get('Action Correctness', 'N/A')}\n")

    # 4. Reasoning & Response Quality
    lines.append("=== 4. REASONING & RESPONSE QUALITY ===\n")
    lines.append("Per-category LLM Council consensus (reasoning assessment):")
    for c in cats:
        t = c["textual"]["overall_response_and_reasoning_quality"]
        lines.append(
            f"  {c['label']}: Rating={t['severity_label']}, "
            f"Confidence={t['confidence']}, Agreement={t['inter_judge_agreement']}"
        )
    lines.append("\nNumeric scores:")
    for c in cats:
        lines.append(
            f"  {c['label']}: "
            f"reasoning={_stat(c, 'reasoning_score', 'mean', '{:.2f}')}"
        )
    lines.append(f"Scorecard: Reasoning Quality = {sc_map.get('Reasoning Quality', 'N/A')}\n")

    # 5. Safety (RAI)
    lines.append("=== 5. SAFETY (RAI COMPLIANCE) ===\n")
    lines.append("Per-category LLM Council consensus (RAI assessment):")
    for c in cats:
        t = c["textual"]["rai_check_summary"]
        lines.append(
            f"  {c['label']}: Rating={t['severity_label']}, "
            f"Confidence={t['confidence']}, Agreement={t['inter_judge_agreement']}"
        )
    rai_line = ", ".join(
        f"{c['label']}: PS_RAI={privacy_security_for_category(c['derived'])*100:.0f}%, "
        f"FairnessPass={c['derived']['rai_compliance_rate']*100:.0f}%"
        for c in cats
    )
    lines.append(f"\nPer-category: {rai_line}")
    lines.append("(PS_RAI = real per-category Privacy & Security score; FairnessPass = fraction of runs where fairness_check_status == Passed, informational only — NOT the RAI/Safety score.)")
    lines.append(f"Scorecard: Safety (RAI) = {sc_map.get('Safety (RAI)', 'N/A')}\n")

    # 6. Hallucination
    lines.append("=== 6. HALLUCINATION CONTROL ===\n")
    lines.append("Per-category LLM Council consensus (hallucination assessment):")
    for c in cats:
        t = (c.get("textual") or {}).get("hallucination_notes") or {}
        lines.append(
            f"  {c['label']}: Rating={t.get('severity_label', 'N/A')}, "
            f"Confidence={t.get('confidence', 'N/A')}, "
            f"Agreement={t.get('inter_judge_agreement', 'N/A')}"
        )
    lines.append("\nNumeric scores:")
    clean_cats = 0
    max_score = 0.0
    for c in cats:
        h = (c.get("numeric") or {}).get("hallucination_score") or {}
        det_flag = _bool(c, "hallucination_detection", "any_detected", False)
        lines.append(
            f"  {c['label']}: "
            f"mean={_stat(c, 'hallucination_score', 'mean', '{:.3f}')}, "
            f"max={_stat(c, 'hallucination_score', 'max', '{:.2f}')}, "
            f"detected={'Yes' if det_flag else 'No'}"
        )
        h_max = h.get("max")
        if not det_flag and (not isinstance(h_max, (int, float)) or h_max == 0):
            clean_cats += 1
        if isinstance(h_max, (int, float)):
            max_score = max(max_score, h_max)
    if clean_cats == len(cats) and cats:
        lines.append(
            f"\nNo hallucinations were observed in any of the {distinct_total} successful runs; "
            f"highest score = {max_score:.2f}."
        )
    else:
        lines.append(
            f"\nHallucinations observed in {len(cats) - clean_cats} of {len(cats)} categories; "
            f"highest score = {max_score:.2f}."
        )

    # Per-category consensus notes — the WHAT (concrete fabrications) behind the scores
    notes_lines = []
    for c in cats:
        hn = (c.get("textual") or {}).get("hallucination_notes") or {}
        summary = (hn.get("consensus_summary") or "").strip()
        if summary and summary != "Not evaluated.":
            notes_lines.append(f"  {c['label']}: {summary}")
    if notes_lines:
        lines.append("\nPer-category hallucination evidence (Council consensus of per-run notes):")
        lines.extend(notes_lines)
    lines.append(f"Scorecard: Hallucination Ctrl = {sc_map.get('Hallucination Ctrl', 'N/A')}\n")

    # 7. Security
    lines.append("=== 7. SECURITY COMPLIANCE ===\n")
    lines.append("Per-category LLM Council consensus (security assessment):")
    for c in cats:
        t = c["textual"]["security_compliance_summary"]
        lines.append(
            f"  {c['label']}: Rating={t['severity_label']}, "
            f"Confidence={t['confidence']}, Agreement={t['inter_judge_agreement']}"
        )
    sec_line = ", ".join(f"{c['label']}={c['derived']['security_compliance_rate']*100:.0f}%" for c in cats)
    lines.append(f"\nSecurity rates: {sec_line}")
    pii_line = ", ".join(
        f"{c['label']}={'Yes' if (c['boolean'].get('pii_detection') or c['boolean'].get('personal_pii') or {}).get('any_detected') else 'No'}" for c in cats
    )
    lines.append(f"PII detected: {pii_line}")
    # Sensitive exposure counts
    pii_counts = []
    for c in cats:
        pii_data = (c.get("numeric") or {}).get("sensitive_exposure", {})
        if pii_data and "sum" in pii_data:
            pii_counts.append(f"{c['label']}={pii_data['sum']:.0f}")
    if pii_counts:
        lines.append(f"Sensitive exposure totals: {', '.join(pii_counts)}")
    # Token usage
    tok_parts = []
    for c in cats:
        inp = (c.get("numeric") or {}).get("input_tokens", {}).get("mean")
        out = (c.get("numeric") or {}).get("output_tokens", {}).get("mean")
        if isinstance(inp, (int, float)) or isinstance(out, (int, float)):
            inp_s = f"{inp:.0f}" if isinstance(inp, (int, float)) else "N/A"
            out_s = f"{out:.0f}" if isinstance(out, (int, float)) else "N/A"
            tok_parts.append(f"{c['label']}: inp={inp_s}, out={out_s}")
    if tok_parts:
        lines.append(f"Token usage (mean): {'; '.join(tok_parts)}")
    lines.append(f"Scorecard: Security = {sc_map.get('Security', 'N/A')}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

def _fallback_findings(phase1: dict) -> dict:
    """Rule-based fallback findings per dimension."""
    cats = phase1["categories"]
    sre_cats = [c for c in cats if not _is_ciso(c)]
    meta = phase1.get("meta", {})
    distinct_total = meta.get("successful_runs", 0) or sum(
        c.get("distinct_runs", c.get("total_runs", 0)) for c in cats
    )
    eval_total = sum(c["total_runs"] for c in sre_cats) or 1
    det_count = sum(int(c["derived"]["fault_detection_success_rate"] * c["total_runs"]) for c in sre_cats)
    overall_det = det_count / eval_total * 100

    result = {}

    # Detection
    items = []
    if overall_det < 50:
        items.append({"severity": "concern", "headline": "Low detection rate",
                       "detail": f"Overall detection rate is {overall_det:.1f}% across {distinct_total} successful runs."})
    ttd_means = [
        ((c.get("numeric") or {}).get("time_to_detect") or {}).get("mean")
        for c in cats
    ]
    ttd_means = [t for t in ttd_means if isinstance(t, (int, float))]
    if ttd_means and any(t > 300 for t in ttd_means):
        items.append({"severity": "note", "headline": "Slow detection",
                       "detail": f"Mean TTD ranges from {min(ttd_means):.0f}s to {max(ttd_means):.0f}s."})
    result["detection"] = items or [{"severity": "note", "headline": "Detection reviewed", "detail": "Detection metrics reviewed."}]

    # Mitigation
    mit_rates = [c["derived"]["fault_mitigation_success_rate"] for c in cats]
    if all(r == 1.0 for r in mit_rates):
        result["mitigation"] = [{"severity": "good", "headline": "Perfect mitigation",
                                  "detail": f"100% mitigation rate across all {len(cats)} categories."}]
    else:
        result["mitigation"] = [{"severity": "note", "headline": "Mitigation reviewed",
                                  "detail": "Mitigation metrics reviewed."}]

    # Action Correctness
    ac_items = []
    for c in cats:
        ac = c["numeric"].get("action_correctness", {})
        if ac and "mean" in ac and ac["mean"] == 1.0:
            ac_items.append({"severity": "good", "headline": "Perfect correctness",
                              "detail": f"{c['label']} scored 1.0."})
            break
    ac_items.append({"severity": "note", "headline": "Limited coverage",
                      "detail": "Not all categories have action correctness instrumentation."})
    result["action_correctness"] = ac_items

    # Reasoning
    ratings = [c["textual"]["overall_response_and_reasoning_quality"]["severity_label"] for c in cats]
    if all(r == "Strong" for r in ratings):
        result["reasoning"] = [{"severity": "good", "headline": "Consistently strong reasoning",
                                 "detail": "All categories rated Strong with high confidence."}]
    else:
        result["reasoning"] = [{"severity": "note", "headline": "Reasoning reviewed",
                                 "detail": "Reasoning quality reviewed."}]

    # Safety — uses real per-category Privacy & Security (RAI), NOT fairness_check_pass_rate
    ps_rates = [privacy_security_for_category(c["derived"]) for c in cats]
    if all(r == 1.0 for r in ps_rates):
        result["safety"] = [{"severity": "good", "headline": "Full Privacy & Security compliance",
                              "detail": "100% Privacy & Security (RAI) score across all categories — no PII exposure, no adversarial input compromises, no security gate failures."}]
    else:
        min_r = min(ps_rates) if ps_rates else 0
        weakest = next((c["label"] for c, r in zip(cats, ps_rates) if r == min_r), "unknown")
        breakdown = ", ".join("{lbl}={pct:.0f}%".format(lbl=c["label"], pct=r * 100) for c, r in zip(cats, ps_rates))
        result["safety"] = [{
            "severity": "note",
            "headline": "Privacy & Security reviewed",
            "detail": f"Per-category Privacy & Security (RAI) scores: {breakdown}. Weakest: {weakest} at {min_r*100:.0f}%."
        }]

    # Hallucination — mirrors the Reasoning fallback: read severity_label
    # produced by the Phase 2 LLM Council consensus over per-run hallucination_notes.
    h_ratings = [
        ((c.get("textual") or {}).get("hallucination_notes") or {}).get("severity_label")
        for c in cats
    ]
    if h_ratings and all(r == "Strong" for r in h_ratings):
        result["hallucination"] = [{"severity": "good", "headline": "Consistently grounded reasoning",
                                     "detail": "All categories rated Strong by the LLM Council — claims grounded in observed evidence."}]
    else:
        result["hallucination"] = [{"severity": "note", "headline": "Hallucination reviewed",
                                     "detail": "Hallucination evidence reviewed; see per-category Council consensus and numeric scores."}]

    # Security
    sec_rates = [c["derived"]["security_compliance_rate"] for c in cats]
    if all(r == 1.0 for r in sec_rates):
        result["security"] = [{"severity": "good", "headline": "Full security compliance",
                                "detail": "100% security compliance, zero PII detected."}]
    else:
        result["security"] = [{"severity": "note", "headline": "Security reviewed",
                                "detail": "Security compliance reviewed."}]

    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_qualitative_findings(phase1: dict, phase2: dict) -> dict:
    """
    Synthesize qualitative findings across 7 dimensions.

    Returns:
        {"qualitative_findings": {"detection": [...], ..., "source": ..., "model": ..., "tokens_used": ...}}
    """
    context_block = _build_qualitative_context(phase1, phase2)
    user_prompt = _CONFIG["user_prompt_template"].format(
        qualitative_context_block=context_block,
    )

    try:
        client = get_client()
        result = call_llm(
            client,
            _CONFIG["system_prompt"],
            user_prompt,
            response_schema=QualitativeSynthesisResponse,
        )

        parsed = result["content"]  # already validated Pydantic model

        synthesis = QualitativeSynthesis(
            **{dim: getattr(parsed, dim) for dim in _DIMENSIONS},
            source="llm",
            model=result.get("model"),
            tokens_used=result.get("tokens_used", 0),
            input_tokens=result.get("input_tokens", 0),
            output_tokens=result.get("output_tokens", 0),
        )
        _scrub_kn_fractions(synthesis)

    except Exception as exc:
        print(f"[phase3c] LLM call failed: {exc}")
        print("[phase3c] Using fallback findings.")
        fb = _fallback_findings(phase1)
        synthesis = QualitativeSynthesis(
            **{dim: fb[dim] for dim in _DIMENSIONS},
            source="fallback",
        )

    return {"qualitative_findings": synthesis.model_dump(mode="json")}


# ---------------------------------------------------------------------------
# Output sanitizer
# ---------------------------------------------------------------------------

# Strip parenthetical / inline K/N fractions the LLM may emit despite the
# prompt forbidding them. We only target "X/Y" where Y is a small integer
# (matches per-fault-evaluation counts up to 999) — Wilson CIs and percentages
# are left untouched.
_FRACTION_RE = re.compile(r"\s*\(?\s*\d{1,3}\s*/\s*\d{1,3}(?:,\s*\d{1,3}\.\d%?)?\s*\)?")
_FRACTION_INLINE_RE = re.compile(r"\b\d{1,3}\s*/\s*\d{1,3}\b")


def _scrub_text(text: str) -> str:
    """Remove K/N fractions from a single narrative string."""
    if not text:
        return text
    # Remove patterns like " (62/62)", " (31/31, 100.0%)", " (0/62)"
    cleaned = _FRACTION_RE.sub("", text)
    # Catch inline fractions not in parens, e.g. "0/31 and 0/62"
    cleaned = _FRACTION_INLINE_RE.sub("", cleaned)
    # Tidy double spaces / orphan punctuation
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def _scrub_kn_fractions(synthesis: "QualitativeSynthesis") -> None:
    """In-place scrub of K/N fractions in all qualitative finding details."""
    for dim in _DIMENSIONS:
        items = getattr(synthesis, dim) or []
        for item in items:
            if hasattr(item, "detail") and isinstance(item.detail, str):
                item.detail = _scrub_text(item.detail)
