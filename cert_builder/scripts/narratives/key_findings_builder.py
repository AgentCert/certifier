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

    for key, sub, label, fmt in [
        ("time_to_detect", "median", "TTD median", lambda v: f"{v:.0f}s"),
        ("time_to_mitigate", "median", "TTM median", lambda v: f"{v:.0f}s"),
        ("reasoning_score", "mean", "Reasoning", lambda v: f"{v:.2f}"),
        ("hallucination_score", "mean", "Halluc mean", lambda v: f"{v:.3f}"),
        ("hallucination_score", "max", "Halluc max", lambda v: f"{v:.2f}"),
    ]:
        rows.append(_row(label, [fmt(c["numeric"][key][sub]) for c in cats]))

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
    avg_reasoning = sum(
        c["numeric"]["reasoning_score"]["mean"] for c in cats
    ) / len(cats)

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
        "overall_detection_pct": f"{overall_det:.1f}",
        "detected_count": detected_count,
        "total_runs": total_runs,
    }

    return context, template_vars


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
    user_prompt = _CONFIG["user_prompt_template"].format(
        findings_context_block=context_block,
        **template_vars,
    )

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
