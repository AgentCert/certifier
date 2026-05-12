"""llm_enrich_node — optionally rewrites narrative fields using an LLM.

Updated for the canonical certification framework format.
Enriches:
  - section.intro
  - text blocks (body field)
  - assessment blocks (body field)
"""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any

from .parameters import GraphState, TokenUsage

log = logging.getLogger(__name__)


def _make_llm(config):
    """Instantiate a LangChain LLM from LLMConfig."""
    provider = getattr(config, "provider", "openai")
    model = getattr(config, "model", "gpt-4.1-mini")
    temperature = getattr(config, "temperature", 0.4)
    max_tokens = getattr(config, "max_tokens", 4096)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, temperature=temperature, max_tokens=max_tokens)

    # Default: OpenAI
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=model, temperature=temperature, max_tokens=max_tokens)


async def _call_llm(llm, system: str, human: str) -> tuple[str, int, int]:
    """Async LLM call; returns (text, input_tokens, output_tokens)."""
    from langchain_core.messages import HumanMessage, SystemMessage
    messages = [SystemMessage(content=system), HumanMessage(content=human)]
    try:
        response = await llm.ainvoke(messages)
        text = response.content if hasattr(response, "content") else str(response)
        usage = getattr(response, "usage_metadata", None)
        inp = getattr(usage, "input_tokens", 0) if usage else 0
        out = getattr(usage, "output_tokens", 0) if usage else 0
        return text, inp, out
    except Exception as exc:
        log.warning("LLM call failed: %s", exc)
        return human, 0, 0   # fall back to original text


_SYSTEM_PROMPT = """\
You are a professional technical writer specialising in AI system evaluation and certification reports.
Your task is to refine narrative text extracted from a structured certification document.
Preserve all factual content, scores, and data references exactly.
Improve sentence flow, clarity, and professional tone.
Do not add new claims, opinions, or information not present in the source text.
Return only the improved text — no preamble, no explanation.
"""

_SECTION_INTRO_PROMPT = """\
Original section introduction for "{section_title}":
---
{text}
---
Rewrite the above as a polished two-to-four sentence introduction paragraph.
"""

_TEXT_BLOCK_PROMPT = """\
Original text block from section "{section_title}":
---
{text}
---
Rewrite the above for clarity and professional tone. Keep all factual details intact.
"""

_ASSESSMENT_BLOCK_PROMPT = """\
Original assessment titled "{block_title}" (rating: {rating}):
---
{text}
---
Rewrite the above for clarity and professional tone. Keep all factual details intact.
"""


async def _enrich_sections(
    sections: list[dict[str, Any]],
    llm_config,
) -> tuple[dict[str, dict[str, Any]], TokenUsage]:
    """Run LLM enrichment concurrently across narrative fields in content blocks."""
    llm = _make_llm(llm_config)
    token_usage = TokenUsage()
    enriched: dict[str, dict[str, Any]] = {}

    async def enrich_text(text: str, human_prompt: str) -> str:
        if not text or not text.strip():
            return text
        result, inp, out = await _call_llm(llm, _SYSTEM_PROMPT, human_prompt)
        token_usage.add(inp, out)
        return result

    tasks = []
    # (section_id, field, block_index_or_None)
    keys: list[tuple[str, str, int | None]] = []

    sections_copy = copy.deepcopy(sections)

    for section in sections_copy:
        sid = section.get("id", "")
        title = section.get("title", "")
        intro = section.get("intro", "")

        # Section intro
        if intro:
            human = _SECTION_INTRO_PROMPT.format(section_title=title, text=intro)
            tasks.append(enrich_text(intro, human))
            keys.append((sid, "intro", None))

        # Content blocks: enrich text and assessment blocks
        for idx, block in enumerate(section.get("content", [])):
            if not isinstance(block, dict):
                continue

            block_type = block.get("type", "")

            if block_type == "text":
                body = block.get("body", "")
                if body and len(body) >= 50:
                    human = _TEXT_BLOCK_PROMPT.format(section_title=title, text=body)
                    tasks.append(enrich_text(body, human))
                    keys.append((sid, "content_body", idx))

            elif block_type == "assessment":
                body = block.get("body", "")
                if body and len(body) >= 50:
                    rating = block.get("rating", "")
                    if hasattr(rating, "value"):
                        rating = rating.value
                    human = _ASSESSMENT_BLOCK_PROMPT.format(
                        block_title=block.get("title", title),
                        rating=rating or "N/A",
                        text=body,
                    )
                    tasks.append(enrich_text(body, human))
                    keys.append((sid, "content_body", idx))

    # Run all tasks concurrently
    results = await asyncio.gather(*tasks)

    # Apply results back
    for i, key in enumerate(keys):
        if i >= len(results):
            break
        sid, field, block_idx = key
        new_text = results[i]

        target = next((s for s in sections_copy if s.get("id") == sid), None)
        if target is None:
            continue

        if field == "intro":
            target["intro"] = new_text
        elif field == "content_body" and block_idx is not None:
            content = target.get("content", [])
            if block_idx < len(content) and isinstance(content[block_idx], dict):
                content[block_idx]["body"] = new_text

    for section in sections_copy:
        enriched[section.get("id", "")] = section

    return enriched, token_usage


def llm_enrich_node(state: GraphState) -> GraphState:
    """Run LLM narrative enrichment if enrich_llm is True."""
    if not state.get("enrich_llm", False):
        return state

    verbose = state.get("verbose", False)
    if verbose:
        log.info("llm_enrich_node: enriching narratives with LLM")

    try:
        enriched, new_tokens = asyncio.run(
            _enrich_sections(
                sections=state.get("sections", []),
                llm_config=state.get("llm_config"),
            )
        )
        existing = state.get("token_usage", TokenUsage())
        existing.add(new_tokens.input_tokens, new_tokens.output_tokens)
        if verbose:
            log.info("llm_enrich_node: %d tokens used", new_tokens.total)
        return {**state, "enriched_sections": enriched, "token_usage": existing}
    except Exception as exc:
        log.error("llm_enrich_node failed: %s", exc)
        errors = state.get("errors", []) + [f"LLM enrichment failed: {exc}"]
        return {**state, "errors": errors}
