"""
Intermediate Pydantic models for Phase 2 computation outputs.

These models validate builder outputs before final report assembly.
They are NOT part of the certified report schema — they wrap the base
classes from certification_schema.py into per-builder result envelopes.

Each computation builder imports its Result model from here instead
of defining models inline.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .certification_schema import (
    AssessmentData,
    CardData,
    CardItem,
    FindingItem,
    GroupedBarChartData,
    HeatmapChartData,
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
    """Phase 2B output: all 13 tables."""
    tables: dict[str, TableData]


# ── Charts (Phase 2C) ───────────────────────────────────────────────

class HeatmapChart(HeatmapChartData):
    """Extends base heatmap with display_values for rendering.

    display_values holds the raw (unscaled) values for display text
    on the heatmap, while values holds [0-1] normalized values for color.
    """
    display_values: list[list[Any]] | None = None


ChartModel = RadarChartData | GroupedBarChartData | StackedBarChartData | HeatmapChart


class ChartsResult(BaseModel):
    """Phase 2C output: all 9 charts."""
    charts: dict[str, ChartModel]


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
