"""
pipeline/agentic_graph.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
LangGraph AGENTIC pipeline — domain-agnostic, no data loss, no hallucination.

Updated for the canonical certification framework format:
  meta / header / sections (content blocks) / footer

Design contract
---------------
- Every field in the source JSON is displayed in the report (zero data loss).
- The LLM is used ONLY to write per-section introduction paragraphs.
  It never decides what data to show or invents figures.
- Works for any certification domain without code changes.

Architecture
------------
START
  |
  v
preprocess          reader.py — load JSON, normalise, parse sections with content blocks
  |
  v
charts              charts.py — render every chart block to SVG
  |
  v
inspect             inspector.py — detect domain for LLM context
  |  [Send() fan-out — one task per section, run in parallel]
  +-- enrich_section_node
  +-- enrich_section_node    section_writer.enrich_section() x N
  +-- enrich_section_node    adds LLM intro; content blocks untouched
      |
      v  (merged by reducer into agentic_sections)
assemble            replace state["sections"] with enriched versions
  |
  v
html_render         html_renderer.py — Jinja2 rendering
  |
  v
pdf_render          pdf_renderer.py — Playwright PDF
  |
  v
END
"""
from __future__ import annotations

import logging
import operator
from typing import Annotated, Any

from typing_extensions import TypedDict
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .parameters import ChartResult, LLMConfig, TokenUsage
from .reader import preprocess_node
from .charts import charts_node
from .html_renderer import html_renderer_node
from .pdf_renderer import pdf_renderer_node
from .agents.inspector import DomainProfile, inspect_document
from .agents.section_writer import enrich_section

log = logging.getLogger(__name__)


# ── state schema ──────────────────────────────────────────────────────────────

class AgenticState(TypedDict, total=False):
    # ── Inputs ──────────────────────────────────────────────────────────────
    input_path:  str
    output_dir:  str
    formats:     list[str]
    enrich_llm:  bool
    llm_config:  LLMConfig
    verbose:     bool

    # ── Populated by preprocess_node ────────────────────────────────────────
    raw_doc:          dict[str, Any]
    meta:             dict[str, Any]
    header:           dict[str, Any]
    sections:         list[dict[str, Any]]
    footer:           str
    charts_to_render: list[dict[str, Any]]

    # ── Populated by charts_node ───────────────────────────────────────────
    chart_results:     dict[str, ChartResult]

    # ── Not used in agentic mode (kept for interface compatibility) ────────
    enriched_sections: dict[str, dict[str, Any]]

    # ── Outputs ───────────────────────────────────────────────────────────
    html_path:   str
    pdf_path:    str
    token_usage: TokenUsage
    errors:      list[str]

    # ── Agentic-specific ──────────────────────────────────────────────────
    domain_profile:   Any   # DomainProfile — set by inspect_node

    # Set by Send() for each enrich_section_node invocation
    current_section:  Any   # dict — one section

    # Parallel fan-in accumulator (merged by operator.add)
    agentic_sections: Annotated[list[dict[str, Any]], operator.add]


# ── LLM factory ───────────────────────────────────────────────────────────────

def _make_llm(config: LLMConfig | None):
    """Instantiate a LangChain LLM from config; returns None on failure."""
    if config is None:
        return None
    try:
        if config.provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(
                model=config.model,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
            )
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=config.model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )
    except Exception as exc:
        log.warning("LLM init failed (%s) — running without LLM: %s",
                    getattr(config, "provider", "?"), exc)
        return None


# ── nodes ─────────────────────────────────────────────────────────────────────

def inspect_node(state: AgenticState) -> dict[str, Any]:
    """Detect domain and build a structural inventory for LLM context."""
    raw_doc = state.get("raw_doc") or {}
    if state.get("verbose"):
        log.info("inspect_node: scanning document structure")
    try:
        profile = inspect_document(raw_doc)
        if state.get("verbose"):
            log.info("inspect_node: domain=%s, %d fields indexed",
                     profile.domain, len(profile.fields))
        return {"domain_profile": profile}
    except Exception as exc:
        log.error("inspect_node failed: %s", exc)
        return {
            "domain_profile": DomainProfile(),
            "errors": (state.get("errors") or []) + [f"inspect failed: {exc}"],
        }


def dispatch_sections(state: AgenticState) -> list[Send]:
    """Fan-out: one Send() per section."""
    sections = state.get("sections") or []
    if not sections:
        log.warning("dispatch_sections: no sections found")
        return []
    if state.get("verbose"):
        log.info("dispatch_sections: fanning out %d sections", len(sections))
    return [
        Send("enrich_section_node", {"current_section": sec})
        for sec in sections
    ]


def enrich_section_node(state: AgenticState) -> dict[str, Any]:
    """Enrich one section with an LLM-written introduction.

    Content blocks are passed through unchanged — the LLM ONLY
    writes the `intro` field.
    """
    section = state.get("current_section")
    if section is None:
        return {"agentic_sections": []}

    profile: DomainProfile = state.get("domain_profile")
    domain = profile.domain if profile else "general"

    llm = _make_llm(state.get("llm_config")) if state.get("enrich_llm") else None

    if state.get("verbose"):
        log.info("enrich_section_node: enriching '%s'",
                 section.get("title", section.get("id", "?")))

    enriched = enrich_section(section=section, domain=domain, llm=llm)
    return {"agentic_sections": [enriched]}


def assemble_node(state: AgenticState) -> dict[str, Any]:
    """Replace state["sections"] with enriched versions."""
    enriched: list[dict] = state.get("agentic_sections") or []

    def _sort_key(s: dict) -> int:
        try:
            return int(s.get("number", 0))
        except (TypeError, ValueError):
            return 0

    enriched = sorted(enriched, key=_sort_key)

    if state.get("verbose"):
        log.info("assemble_node: %d enriched sections; %d chart_results",
                 len(enriched), len(state.get("chart_results") or {}))

    return {
        "sections":          enriched,
        "enriched_sections": {},  # clear so html_renderer uses sections directly
    }


# ── graph construction ────────────────────────────────────────────────────────

def build_agentic_graph():
    """Build and compile the agentic LangGraph pipeline."""
    g = StateGraph(AgenticState)

    g.add_node("preprocess",          preprocess_node)
    g.add_node("charts",              charts_node)
    g.add_node("inspect",             inspect_node)
    g.add_node("enrich_section_node", enrich_section_node)
    g.add_node("assemble",            assemble_node)
    g.add_node("html_render",         html_renderer_node)
    g.add_node("pdf_render",          pdf_renderer_node)

    g.add_edge(START,        "preprocess")
    g.add_edge("preprocess", "charts")
    g.add_edge("charts",     "inspect")

    g.add_conditional_edges(
        "inspect",
        dispatch_sections,
        ["enrich_section_node"],
    )

    g.add_edge("enrich_section_node", "assemble")
    g.add_edge("assemble",            "html_render")
    g.add_edge("html_render",         "pdf_render")
    g.add_edge("pdf_render",          END)

    return g.compile()


# ── public entry point ────────────────────────────────────────────────────────

def run_agentic_pipeline(
    input_path:  str,
    output_dir:  str,
    formats:     list[str] | None = None,
    enrich_llm:  bool = False,
    model:       str  = "gpt-4.1-mini",
    provider:    str  = "openai",
    temperature: float = 0.4,
    verbose:     bool = False,
    schema_class: Any | None = None,
) -> dict[str, Any]:
    """
    Run the agentic cert-reporter pipeline.

    Identical call signature to run_pipeline() in graph.py — the two are
    interchangeable from main.py and api/routes.py.
    """
    if formats is None:
        formats = ["html", "pdf"]

    initial_state: AgenticState = {
        "input_path":  input_path,
        "output_dir":  output_dir,
        "formats":     formats,
        "enrich_llm":  enrich_llm,
        "llm_config":  LLMConfig(model=model, provider=provider, temperature=temperature),
        "verbose":     verbose,
        "schema_class": schema_class,

        "raw_doc":          {},
        "meta":             {},
        "header":           {},
        "sections":         [],
        "footer":           "",
        "charts_to_render": [],
        "chart_results":    {},
        "enriched_sections": {},

        "domain_profile":    None,
        "agentic_sections":  [],

        "html_path":   "",
        "pdf_path":    "",
        "token_usage": TokenUsage(),
        "errors":      [],
    }

    graph = build_agentic_graph()
    return graph.invoke(initial_state)
