"""preprocess_node — loads and normalises the certification JSON document.

Updated for the canonical certification framework format:
  meta / header / sections (with typed content blocks) / footer

Chart blocks are extracted from section content for rendering by charts_node.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .parameters import GraphState, TokenUsage
from .schema import normalise_document

log = logging.getLogger(__name__)


def _extract_chart_blocks(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Walk all sections, find chart content blocks, assign unique IDs."""
    charts: list[dict[str, Any]] = []
    for section in sections:
        sid = section.get("id", "")
        for i, block in enumerate(section.get("content", [])):
            if not isinstance(block, dict):
                # Pydantic model objects — convert to dict
                if hasattr(block, "model_dump"):
                    block = block.model_dump(mode="python")
                    section["content"][i] = block
                elif hasattr(block, "__dict__"):
                    block = dict(block.__dict__)
                    section["content"][i] = block
                else:
                    continue

            if block.get("type") == "chart":
                chart_type = block.get("chart_type", "unknown")
                chart_id = f"{sid}_{chart_type}_{i}"
                block["_chart_id"] = chart_id
                charts.append(block)
    return charts


def _ensure_dicts(sections: list) -> list[dict[str, Any]]:
    """Ensure sections and their content blocks are plain dicts."""
    result = []
    for sec in sections:
        if hasattr(sec, "model_dump"):
            sec = sec.model_dump(mode="python")
        elif not isinstance(sec, dict):
            continue
        else:
            sec = dict(sec)

        # Convert content block models to dicts
        content = sec.get("content", [])
        clean_content = []
        for block in content:
            if hasattr(block, "model_dump"):
                clean_content.append(block.model_dump(mode="python"))
            elif isinstance(block, dict):
                clean_content.append(block)
            elif hasattr(block, "__dict__"):
                clean_content.append(dict(block.__dict__))
        sec["content"] = clean_content
        result.append(sec)
    return result


def preprocess_node(state: GraphState) -> GraphState:
    """
    Load the JSON file, normalise it through the Pydantic schema, then
    extract chart blocks from section content for rendering.
    """
    input_path = state.get("input_path", "")
    if not input_path:
        return {**state, "errors": state.get("errors", []) + ["preprocess_node: input_path not set"]}

    verbose = state.get("verbose", False)
    if verbose:
        log.info("preprocess_node: loading %s", input_path)

    try:
        with open(input_path, "r", encoding="utf-8") as fh:
            raw: dict[str, Any] = json.load(fh)
    except Exception as exc:
        return {**state, "errors": state.get("errors", []) + [f"Failed to load JSON: {exc}"]}

    # ── Normalise through schema ───────────────────────────────────────────
    doc = normalise_document(raw, schema_class=state.get("schema_class"))

    meta: dict[str, Any] = doc.get("meta") or {}
    header: dict[str, Any] = doc.get("header") or {}
    sections_raw = doc.get("sections") or []
    footer: str = doc.get("footer") or ""

    # Convert Pydantic models to plain dicts for Jinja2 compatibility
    if hasattr(meta, "model_dump"):
        meta = meta.model_dump(mode="python")
    if hasattr(header, "model_dump"):
        header = header.model_dump(mode="python")

    sections = _ensure_dicts(sections_raw)

    # Extract chart blocks from content for the chart rendering pipeline
    charts_to_render = _extract_chart_blocks(sections)

    if verbose:
        log.info(
            "preprocess_node: %d sections, %d chart blocks extracted",
            len(sections),
            len(charts_to_render),
        )

    return {
        **state,
        "raw_doc": raw,
        "meta": meta,
        "header": header,
        "sections": sections,
        "footer": footer,
        "charts_to_render": charts_to_render,
        # Initialise downstream fields
        "chart_results": {},
        "enriched_sections": {},
        "html_path": "",
        "pdf_path": "",
        "token_usage": state.get("token_usage") or TokenUsage(),
        "errors": state.get("errors", []),
    }
