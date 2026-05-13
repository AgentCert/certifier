"""GraphState and supporting Pydantic models for the cert-reporter pipeline.

Updated for the canonical certification framework JSON format:
  meta / header / sections (with content blocks) / footer
"""

from __future__ import annotations

from typing import Any, Optional
from typing_extensions import TypedDict

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Supporting data models
# ---------------------------------------------------------------------------

class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, inp: int, out: int) -> None:
        self.input_tokens += inp
        self.output_tokens += out

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class ChartResult(BaseModel):
    chart_id: str
    chart_type: str
    title: str
    svg: str = ""          # rendered SVG string
    alt_text: str = ""
    width_px: int = 600
    height_px: int = 400
    error: Optional[str] = None


class LLMConfig(BaseModel):
    model: str = "gpt-4.1-mini"
    temperature: float = 0.4
    max_tokens: int = 4096
    provider: str = "openai"   # "openai" | "anthropic"


# ---------------------------------------------------------------------------
# GraphState TypedDict
# ---------------------------------------------------------------------------

class GraphState(TypedDict):
    # ── Inputs ──────────────────────────────────────────────────────────────
    input_path: str                      # path to source JSON file
    output_dir: str                      # directory for HTML/PDF output
    formats: list[str]                   # e.g. ["html", "pdf"]
    enrich_llm: bool                     # whether to run LLM enrichment node
    llm_config: LLMConfig                # LLM settings
    verbose: bool
    schema_class: Optional[Any]          # Pydantic model class for schema validation;
                                         # None = skip validation (pipeline default)

    # ── Parsed document fields (set by preprocess_node) ─────────────────────
    raw_doc: dict[str, Any]              # full parsed JSON
    meta: dict[str, Any]                 # report identity & scope
    header: dict[str, Any]               # scorecard + key findings
    sections: list[dict[str, Any]]       # section dicts with content blocks
    footer: str                          # footer string

    # ── Charts extracted from content blocks ────────────────────────────────
    charts_to_render: list[dict[str, Any]]  # chart blocks extracted from sections

    # ── Chart results (set by charts_node) ──────────────────────────────────
    chart_results: dict[str, ChartResult]   # keyed by chart_id

    # ── LLM-enriched sections (set by llm_enrich_node) ──────────────────────
    enriched_sections: dict[str, dict[str, Any]]  # section id → enriched section

    # ── Rendered outputs (set by html_node / pdf_node) ──────────────────────
    html_path: str
    pdf_path: str

    # ── Token usage tracking ─────────────────────────────────────────────────
    token_usage: TokenUsage

    # ── Error / status ───────────────────────────────────────────────────────
    errors: list[str]
