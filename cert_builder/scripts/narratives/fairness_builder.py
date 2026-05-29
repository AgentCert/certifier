"""
Phase 3F — Fairness Score Builder.

Scores cross-category consistency (Fairness principle) on a 0.0-1.0 scale
using aggregated TTD/TTM metrics and hypothesis test results (H-03/H-04).

Score resolution order (first available wins for the numeric score):
  1. ``hypothesis``  — deterministic score computed from H-03 / H-04 results
                       when statistical_hypothesis is present. The LLM still
                       generates the executive-tone reasoning narrative.
  2. ``llm``         — LLM-judged score from the rubric (no hypothesis tests
                       available, e.g. advanced analysis was not requested).
  3. ``fallback``    — rule-based detection-rate-spread score (LLM call failed
                       AND no hypothesis tests available).

Input:  Phase 1 parsed context + Phase 2 computed content.
Output: {"fairness_score": {"fairness_score": float, "fairness_label": str,
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
    source: Literal["llm", "fallback", "hypothesis"] = "llm"
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
# Deterministic hypothesis-based scoring (H-03 / H-04)
# ---------------------------------------------------------------------------

# Scoring constants — kept in one block so they are easy to tune.
_H03_A12_PENALTY_WEIGHT = 0.80   # multiplier on |A12 - 0.5| for sig. H-03
_H03_MAX_PENALTY = 0.40          # cap on H-03 contribution
_H04_SIG_PENALTY_WEIGHT = 1.00   # multiplier on rate spread for sig. H-04
_H04_NONSIG_PENALTY_WEIGHT = 0.30
_H04_MAX_SIG_PENALTY = 0.50
_H04_MAX_NONSIG_PENALTY = 0.15
_HARD_FLOOR_SEVERE_RATE = 0.10   # any category < 10% detection → score ≤ 0.20
_HARD_FLOOR_SEVERE_CAP = 0.20
_HARD_FLOOR_WEAK_RATE = 0.30     # any category < 30% detection → score ≤ 0.40
_HARD_FLOOR_WEAK_CAP = 0.40


def _score_to_label(score: float) -> Literal["Excellent", "Good", "Adequate", "Weak"]:
    if score >= 0.9:
        return "Excellent"
    if score >= 0.7:
        return "Good"
    if score >= 0.5:
        return "Adequate"
    return "Weak"


def _get_attr(obj, key, default=None):
    """Read a field from a dict or a Pydantic-like object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _compute_hypothesis_based_score(phase1: dict) -> Optional[dict]:
    """Compute the Fairness score deterministically from H-03 / H-04 results.

    Returns ``None`` when neither hypothesis is available with usable data
    (e.g. advanced analysis was not requested) so the caller can fall through
    to the LLM-judged path.

    Scoring model (additive penalties from a baseline of 1.0):
      * **H-03** — cross-category TTD/TTM (Kruskal-Wallis + A12 effect sizes).
        If the omnibus is significant, penalty is the largest pairwise
        ``|A12 - 0.5|`` (range 0.0-0.5) times ``_H03_A12_PENALTY_WEIGHT``,
        capped at ``_H03_MAX_PENALTY``. Non-significant → 0.
      * **H-04** — cross-category success-rate uniformity. Penalty is the
        ``max-min`` rate spread times a significance-aware weight, capped.

    Hard floors after additive penalties:
      * Any category detection rate < 10% → score capped at 0.20.
      * Any category detection rate < 30% → score capped at 0.40.

    The weakest category is taken from H-04's ``weakest_category`` field,
    falling back to ``argmin(per_category_rates)``.
    """
    sh = (phase1.get("meta") or {}).get("statistical_hypothesis", {})
    if not sh or sh.get("status") != "ok":
        return None
    results = sh.get("results", {}) or {}

    h03 = results.get("H-03") or results.get("h03") or results.get("h_03")
    h04 = results.get("H-04") or results.get("h04") or results.get("h_04")
    if not h03 and not h04:
        return None

    penalties: list[float] = []
    basis_parts: list[str] = []
    weakest: Optional[str] = None

    # ── H-03: TTD/TTM cross-category effect ──────────────────────────────
    if h03:
        if _get_attr(h03, "omnibus_significant", False):
            pairwise = _get_attr(h03, "pairwise", []) or []
            sig_devs: list[float] = []
            for pw in pairwise:
                if not _get_attr(pw, "significant", False):
                    continue
                a12 = _get_attr(pw, "a12", None)
                if isinstance(a12, (int, float)):
                    sig_devs.append(abs(float(a12) - 0.5))
            max_dev = max(sig_devs) if sig_devs else 0.0
            h03_pen = min(max_dev * _H03_A12_PENALTY_WEIGHT, _H03_MAX_PENALTY)
            penalties.append(h03_pen)
            basis_parts.append(
                f"H-03 omnibus significant (largest pairwise |A12-0.5|={max_dev:.2f}) "
                f"→ -{h03_pen:.2f}"
            )
        else:
            basis_parts.append("H-03 omnibus not significant → 0 penalty")

    # ── H-04: success-rate uniformity ────────────────────────────────────
    per_rates: dict = {}
    if h04:
        per_rates_raw = _get_attr(h04, "per_category_rates", {}) or {}
        # Coerce to plain {str: float}
        per_rates = {
            str(k): float(v)
            for k, v in per_rates_raw.items()
            if isinstance(v, (int, float))
        }
        spread = (max(per_rates.values()) - min(per_rates.values())) if per_rates else 0.0
        sig = _get_attr(h04, "significant", False)
        if sig:
            h04_pen = min(spread * _H04_SIG_PENALTY_WEIGHT, _H04_MAX_SIG_PENALTY)
        else:
            h04_pen = min(spread * _H04_NONSIG_PENALTY_WEIGHT, _H04_MAX_NONSIG_PENALTY)
        penalties.append(h04_pen)
        basis_parts.append(
            f"H-04 {'significant' if sig else 'not significant'} "
            f"(rate spread {spread * 100:.0f}pp) → -{h04_pen:.2f}"
        )
        weakest = _get_attr(h04, "weakest_category", None) or None
        if not weakest and per_rates:
            weakest = min(per_rates.items(), key=lambda kv: kv[1])[0]

    if not penalties:
        return None

    score = max(0.0, 1.0 - sum(penalties))

    # Hard floors for extreme per-category weakness.
    if per_rates:
        min_rate = min(per_rates.values())
        if min_rate < _HARD_FLOOR_SEVERE_RATE and score > _HARD_FLOOR_SEVERE_CAP:
            score = _HARD_FLOOR_SEVERE_CAP
            basis_parts.append(
                f"Hard cap applied: weakest category rate {min_rate * 100:.0f}% "
                f"< {_HARD_FLOOR_SEVERE_RATE * 100:.0f}% → score ≤ {_HARD_FLOOR_SEVERE_CAP:.2f}"
            )
        elif min_rate < _HARD_FLOOR_WEAK_RATE and score > _HARD_FLOOR_WEAK_CAP:
            score = _HARD_FLOOR_WEAK_CAP
            basis_parts.append(
                f"Hard cap applied: weakest category rate {min_rate * 100:.0f}% "
                f"< {_HARD_FLOOR_WEAK_RATE * 100:.0f}% → score ≤ {_HARD_FLOOR_WEAK_CAP:.2f}"
            )

    score = round(score, 2)
    return {
        "score": score,
        "label": _score_to_label(score),
        "weakest_category": weakest,
        "basis": "; ".join(basis_parts),
    }


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
    """Score cross-category fairness on a 0.0-1.0 scale.

    Resolution order for the numeric score:
      1. Deterministic computation from H-03 / H-04 (when available).
         The LLM still generates the executive-tone reasoning narrative;
         only the numeric score and label are overridden.
      2. LLM-judged score using the rubric in fairness_scoring_prompt.yaml.
      3. Rule-based fallback (LLM failure path).

    Returns:
        {"fairness_score": {fairness_score, fairness_label, reasoning,
                            weakest_category, confidence, source, model,
                            tokens_used, input_tokens, output_tokens}}
    """
    category_summary = _build_category_summary(phase1)
    hypothesis_summary = _build_hypothesis_summary(phase1)

    # Compute the deterministic anchor first — used to override the LLM's
    # numeric score whenever the underlying hypothesis tests are present.
    hyp_result = _compute_hypothesis_based_score(phase1)

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
        if hyp_result is not None:
            # Hypothesis tests present → the numeric score & label are
            # deterministic. Keep the LLM's reasoning narrative so the
            # executive-tone explanation is preserved.
            output = FairnessScoreResult(
                fairness_score=hyp_result["score"],
                fairness_label=hyp_result["label"],
                reasoning=parsed.reasoning,
                weakest_category=hyp_result["weakest_category"] or parsed.weakest_category,
                confidence="High",
                source="hypothesis",
                model=result.get("model"),
                tokens_used=result.get("tokens_used", 0),
                input_tokens=result.get("input_tokens", 0),
                output_tokens=result.get("output_tokens", 0),
            )
        else:
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
        if hyp_result is not None:
            # LLM unavailable but hypothesis tests are present — emit the
            # deterministic score with a synthesized basis as the reasoning.
            print("[phase3f] Using deterministic H-03/H-04 fairness score (no LLM narrative).")
            output = FairnessScoreResult(
                fairness_score=hyp_result["score"],
                fairness_label=hyp_result["label"],
                reasoning=(
                    "Cross-category fairness scored from statistical hypothesis tests "
                    f"(H-03 / H-04). {hyp_result['basis']}."
                ),
                weakest_category=hyp_result["weakest_category"],
                confidence="High",
                source="hypothesis",
            )
        else:
            print("[phase3f] Using rule-based fallback fairness score.")
            output = _fallback_score(phase1)

    return {"fairness_score": output.model_dump(mode="json")}
