"""
Phase 3F — Fairness Score Builder.

Scores cross-category consistency (Fairness principle) on a 1-10 scale
using aggregated TTD/TTM metrics and hypothesis test results (H-03/H-04).

Input:  Phase 1 parsed context + Phase 2 computed content.
Output: {"fairness_score": {"fairness_score": int, "fairness_label": str,
                            "reasoning": str, "weakest_category": str|None,
                            "confidence": str, "source": str, ...}}
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field

from cert_builder.scripts.narratives.llm_client import get_client, call_llm

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "fairness_scoring_prompt.yaml"
_CONFIG = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Pydantic schema
# ---------------------------------------------------------------------------

class FairnessScoreResponse(BaseModel):
    fairness_score: float = Field(..., ge=0.0, le=1.0)
    fairness_label: Literal["Excellent", "Good", "Adequate", "Weak"]
    reasoning: str = Field(..., min_length=10)
    weakest_category: Optional[str] = None
    confidence: Literal["High", "Medium", "Low"]


class FairnessScoreResult(BaseModel):
    fairness_score: float = Field(ge=0.0, le=1.0)
    fairness_label: Literal["Excellent", "Good", "Adequate", "Weak"]
    reasoning: str
    weakest_category: Optional[str] = None
    confidence: Literal["High", "Medium", "Low"]
    source: Literal["llm", "fallback"] = "llm"
    model: Optional[str] = None
    tokens_used: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def _build_category_summary(phase1: dict) -> str:
    cats = phase1.get("categories", [])
    if not cats:
        return "No per-category data available."

    lines = ["PER-CATEGORY METRICS:"]
    for c in cats:
        label = c.get("label", "Unknown")
        derived = c.get("derived", {})
        numeric = c.get("numeric", {})
        distinct_runs = c.get("distinct_runs", c.get("total_runs", 0))

        det_rate = derived.get("fault_detection_success_rate", 0)
        mit_rate = derived.get("fault_mitigation_success_rate", 0)

        ttd = numeric.get("time_to_detect", {}) or {}
        ttm = numeric.get("time_to_mitigate", {}) or {}
        reasoning = numeric.get("reasoning_score", {}) or {}

        ttd_mean = ttd.get("mean")
        ttd_median = ttd.get("median")
        ttm_mean = ttm.get("mean")
        ttm_median = ttm.get("median")
        reasoning_mean = reasoning.get("mean")

        def _fmt(v, suffix="s"):
            return f"{v:.1f}{suffix}" if isinstance(v, (int, float)) else "N/A"

        lines.append(
            f"  {label} [{distinct_runs} runs]: "
            f"detection_rate={det_rate*100:.0f}%, "
            f"mitigation_rate={mit_rate*100:.0f}%, "
            f"TTD mean={_fmt(ttd_mean)}, median={_fmt(ttd_median)}, "
            f"TTM mean={_fmt(ttm_mean)}, median={_fmt(ttm_median)}, "
            f"reasoning={_fmt(reasoning_mean, '')}"
        )

        # Sub-fault breakdown if available
        ttd_sf = ttd.get("subfault", {})
        if ttd_sf:
            for sf, td in sorted(ttd_sf.items()):
                lines.append(
                    f"    subfault {sf}: TTD_score={td.get('weighted_score', 'N/A')}, "
                    f"det_rate={td.get('detection_rate', 'N/A')}"
                )

    return "\n".join(lines)


def _build_hypothesis_summary(phase1: dict) -> str:
    sh = (phase1.get("meta") or {}).get("statistical_hypothesis", {})
    if not sh or sh.get("status") != "ok":
        return "HYPOTHESIS TESTS: Not available (advanced analysis not requested)."

    results = sh.get("results", {})
    lines = ["HYPOTHESIS TEST FINDINGS:"]

    # H-03: Cross-category TTD/TTM comparison
    h03 = results.get("H-03") or results.get("h03") or results.get("h_03")
    if h03:
        sig = h03.get("omnibus_significant", False)
        p = h03.get("omnibus_p", 1.0)
        test = h03.get("test_used", "kruskal_wallis")
        assessment = h03.get("overall_assessment", "")
        lines.append(
            f"  H-03 (Cross-Category Performance): "
            f"{'SIGNIFICANT' if sig else 'not significant'} "
            f"({test}, p={p:.4f})"
        )
        if assessment:
            lines.append(f"    Assessment: {assessment}")
        pairwise = h03.get("pairwise", [])
        sig_pairs = [p for p in pairwise if (p.get("significant") if isinstance(p, dict) else getattr(p, "significant", False))]
        if sig_pairs:
            for pw in sig_pairs[:3]:
                pair = pw.get("pair", "") if isinstance(pw, dict) else getattr(pw, "pair", "")
                a12 = pw.get("a12", 0.5) if isinstance(pw, dict) else getattr(pw, "a12", 0.5)
                mag = pw.get("effect_magnitude", "") if isinstance(pw, dict) else getattr(pw, "effect_magnitude", "")
                lines.append(f"    Pairwise: {pair}, effect={mag}, A12={a12:.2f}")

    # H-04: Cross-category success rate uniformity
    h04 = results.get("H-04") or results.get("h04") or results.get("h_04")
    if h04:
        sig = h04.get("significant", False)
        p = h04.get("p_value", 1.0)
        weakest = h04.get("weakest_category", "")
        assessment = h04.get("overall_assessment", "")
        per_cat = h04.get("per_category_rates", {})
        lines.append(
            f"  H-04 (Success Rate Uniformity): "
            f"{'SIGNIFICANT' if sig else 'not significant'} "
            f"(p={p:.4f})"
            + (f", weakest={weakest}" if weakest else "")
        )
        if per_cat:
            cat_rates = ", ".join(f"{k}={v*100:.0f}%" for k, v in per_cat.items())
            lines.append(f"    Per-category rates: {cat_rates}")
        if assessment:
            lines.append(f"    Assessment: {assessment}")

    if len(lines) == 1:
        lines.append("  No relevant hypothesis results found in the results block.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

def _fallback_score(phase1: dict) -> FairnessScoreResult:
    cats = phase1.get("categories", [])
    if not cats:
        return FairnessScoreResult(
            fairness_score=0.5,
            fairness_label="Adequate",
            reasoning="Insufficient data to assess cross-category consistency.",
            confidence="Low",
            source="fallback",
        )

    det_rates = [c.get("derived", {}).get("fault_detection_success_rate", 0) for c in cats]
    spread = max(det_rates) - min(det_rates) if det_rates else 0

    if spread <= 0.05:
        score, label = 0.9, "Excellent"
    elif spread <= 0.15:
        score, label = 0.7, "Good"
    elif spread <= 0.30:
        score, label = 0.5, "Adequate"
    else:
        score, label = 0.3, "Weak"

    weakest = min(cats, key=lambda c: c.get("derived", {}).get("fault_detection_success_rate", 0))
    return FairnessScoreResult(
        fairness_score=score,
        fairness_label=label,
        reasoning=(
            f"Cross-category detection rate spread is {spread*100:.0f} percentage points. "
            f"Score assigned by rule-based fallback."
        ),
        weakest_category=weakest.get("label"),
        confidence="Low",
        source="fallback",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_fairness_score(phase1: dict, phase2: dict) -> dict:
    """Score cross-category fairness using LLM on a 1-10 scale.

    Returns:
        {"fairness_score": {fairness_score, fairness_label, reasoning,
                            weakest_category, confidence, source, model,
                            tokens_used, input_tokens, output_tokens}}
    """
    category_summary = _build_category_summary(phase1)
    hypothesis_summary = _build_hypothesis_summary(phase1)

    user_prompt = _CONFIG["user_prompt_template"].format(
        category_summary=category_summary,
        hypothesis_summary=hypothesis_summary,
    )

    try:
        client = get_client()
        result = call_llm(
            client,
            _CONFIG["system_prompt"],
            user_prompt,
            response_schema=FairnessScoreResponse,
        )
        parsed: FairnessScoreResponse = result["content"]
        output = FairnessScoreResult(
            fairness_score=parsed.fairness_score,
            fairness_label=parsed.fairness_label,
            reasoning=parsed.reasoning,
            weakest_category=parsed.weakest_category,
            confidence=parsed.confidence,
            source="llm",
            model=result.get("model"),
            tokens_used=result.get("tokens_used", 0),
            input_tokens=result.get("input_tokens", 0),
            output_tokens=result.get("output_tokens", 0),
        )
    except Exception as exc:
        print(f"[phase3f] LLM fairness score failed: {exc}")
        print("[phase3f] Using fallback fairness score.")
        output = _fallback_score(phase1)

    return {"fairness_score": output.model_dump(mode="json")}
