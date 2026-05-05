"""
pipeline/agents/section_writer.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
LLM-driven section enrichment — adds intro paragraphs to existing sections.

Updated for the canonical certification framework format.
Sections now have content blocks (heading, text, table, findings, assessment,
card, chart) instead of subsections.

The writer:
  1. Builds a factual data summary from content blocks.
  2. Calls the LLM to write a section introduction paragraph.
  3. All original content blocks are preserved unchanged.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

log = logging.getLogger(__name__)


# ── LLM prompts ───────────────────────────────────────────────────────────────

_ENRICH_SYSTEM = """\
You are a senior technical writer composing section introductions for a formal \
evaluation or certification report.
Write a concise, professional 2-4 sentence introduction for the section.
Base your writing ONLY on the data summary provided — do not invent facts.
No markdown, no bullet points — plain prose only.
"""

_ENRICH_HUMAN = """\
Section title: {title}
Domain: {domain}

Data contained in this section:
{data_summary}

Write the section introduction paragraph.
"""


# ── data summary from content blocks ──────────────────────────────────────────

def _section_data_summary(section: dict[str, Any], max_chars: int = 800) -> str:
    """
    Build a concise text description of what's inside a section's content
    blocks so the LLM can write a grounded introduction.
    """
    lines: list[str] = []

    for block in section.get("content") or []:
        if not isinstance(block, dict):
            if hasattr(block, "model_dump"):
                block = block.model_dump(mode="python")
            else:
                continue

        block_type = block.get("type", "")

        if block_type == "heading":
            title = block.get("title", "")
            detail = block.get("detail", "")
            lines.append(f"Heading: {title}" + (f" — {detail}" if detail else ""))

        elif block_type == "text":
            body = block.get("body", "")
            lines.append(f"Text: {body[:200]}")

        elif block_type == "table":
            t_title = block.get("title", "table")
            headers = block.get("headers", [])
            rows = block.get("rows", [])
            lines.append(f"Table '{t_title}': {len(rows)} rows, columns: {headers[:6]}")
            if rows:
                # Show first row as sample
                sample = rows[0] if isinstance(rows[0], list) else rows[0]
                lines.append(f"  Sample row: {sample}")

        elif block_type == "findings":
            items = block.get("items", [])
            for item in items[:3]:
                if isinstance(item, dict):
                    sev = item.get("severity", "")
                    if hasattr(sev, "value"):
                        sev = sev.value
                    lines.append(f"  Finding ({sev}): {item.get('text', '')[:100]}")

        elif block_type == "assessment":
            title = block.get("title", "")
            rating = block.get("rating", "")
            if hasattr(rating, "value"):
                rating = rating.value
            body = block.get("body", "")
            lines.append(f"Assessment '{title}': rating={rating}, {body[:150]}")

        elif block_type == "card":
            items = block.get("items", [])
            card_title = block.get("title", "card")
            pairs = []
            for item in items[:6]:
                if isinstance(item, dict):
                    pairs.append(f"{item.get('label', '?')}={item.get('value', '?')}")
            lines.append(f"Card '{card_title}': {', '.join(pairs)}")

        elif block_type == "chart":
            chart_type = block.get("chart_type", "")
            chart_title = block.get("title", "")
            lines.append(f"Chart ({chart_type}): {chart_title}")

    result = "\n".join(lines)
    return result[:max_chars] if result else "(no detailed data available)"


# ── async core ────────────────────────────────────────────────────────────────

async def _enrich_section_async(
    section: dict[str, Any],
    domain: str,
    llm,
) -> dict[str, Any]:
    """
    Add an LLM-generated introduction to an existing section.
    All original content blocks are preserved unchanged.
    """
    data_summary = _section_data_summary(section)
    intro_text = section.get("intro") or ""

    if llm is not None and not intro_text:
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            messages = [
                SystemMessage(content=_ENRICH_SYSTEM),
                HumanMessage(content=_ENRICH_HUMAN.format(
                    title=section.get("title") or "",
                    domain=domain,
                    data_summary=data_summary,
                )),
            ]
            response = await llm.ainvoke(messages)
            intro_text = (
                response.content if hasattr(response, "content") else str(response)
            ).strip()
        except Exception as exc:
            log.debug("LLM intro failed for section %s: %s",
                      section.get("id", "?"), exc)

    enriched = dict(section)
    enriched["intro"] = intro_text
    return enriched


# ── public synchronous wrapper ────────────────────────────────────────────────

def enrich_section(
    section: dict[str, Any],
    domain: str = "general",
    llm=None,
) -> dict[str, Any]:
    """
    Synchronous wrapper for _enrich_section_async.

    Takes an existing section dict and returns a copy with `intro`
    filled in by the LLM. Content blocks are untouched.
    """
    result: list[dict] = []
    exc_holder: list[Exception] = []

    def _worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result.append(
                loop.run_until_complete(
                    _enrich_section_async(section, domain, llm)
                )
            )
        except Exception as e:
            exc_holder.append(e)
        finally:
            loop.close()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join()

    if exc_holder:
        log.warning("enrich_section error for %s: %s",
                    section.get("id", "?"), exc_holder[0])
    return result[0] if result else dict(section)
