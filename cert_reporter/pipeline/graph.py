"""LangGraph pipeline definition for cert-reporter.

Updated for the canonical certification framework format.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from .parameters import GraphState, LLMConfig, TokenUsage
from .reader import preprocess_node
from .charts import charts_node
from .llm_nodes import llm_enrich_node
from .html_renderer import html_renderer_node
from .pdf_renderer import pdf_renderer_node

log = logging.getLogger(__name__)


def _should_enrich(state: GraphState) -> str:
    return "enrich" if state.get("enrich_llm", False) else "skip_enrich"


def build_graph() -> StateGraph:
    """Construct and compile the cert-reporter LangGraph pipeline."""
    g = StateGraph(GraphState)

    # Register nodes
    g.add_node("preprocess", preprocess_node)
    g.add_node("charts", charts_node)
    g.add_node("llm_enrich", llm_enrich_node)
    g.add_node("html_render", html_renderer_node)
    g.add_node("pdf_render", pdf_renderer_node)

    # Edges
    g.add_edge(START, "preprocess")
    g.add_edge("preprocess", "charts")

    # Conditional: run LLM enrichment or skip
    g.add_conditional_edges(
        "charts",
        _should_enrich,
        {"enrich": "llm_enrich", "skip_enrich": "html_render"},
    )
    g.add_edge("llm_enrich", "html_render")
    g.add_edge("html_render", "pdf_render")
    g.add_edge("pdf_render", END)

    return g.compile()


def run_pipeline(
    input_path: str,
    output_dir: str,
    formats: list[str] | None = None,
    enrich_llm: bool = False,
    model: str = "gpt-4.1-mini",
    provider: str = "openai",
    temperature: float = 0.4,
    verbose: bool = False,
    schema_class: Any | None = None,
) -> dict[str, Any]:
    """
    Run the full cert-reporter pipeline.

    Returns the final GraphState dict.
    """
    if formats is None:
        formats = ["html", "pdf"]

    initial_state: GraphState = {
        "input_path": input_path,
        "output_dir": output_dir,
        "formats": formats,
        "enrich_llm": enrich_llm,
        "llm_config": LLMConfig(model=model, provider=provider, temperature=temperature),
        "verbose": verbose,
        "schema_class": schema_class,
        # Fields populated by nodes:
        "raw_doc": {},
        "meta": {},
        "header": {},
        "sections": [],
        "footer": "",
        "charts_to_render": [],
        "chart_results": {},
        "enriched_sections": {},
        "html_path": "",
        "pdf_path": "",
        "token_usage": TokenUsage(),
        "errors": [],
    }

    graph = build_graph()
    final_state = graph.invoke(initial_state)
    return final_state
