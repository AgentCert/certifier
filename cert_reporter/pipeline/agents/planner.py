"""
pipeline/agents/planner.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
LLM-driven report planner.

Takes a DomainProfile (from inspector.py) and asks an LLM to produce a
structured ReportPlan — a list of sections, each with:
  • a title
  • which JSON data paths to pull
  • what chart type(s) to use (if any)
  • instructions for the section-writer agent

The plan is the single configuration that drives the rest of the agentic
pipeline.  Because it is LLM-generated from the data inventory, no code
changes are needed when the domain or JSON structure changes.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from .inspector import DomainProfile

log = logging.getLogger(__name__)


# ── models ────────────────────────────────────────────────────────────────────

class SectionPlan(BaseModel):
    section_id: str
    section_number: int = 1
    title: str
    data_paths: list[str] = Field(default_factory=list)
    chart_hints: list[str] = Field(default_factory=list)
    narrative_instructions: str = ""


class ReportPlan(BaseModel):
    title: str = "Evaluation Report"
    subtitle: str = "Comprehensive Analysis"
    domain: str = "general"
    cert_level: str = ""
    cert_score: float = 0.0
    agent_name: str = ""
    sections: list[SectionPlan] = Field(default_factory=list)


# ── prompts ───────────────────────────────────────────────────────────────────

_PLANNER_SYSTEM = """\
You are a professional report architect specialising in technical evaluation reports.
Given a structured data inventory from an evaluation or certification document, you
create a complete report plan as a JSON object.

Rules:
- Choose 4–10 sections that best represent the data.
- Every section must reference at least one real path from the data_paths_available list.
- chart_hints use the format "<type>:<data_path>" where type is one of:
    gauge, bar, grouped_bar, heatmap, radar, stacked_bar, line
- narrative_instructions should direct a writer agent concisely (1–2 sentences).
- section_id must be snake_case.

Return ONLY a valid JSON object matching this schema — no markdown, no explanation:
{
  "title": "...",
  "subtitle": "...",
  "domain": "...",
  "cert_level": "...",
  "cert_score": <number>,
  "agent_name": "...",
  "sections": [
    {
      "section_id": "...",
      "section_number": <int>,
      "title": "...",
      "data_paths": ["..."],
      "chart_hints": ["..."],
      "narrative_instructions": "..."
    }
  ]
}
"""

_PLANNER_HUMAN = """\
Domain detected: {domain}
Document title: {title}
Agent / subject: {agent_name}
Certification level: {cert_level}  Score: {cert_score}

=== DATA INVENTORY ===

SCALAR FIELDS (single values):
{scalar_summary}

NARRATIVE FIELDS (long text):
{narrative_summary}

TABULAR DATA (rows × columns):
{table_summary}

Data paths available for sections:
{data_paths_available}

Create a technical {domain} report plan with 5–8 sections.
"""


def _build_inventory_text(profile: DomainProfile) -> dict[str, str]:
    """Format the domain profile into human-readable inventory blocks."""
    # Scalars
    scalar_lines = []
    for path, val in list(profile.scalars.items())[:30]:
        scalar_lines.append(f"  {path}: {val}")
    scalar_summary = "\n".join(scalar_lines) or "  (none)"

    # Narratives
    narrative_lines = []
    for path, text in list(profile.narratives.items())[:15]:
        preview = (text[:80] + "…") if len(text) > 80 else text
        narrative_lines.append(f"  {path} [{len(text)} chars]: {preview}")
    narrative_summary = "\n".join(narrative_lines) or "  (none)"

    # Tables
    table_lines = []
    for path, info in list(profile.tables.items())[:20]:
        num_info = f", numeric: {info.numeric_columns[:4]}" if info.numeric_columns else ""
        table_lines.append(
            f"  {path}: {info.row_count} rows, cols={info.columns[:6]}{num_info}"
        )
    table_summary = "\n".join(table_lines) or "  (none)"

    # All paths that could be referenced
    all_paths = sorted({
        f.path for f in profile.fields
        if f.field_type in {"table", "narrative", "scalar", "kv_list"}
    })[:60]
    data_paths_available = "\n".join(f"  {p}" for p in all_paths) or "  (none)"

    return {
        "scalar_summary":       scalar_summary,
        "narrative_summary":    narrative_summary,
        "table_summary":        table_summary,
        "data_paths_available": data_paths_available,
    }


def _parse_plan_from_llm(text: str, profile: DomainProfile) -> ReportPlan:
    """Extract JSON from LLM response and build a ReportPlan."""
    # Strip markdown code fences if present
    clean = re.sub(r"```(?:json)?\s*", "", text).strip()
    clean = re.sub(r"```\s*$", "", clean).strip()

    # Find the outermost { … }
    start = clean.find("{")
    end   = clean.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in LLM response")
    obj = json.loads(clean[start:end])

    plan = ReportPlan.model_validate(obj)

    # Back-fill header info from profile if LLM left them blank
    if not plan.title:
        plan.title = profile.title or "Evaluation Report"
    if not plan.agent_name:
        plan.agent_name = profile.agent_name
    if not plan.cert_level:
        plan.cert_level = profile.cert_level
    if plan.cert_score == 0.0 and profile.cert_score:
        plan.cert_score = profile.cert_score
    if not plan.domain:
        plan.domain = profile.domain

    return plan


def _fallback_plan(profile: DomainProfile) -> ReportPlan:
    """Minimal fallback if the LLM call fails or JSON is invalid."""
    sections: list[SectionPlan] = []
    num = 1

    # Executive summary from scalars
    if profile.scalars:
        sections.append(SectionPlan(
            section_id="executive_summary", section_number=num,
            title="Executive Summary",
            data_paths=list(profile.scalars.keys())[:5],
            chart_hints=(["gauge:" + list(profile.scalars.keys())[0]]
                         if profile.scalars else []),
            narrative_instructions="Summarise the key results and overall outcome.",
        ))
        num += 1

    # One section per table
    for path, info in list(profile.tables.items())[:6]:
        slug = re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")[-40:]
        chart = ("grouped_bar:" + path if info.numeric_columns else "")
        sections.append(SectionPlan(
            section_id=slug, section_number=num,
            title=path.replace("_", " ").replace(".", " › ").title(),
            data_paths=[path],
            chart_hints=[chart] if chart else [],
            narrative_instructions=f"Analyse the data in {path}.",
        ))
        num += 1

    # One section per narrative block
    for path in list(profile.narratives.keys())[:4]:
        slug = re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")[-40:]
        sections.append(SectionPlan(
            section_id=slug, section_number=num,
            title=path.replace("_", " ").replace(".", " › ").title(),
            data_paths=[path],
            chart_hints=[],
            narrative_instructions=f"Present and analyse the findings at {path}.",
        ))
        num += 1

    return ReportPlan(
        title=profile.title or "Evaluation Report",
        subtitle="Comprehensive Analysis",
        domain=profile.domain,
        cert_level=profile.cert_level,
        cert_score=profile.cert_score,
        agent_name=profile.agent_name,
        sections=sections[:8],
    )


async def _plan_async(profile: DomainProfile, llm) -> ReportPlan:
    from langchain_core.messages import HumanMessage, SystemMessage

    inventory = _build_inventory_text(profile)
    human_text = _PLANNER_HUMAN.format(
        domain=profile.domain,
        title=profile.title,
        agent_name=profile.agent_name,
        cert_level=profile.cert_level,
        cert_score=profile.cert_score,
        **inventory,
    )

    messages = [SystemMessage(content=_PLANNER_SYSTEM), HumanMessage(content=human_text)]
    try:
        response = await llm.ainvoke(messages)
        text = response.content if hasattr(response, "content") else str(response)
        return _parse_plan_from_llm(text, profile)
    except Exception as exc:
        log.warning("Planner LLM call failed (%s), using fallback plan", exc)
        return _fallback_plan(profile)


def build_report_plan(profile: DomainProfile, llm) -> ReportPlan:
    """Synchronous wrapper — runs the async planner in a new event loop."""
    import asyncio
    import threading

    result: list[Any] = []
    exc_holder: list[Exception] = []

    def _worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result.append(loop.run_until_complete(_plan_async(profile, llm)))
        except Exception as e:
            exc_holder.append(e)
        finally:
            loop.close()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join()

    if exc_holder:
        log.warning("Planner failed: %s — using fallback", exc_holder[0])
        return _fallback_plan(profile)
    return result[0] if result else _fallback_plan(profile)
