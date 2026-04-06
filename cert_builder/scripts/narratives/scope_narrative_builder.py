"""
Phase 3A — Scope Narrative Builder.

Generates a 3-5 sentence executive scope paragraph for Section 1 of
the certification report. This is LLM Call 1 of 6.

Input:  Phase 1 parsed context (meta + categories_summary).
Output: {"scope_narrative": {"text": ..., "source": ..., "model": ..., "tokens_used": ...}}
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

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "scope_narrative_prompt.yaml"
_CONFIG = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Pydantic models (intermediate — not part of certified report)
# ---------------------------------------------------------------------------

class ScopeNarrativeResponse(BaseModel):
    """Schema enforced on the LLM response via structured output."""
    text: str = Field(..., min_length=1)


class ScopeNarrative(BaseModel):
    """Envelope for Call 1 output. Maps to sections[0].intro (str)."""
    text:        str = Field(..., min_length=1)
    source:      Literal["llm", "fallback"] = "llm"
    model:       str | None = None
    tokens_used: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def _build_scope_context(meta: dict) -> str:
    """Assemble the SCOPE CONTEXT block from phase1 meta."""
    cats = meta.get("categories_summary", [])
    cat_lines = []
    for c in cats:
        cat_lines.append(
            f"    - {c['name']:14s} {c['fault']:20s} ({c['runs']} runs)"
        )
    cat_block = "\n".join(cat_lines)

    return (
        f"Agent:             {meta['agent_name']}\n"
        f"Agent ID:          {meta['agent_id']}\n"
        f"Date:              {meta['certification_date']}\n"
        f"Categories:        {meta['total_fault_categories']}\n"
        f"{cat_block}\n"
        f"Total Runs:        {meta['total_runs']}\n"
        f"Total Faults:      {meta['total_faults_tested']}\n"
        f"Runs per Fault:    {meta['runs_per_fault']}\n"
        f"Evaluation Method: Multi-judge LLM Council\n"
        f"                   k=3 judges + meta-reconciliation"
    )


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

def _fallback_narrative(meta: dict) -> str:
    """Template-based fallback when the LLM call fails."""
    cats = meta.get("categories_summary", [])
    return _CONFIG["fallback_template"].format(
        agent_name=meta["agent_name"],
        n_categories=meta["total_fault_categories"],
        category_list=", ".join(c["name"] for c in cats),
        total_runs=meta["total_runs"],
        total_faults=meta["total_faults_tested"],
        fault_list=", ".join(c["fault"] for c in cats),
        runs_per_fault=meta["runs_per_fault"],
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_scope_narrative(phase1: dict) -> dict:
    """
    Generate the scope narrative for Section 1.

    Args:
        phase1: Phase 1 parsed context dict (must contain "meta").

    Returns:
        {"scope_narrative": {"text": ..., "source": ..., "model": ..., "tokens_used": ...}}
    """
    meta = phase1["meta"]
    scope_context = _build_scope_context(meta)
    user_prompt = _CONFIG["user_prompt_template"].format(
        scope_context_block=scope_context,
    )

    try:
        client = get_client()
        result = call_llm(
            client,
            _CONFIG["system_prompt"],
            user_prompt,
            response_schema=ScopeNarrativeResponse,
        )

        parsed = result["content"]  # already validated Pydantic model
        narrative = ScopeNarrative(
            text=parsed.text,
            source="llm",
            model=result.get("model"),
            tokens_used=result.get("tokens_used", 0),
        )

    except Exception as exc:
        print(f"[phase3a] LLM call failed: {exc}")
        print("[phase3a] Using fallback narrative.")
        narrative = ScopeNarrative(
            text=_fallback_narrative(meta),
            source="fallback",
        )

    return {"scope_narrative": narrative.model_dump()}
