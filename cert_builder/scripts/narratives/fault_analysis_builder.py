"""
Phase 3D — Fault Category Analysis Builder.

Generates per-category analytical synthesis for Section 10. For each fault
category, produces a heading detail line and a 2-4 sentence analysis.
This is LLM Call 4 of 6 (JSON output — per-category object).

Input:  Phase 1 parsed context + Phase 2 computed content.
Output: {"fault_category_analysis": {"categories": {...}, "source": ..., "model": ..., "tokens_used": ...}}
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from cert_builder.scripts.narratives.llm_client import get_client, call_llm

# ---------------------------------------------------------------------------
# Load prompt config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "fault_analysis_prompt.yaml"
_CONFIG = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Pydantic models (intermediate — not part of certified report)
# ---------------------------------------------------------------------------

class FaultCategoryAnalysisItem(BaseModel):
    """LLM output for a single fault category."""
    label:    str = Field(..., min_length=1)
    title:    str = Field(..., min_length=1)
    detail:   str = Field(..., min_length=1)
    analysis: str = Field(..., min_length=1)


class FaultCategoryAnalysisResponse(BaseModel):
    """Schema enforced on the LLM response via structured output."""
    categories: list[FaultCategoryAnalysisItem]


class FaultCategoryAnalysisResult(BaseModel):
    """Envelope for Call 4 output."""
    categories:  dict[str, dict]
    source:      Literal["llm", "fallback"] = "llm"
    model:       str | None = None
    tokens_used: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def _build_category_context(cat: dict, phase2: dict) -> str:
    """Build context block for a single category."""
    label = cat["label"]
    fault = cat["faults_tested"][0]
    runs = cat["total_runs"]
    n = cat["numeric"]
    d = cat["derived"]
    b = cat["boolean"]
    t = cat["textual"]

    # Action correctness
    ac = n.get("action_correctness", {})
    ac_str = f"{ac['mean']:.1f}" if ac and "mean" in ac else "N/A"

    lines = [
        f"=== {label.upper()} FAULTS ===",
        f"Category:    {label}",
        f"Fault:       {fault}",
        f"Runs:        {runs}",
        "",
        "KEY METRICS:",
        f"  Detection rate:       {d['fault_detection_success_rate']*100:.0f}%",
        f"  Mitigation rate:      {d['fault_mitigation_success_rate']*100:.0f}%",
        f"  False negative rate:  {d['false_negative_rate']*100:.0f}%",
        f"  False positive rate:  {d['false_positive_rate']*100:.0f}%",
        f"  Reasoning score:      {n['reasoning_score']['mean']}/10",
        f"  Response quality:     {n['response_quality_score']['mean']}/10",
        f"  Hallucination mean:   {n['hallucination_score']['mean']}",
        f"  Hallucination max:    {n['hallucination_score']['max']}",
        f"  Action correctness:   {ac_str}",
        f"  TTD median:           {n['time_to_detect']['median']:.1f}s",
        f"  TTM median:           {n['time_to_mitigate']['median']:.1f}s",
        f"  RAI compliance:       {d['rai_compliance_rate']*100:.0f}%",
        f"  Security compliance:  {d['security_compliance_rate']*100:.0f}%",
        f"  PII detected:         {'Yes' if b['pii_detection']['any_detected'] else 'No'}",
    ]

    # LLM Council assessments
    lines.append("")
    lines.append("LLM COUNCIL ASSESSMENTS:")

    for key, heading in [
        ("agent_summary", "Agent Summary"),
        ("overall_response_and_reasoning_quality", "Response & Reasoning Quality"),
        ("security_compliance_summary", "Security Compliance"),
        ("rai_check_summary", "RAI Compliance"),
    ]:
        block = t[key]
        lines.append(f"  {heading}:")
        if "severity_label" in block:
            lines.append(f"    Rating: {block['severity_label']}")
        lines.append(f"    Confidence: {block['confidence']}, Agreement: {block['inter_judge_agreement']}")
        # Truncate long consensus summaries for prompt efficiency
        summary = block["consensus_summary"]
        if len(summary) > 300:
            summary = summary[:297] + "..."
        lines.append(f"    \"{summary}\"")

    # Limitations from phase2
    lim_rows = phase2.get("tables", {}).get("limitations", {}).get("rows", [])
    cat_lims = [r for r in lim_rows if r[2] == label]
    if cat_lims:
        lines.append("")
        lines.append("LIMITATIONS:")
        for r in cat_lims:
            lines.append(f"  - [{r[3]}] {r[1]}")

    # Recommendations from phase2
    rec_rows = phase2.get("tables", {}).get("recommendations", {}).get("rows", [])
    cat_recs = [r for r in rec_rows if r[3] == label]
    if cat_recs:
        lines.append("")
        lines.append("RECOMMENDATIONS:")
        for r in cat_recs:
            lines.append(f"  - [{r[1]}] {r[2]}")

    return "\n".join(lines)


def _build_all_contexts(phase1: dict, phase2: dict) -> str:
    """Build combined context block for all categories."""
    blocks = []
    for cat in phase1["categories"]:
        blocks.append(_build_category_context(cat, phase2))
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

def _fallback_analysis(phase1: dict) -> dict[str, dict]:
    """Deterministic fallback per category."""
    result = {}
    for cat in phase1["categories"]:
        label = cat["label"]
        fault = cat["faults_tested"][0]
        runs = cat["total_runs"]
        d = cat["derived"]
        n = cat["numeric"]
        det = int(d["fault_detection_success_rate"] * 100)
        mit = int(d["fault_mitigation_success_rate"] * 100)
        reasoning = n["reasoning_score"]["mean"]
        rq = n["response_quality_score"]["mean"]

        detail = (
            f"{fault} | {runs} runs | Detection: {det}% | Mitigation: {mit}% "
            f"| Reasoning: {reasoning}/10 | Response Quality: {rq}/10"
        )

        rating = cat["textual"]["overall_response_and_reasoning_quality"]["severity_label"]
        confidence = cat["textual"]["overall_response_and_reasoning_quality"]["confidence"]

        analysis = _CONFIG["fallback_template"].format(
            label=label, fault=fault, runs=runs,
            det_rate=det, mit_rate=mit,
            rating=rating, confidence=confidence,
        )

        result[label] = {
            "title": f"{label} Faults",
            "detail": detail,
            "analysis": analysis.strip(),
        }
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_fault_analysis(phase1: dict, phase2: dict) -> dict:
    """
    Generate per-category fault analysis.

    Returns:
        {"fault_category_analysis": {"categories": {...}, "source": ..., "model": ..., "tokens_used": ...}}
    """
    contexts_block = _build_all_contexts(phase1, phase2)
    user_prompt = _CONFIG["user_prompt_template"].format(
        category_contexts_block=contexts_block,
    )

    try:
        client = get_client()
        result = call_llm(
            client,
            _CONFIG["system_prompt"],
            user_prompt,
            response_schema=FaultCategoryAnalysisResponse,
        )

        parsed = result["content"]  # already validated Pydantic model

        # Map by position to use phase1's labels (not LLM's)
        expected_labels = [c["label"] for c in phase1["categories"]]
        cat_dict = {}
        for i, item in enumerate(parsed.categories):
            label = expected_labels[i] if i < len(expected_labels) else item.label
            cat_dict[label] = {"title": item.title, "detail": item.detail, "analysis": item.analysis}

        envelope = FaultCategoryAnalysisResult(
            categories=cat_dict,
            source="llm",
            model=result.get("model"),
            tokens_used=result.get("tokens_used", 0),
        )

    except Exception as exc:
        print(f"[phase3d] LLM call failed: {exc}")
        print("[phase3d] Using fallback analysis.")
        envelope = FaultCategoryAnalysisResult(
            categories=_fallback_analysis(phase1),
            source="fallback",
        )

    return {"fault_category_analysis": envelope.model_dump()}
