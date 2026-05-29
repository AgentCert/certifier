"""
Intermediate Pydantic models for Phase 2 computation outputs.

These models validate builder outputs before final report assembly.
They are NOT part of the certified report schema — they wrap the base
classes from certification_schema.py into per-builder result envelopes.

Each computation builder imports its Result model from here instead
of defining models inline.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .certification_schema import (
    AssessmentData,
    CardData,
    CardItem,
    FindingItem,
    FindingSeverity,
    GroupedBarChartData,
    HeatmapChartData,
    LineChartData,
    RadarChartData,
    ScorecardDimension,
    StackedBarChartData,
    TableData,
)


# ── Scorecard (Phase 2A) ────────────────────────────────────────────

class Scorecard(BaseModel):
    """Intermediate scorecard with dimensions and per-category norms."""
    dimensions: list[ScorecardDimension] = Field(..., min_length=1)
    normalized_per_category: list[dict[str, Any]] = []


class ScorecardResult(BaseModel):
    """Phase 2A output: scorecard + findings."""
    scorecard: Scorecard
    findings: list[FindingItem]


# ── Tables (Phase 2B) ───────────────────────────────────────────────

class TablesResult(BaseModel):
    """Phase 2B output: all 18 tables."""
    tables: dict[str, TableData]


# ── Charts (Phase 2C) ───────────────────────────────────────────────

class HeatmapChart(HeatmapChartData):
    """Extends base heatmap with display_values for rendering.

    display_values holds the raw (unscaled) values for display text
    on the heatmap, while values holds [0-1] normalized values for color.
    """
    display_values: list[list[Any]] | None = None


ChartModel = RadarChartData | GroupedBarChartData | StackedBarChartData | HeatmapChart | LineChartData


class ChartsResult(BaseModel):
    """Phase 2C output: all 10 charts."""
    charts: dict[str, ChartModel]
    mean_tokens: dict[str, Any] = {}  # {"mean_input_tokens": float, "mean_output_tokens": float}


# ── Assessments (Phase 2D) ──────────────────────────────────────────

class AssessmentsResult(BaseModel):
    """Phase 2D output: assessments grouped by category."""
    assessments: dict[str, list[AssessmentData]]


# ── Hardcoded Content (Phase 2E) ────────────────────────────────────

class HardcodedContent(BaseModel):
    """Static definitions, formulas, and methodology text from YAML."""
    definitions: dict[str, str]
    normalization: dict[str, Any]
    statistics: dict[str, str]
    section_intros: dict[str, str]
    methodology_bullets: list[str] = Field(..., min_length=1)


class HardcodedResult(BaseModel):
    """Phase 2E output: hardcoded content envelope."""
    hardcoded: HardcodedContent


# ── Cards (Phase 2F) ────────────────────────────────────────────────

class CardsResult(BaseModel):
    """Phase 2F output: all 3 cards."""
    cards: dict[str, CardData]


# ── Phase 2 Merged Output ───────────────────────────────────────────

class ComputedContent(BaseModel):
    """Validated shape of the full Phase 2 output (all 6 builders merged)."""
    scorecard: dict[str, Any]
    findings: list[dict[str, Any]]
    tables: dict[str, Any]
    charts: dict[str, Any]
    assessments: dict[str, Any]
    hardcoded: dict[str, Any]
    cards: dict[str, Any]
    mean_tokens: dict[str, Any] = {}  # {"mean_input_tokens": float, "mean_output_tokens": float}


# ── Qualitative Synthesis (Phase 3C) ────────────────────────────────
#
# Schemas for the cross-category qualitative findings builder
# (`cert_builder/scripts/narratives/qualitative_builder.py`). They are
# kept here — not in ``certification_schema`` — because they are the
# raw LLM-response shape; the assembler later converts each
# ``QualitativeFinding`` into a certified ``FindingItem`` via
# ``to_finding_item()``.

class QualitativeFinding(BaseModel):
    """Single finding from the LLM (one bullet in the report)."""
    severity: FindingSeverity
    headline: str = Field(..., min_length=1, max_length=50)
    detail:   str = Field(..., min_length=1)


class QualitativeSynthesisResponse(BaseModel):
    """Schema enforced on the LLM response via structured output.

    Each of the 7 evaluation dimensions returns 1–3 findings; the assembler
    renders these as the per-dimension findings blocks in Section 3.3 of the
    certification report.
    """
    detection:          list[QualitativeFinding] = Field(..., min_length=1, max_length=3)
    mitigation:         list[QualitativeFinding] = Field(..., min_length=1, max_length=3)
    action_correctness: list[QualitativeFinding] = Field(..., min_length=1, max_length=3)
    reasoning:          list[QualitativeFinding] = Field(..., min_length=1, max_length=3)
    safety:             list[QualitativeFinding] = Field(..., min_length=1, max_length=3)
    hallucination:      list[QualitativeFinding] = Field(..., min_length=1, max_length=3)
    security:           list[QualitativeFinding] = Field(..., min_length=1, max_length=3)


class QualitativeSynthesis(BaseModel):
    """Envelope for Phase 3C output — the LLM response plus provenance
    metadata (``source``, ``model``, token usage) used by the orchestrator
    when summing per-phase token cost.
    """
    detection:          list[QualitativeFinding]
    mitigation:         list[QualitativeFinding]
    action_correctness: list[QualitativeFinding]
    reasoning:          list[QualitativeFinding]
    safety:             list[QualitativeFinding]
    hallucination:      list[QualitativeFinding]
    security:           list[QualitativeFinding]
    source:             Literal["llm", "fallback"] = "llm"
    model:              str | None = None
    tokens_used:        int = Field(default=0, ge=0)
    input_tokens:       int = Field(default=0, ge=0)
    output_tokens:      int = Field(default=0, ge=0)
