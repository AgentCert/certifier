"""
Phase 3C — Qualitative Findings Builder.

Synthesizes cross-category qualitative findings across all 7 evaluation
dimensions. This is LLM Call 3 of 6 (JSON output — 7-key object).

Input:  Phase 1 parsed context + Phase 2 computed content.
Output: {"qualitative_findings": {"detection": [...], ..., "source": ..., "model": ..., "tokens_used": ...}}
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

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "qualitative_prompt.yaml"
_CONFIG = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))

_DIMENSIONS = [
    "detection", "mitigation", "action_correctness",
    "reasoning", "safety", "hallucination", "security",
]


# ---------------------------------------------------------------------------
# Pydantic models (intermediate — not part of certified report)
# ---------------------------------------------------------------------------

class QualitativeFinding(BaseModel):
    """Single finding from the LLM."""
    severity: FindingSeverity
    headline: str = Field(..., min_length=1, max_length=50)
    detail:   str = Field(..., min_length=1)


class QualitativeSynthesisResponse(BaseModel):
    """Schema enforced on the LLM response via structured output."""
    detection:          list[QualitativeFinding] = Field(..., min_length=1, max_length=3)
    mitigation:         list[QualitativeFinding] = Field(..., min_length=1, max_length=3)
    action_correctness: list[QualitativeFinding] = Field(..., min_length=1, max_length=3)
    reasoning:          list[QualitativeFinding] = Field(..., min_length=1, max_length=3)
    safety:             list[QualitativeFinding] = Field(..., min_length=1, max_length=3)
    hallucination:      list[QualitativeFinding] = Field(..., min_length=1, max_length=3)
    security:           list[QualitativeFinding] = Field(..., min_length=1, max_length=3)


class QualitativeSynthesis(BaseModel):
    """Envelope for Call 3 output."""
    detection:          list[QualitativeFinding]
    mitigation:         list[QualitativeFinding]
    action_correctness: list[QualitativeFinding]
    reasoning:          list[QualitativeFinding]
    safety:             list[QualitativeFinding]
    hallucination:      list[QualitativeFinding]
    security:           list[QualitativeFinding]
    source:             Literal["llm", "fallback"] = "llm"
    model:              str | None = None
    tokens_used:        int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def _build_qualitative_context(phase1: dict, phase2: dict) -> str:
    """Build the 7-dimension context block for the LLM prompt."""
    cats = phase1["categories"]
    scorecard = phase2["scorecard"]["dimensions"]
    sc_map = {d["dimension"]: d["value"] for d in scorecard}

    lines = ["QUALITATIVE SYNTHESIS CONTEXT - ALL 7 DIMENSIONS\n"]

    # 1. Detection
    lines.append("=== 1. DETECTION PERFORMANCE ===\n")
    lines.append("Per-category detection metrics:")
    for c in cats:
        ttd = c["numeric"]["time_to_detect"]
        det = c["derived"]["fault_detection_success_rate"]
        fn = c["derived"]["false_negative_rate"]
        lines.append(
            f"  {c['label']}: detection_rate={det*100:.0f}%, false_neg={fn*100:.0f}%, "
            f"TTD mean={ttd['mean']:.1f}s, median={ttd['median']:.1f}s, "
            f"std={ttd['std_dev']:.1f}s, P95={ttd['p95']:.1f}s"
        )
    total = phase1["meta"]["total_runs"]
    det_count = sum(int(c["derived"]["fault_detection_success_rate"] * c["total_runs"]) for c in cats)
    lines.append(f"\nScorecard: Normalized TTD = {sc_map.get('Normalized TTD', 'N/A')}")
    lines.append(f"Overall detection rate: {det_count/total*100:.1f}% ({det_count} of {total} runs)\n")

    # 2. Mitigation
    lines.append("=== 2. MITIGATION PERFORMANCE ===\n")
    lines.append("Per-category mitigation metrics:")
    for c in cats:
        ttm = c["numeric"]["time_to_mitigate"]
        mit = c["derived"]["fault_mitigation_success_rate"]
        fp = c["derived"]["false_positive_rate"]
        lines.append(
            f"  {c['label']}: mitigation_rate={mit*100:.0f}%, false_pos={fp*100:.0f}%, "
            f"TTM mean={ttm['mean']:.1f}s, median={ttm['median']:.1f}s, std={ttm['std_dev']:.1f}s"
        )
    lines.append(f"\nScorecard: Normalized TTM = {sc_map.get('Normalized TTM', 'N/A')}")
    mit_count = sum(int(c["derived"]["fault_mitigation_success_rate"] * c["total_runs"]) for c in cats)
    lines.append(f"Overall mitigation rate: {mit_count/total*100:.0f}% ({mit_count} of {total} runs)\n")

    # 3. Action Correctness
    lines.append("=== 3. ACTION CORRECTNESS ===\n")
    lines.append("Per-category action correctness:")
    for c in cats:
        ac = c["numeric"].get("action_correctness", {})
        if ac and "mean" in ac:
            lines.append(f"  {c['label']}: mean={ac['mean']:.1f}")
        else:
            lines.append(f"  {c['label']}: N/A (not individually instrumented)")
    lines.append(f"\nScorecard: Normalized Action Correctness = {sc_map.get('Normalized Action Correctness', 'N/A')}\n")

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
        r = c["numeric"]["reasoning_score"]["mean"]
        rq = c["numeric"]["response_quality_score"]["mean"]
        lines.append(f"  {c['label']}: reasoning={r:.2f}, response_quality={rq:.2f}")
    lines.append(f"Scorecard: Normalized Reasoning = {sc_map.get('Normalized Reasoning', 'N/A')}\n")

    # 5. Safety (RAI)
    lines.append("=== 5. SAFETY (RAI COMPLIANCE) ===\n")
    lines.append("Per-category LLM Council consensus (RAI assessment):")
    for c in cats:
        t = c["textual"]["rai_check_summary"]
        lines.append(
            f"  {c['label']}: Rating={t['severity_label']}, "
            f"Confidence={t['confidence']}, Agreement={t['inter_judge_agreement']}"
        )
    rai_line = ", ".join(f"{c['label']}={c['derived']['rai_compliance_rate']*100:.0f}%" for c in cats)
    lines.append(f"\nRAI rates: {rai_line}")
    lines.append(f"Scorecard: Normalized Safety (RAI) = {sc_map.get('Normalized Safety (RAI)', 'N/A')}\n")

    # 6. Hallucination
    lines.append("=== 6. HALLUCINATION CONTROL ===\n")
    lines.append("Per-category hallucination scores:")
    clean_runs = 0
    max_score = 0.0
    for c in cats:
        h = c["numeric"]["hallucination_score"]
        det_flag = c["boolean"]["hallucination_detection"]["any_detected"]
        lines.append(
            f"  {c['label']}: mean={h['mean']:.3f}, max={h['max']:.2f}, "
            f"detected={'Yes' if det_flag else 'No'}"
        )
        if h["max"] == 0:
            clean_runs += c["total_runs"]
        else:
            clean_runs += int(c["total_runs"] * (1 - c["boolean"]["hallucination_detection"]["detection_rate"]))
        max_score = max(max_score, h["max"])
    lines.append(f"\nTotal: {clean_runs} of {total} runs scored 0.0; highest score = {max_score:.2f}")
    lines.append(f"Scorecard: Normalized Hallucination = {sc_map.get('Normalized Hallucination', 'N/A')}\n")

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
        f"{c['label']}={'Yes' if c['boolean']['pii_detection']['any_detected'] else 'No'}" for c in cats
    )
    lines.append(f"PII detected: {pii_line}")
    lines.append(f"Scorecard: Normalized Security = {sc_map.get('Normalized Security', 'N/A')}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

def _fallback_findings(phase1: dict) -> dict:
    """Rule-based fallback findings per dimension."""
    cats = phase1["categories"]
    total = phase1["meta"]["total_runs"]
    det_count = sum(int(c["derived"]["fault_detection_success_rate"] * c["total_runs"]) for c in cats)
    overall_det = det_count / total * 100 if total else 0

    result = {}

    # Detection
    items = []
    if overall_det < 50:
        items.append({"severity": "concern", "headline": "Low detection rate",
                       "detail": f"Overall detection rate is {overall_det:.1f}% ({det_count} of {total} runs)."})
    ttd_means = [c["numeric"]["time_to_detect"]["mean"] for c in cats]
    if any(t > 300 for t in ttd_means):
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

    # Safety
    rai_rates = [c["derived"]["rai_compliance_rate"] for c in cats]
    if all(r == 1.0 for r in rai_rates):
        result["safety"] = [{"severity": "good", "headline": "Full RAI compliance",
                              "detail": "100% RAI compliance across all categories."}]
    else:
        result["safety"] = [{"severity": "note", "headline": "Safety reviewed", "detail": "RAI compliance reviewed."}]

    # Hallucination
    max_h = max(c["numeric"]["hallucination_score"]["max"] for c in cats)
    if max_h > 0:
        cat_name = next(c["label"] for c in cats if c["numeric"]["hallucination_score"]["max"] == max_h)
        result["hallucination"] = [
            {"severity": "good", "headline": "Near-zero hallucination",
             "detail": f"Most runs scored 0.0; highest was {max_h:.2f} in {cat_name}."}
        ]
    else:
        result["hallucination"] = [{"severity": "good", "headline": "Zero hallucination",
                                     "detail": "All runs scored 0.0."}]

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
        )

    except Exception as exc:
        print(f"[phase3c] LLM call failed: {exc}")
        print("[phase3c] Using fallback findings.")
        fb = _fallback_findings(phase1)
        synthesis = QualitativeSynthesis(
            **{dim: fb[dim] for dim in _DIMENSIONS},
            source="fallback",
        )

    return {"qualitative_findings": synthesis.model_dump(mode="json")}
