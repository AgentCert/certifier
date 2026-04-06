"""
Pydantic v2 schema for the AgentCert certification report JSON format.

Model hierarchy
---------------
CertificationReport
├── meta: Meta
│   └── categories: list[CategoryMeta]
├── header: Header
│   ├── scorecard: list[ScorecardDimension]
│   └── findings: list[FindingItem]
├── sections: list[Section]
│   └── content: list[ContentBlock]   ← discriminated union (10 models, 7 types)
└── footer: str

Base vs Block pattern
---------------------
Content blocks that carry data (Table, Assessment, Card, Charts) are split
into a base class (e.g. TableData) and a *Block subclass (e.g. TableBlock).
The base holds the data fields; the Block adds the type discriminator literal.
Computation builders import bases for intermediate validation; the report
assembler adds the type literal when constructing final content blocks.

Chart blocks share type="chart" so a custom discriminator resolves on
type + chart_type (e.g. "chart.radar", "chart.grouped_bar").

Usage
-----
    from certification_schema import CertificationReport

    # Validate existing JSON
    report = CertificationReport.model_validate_json(Path("certification_report.json").read_text())

    # Build in code
    report = CertificationReport(meta=..., header=..., sections=..., footer=...)
    report.model_dump_json(indent=2)

    # Export JSON Schema
    CertificationReport.model_json_schema()
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, model_validator


# ── Enums ────────────────────────────────────────────────────────────

class FindingSeverity(str, Enum):
    """Severity tag for findings items."""
    concern = "concern"
    good = "good"
    note = "note"


class TextStyle(str, Enum):
    """Visual style hint for text blocks."""
    info = "info"
    warning = "warning"


class Rating(str, Enum):
    """Qualitative assessment rating. Field is nullable for unrated assessments."""
    strong = "Strong"
    clean = "Clean"
    moderate = "Moderate"
    minor = "Minor"
    significant = "Significant"


class Confidence(str, Enum):
    """Assessment confidence level."""
    high = "High"
    medium = "Medium"
    low = "Low"


class SectionPart(str, Enum):
    """High-level report part grouping."""
    agent_capability = "Agent Capability Assessment"
    fault_injection = "Fault Injection Analysis"


# ── Leaf Models ──────────────────────────────────────────────────────

class ScorecardDimension(BaseModel):
    """Single axis on the scorecard radar."""
    dimension: str = Field(..., min_length=1)
    value: float = Field(..., ge=0.0, le=1.0)


class FindingItem(BaseModel):
    """One finding with a severity tag."""
    severity: FindingSeverity
    text: str = Field(..., min_length=1)


class CategoryMeta(BaseModel):
    """Per-category summary in report metadata."""
    name: str = Field(..., min_length=1)
    fault: str = Field(..., min_length=1)
    runs: int = Field(..., ge=0)


class CardItem(BaseModel):
    """Key-value pair inside a card block."""
    label: str = Field(..., min_length=1)
    value: str | int


class ReferenceLine(BaseModel):
    """Horizontal reference line on a bar chart."""
    value: float
    label: str = Field(..., min_length=1)


class BarSeries(BaseModel):
    """Named data series for grouped/stacked bar charts."""
    name: str = Field(..., min_length=1)
    values: list[float] = Field(..., min_length=1)


# ── Top-Level: Meta ──────────────────────────────────────────────────

class Meta(BaseModel):
    """Report-level identity and experiment scope."""
    agent_name: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    certification_run_id: str
    certification_date: str = Field(..., min_length=1)
    subtitle: str = Field(..., min_length=1)
    total_runs: int = Field(..., ge=0)
    total_faults: int = Field(..., ge=0)
    total_categories: int = Field(..., ge=0)
    runs_per_fault_configured: int = Field(..., ge=0)
    categories: list[CategoryMeta] = Field(..., min_length=1)


# ── Top-Level: Header ────────────────────────────────────────────────

class Header(BaseModel):
    """Scorecard snapshot and key findings."""
    scorecard: list[ScorecardDimension] = Field(..., min_length=1)
    findings: list[FindingItem] = Field(..., min_length=1)


# ── Content Block Base Classes ───────────────────────────────────────
#
# Base classes hold the data fields; *Block subclasses add the
# type/chart_type literal discriminators needed by the report schema.
# Computation builders can import the bases directly for intermediate
# validation without the type literals (which are added at assembly).

class TableData(BaseModel):
    """Data table with string headers and heterogeneous rows."""
    headers: list[str] = Field(..., min_length=1)
    rows: list[list[Any]] = Field(..., min_length=1)
    title: str | None = None

    @model_validator(mode="after")
    def _check_row_lengths(self):
        expected = len(self.headers)
        for i, row in enumerate(self.rows):
            if len(row) != expected:
                raise ValueError(
                    f"Row {i} has {len(row)} cells but headers has {expected}"
                )
        return self


class AssessmentData(BaseModel):
    """LLM Council qualitative assessment data.

    rating is None for assessments without a discrete label (e.g. agent summaries).
    agreement is typically float (0.0-1.0) but may be a display string.
    """
    title: str = Field(..., min_length=1)
    rating: Rating | None = None
    confidence: Confidence
    agreement: float | str
    body: str = Field(..., min_length=1)


class CardData(BaseModel):
    """Key-value display card data."""
    items: list[CardItem] = Field(..., min_length=1)
    title: str | None = None


class RadarChartData(BaseModel):
    """Radar/spider chart data."""
    chart_type: Literal["radar"] = "radar"
    title: str = Field(..., min_length=1)
    dimensions: list[ScorecardDimension] = Field(..., min_length=1)


class GroupedBarChartData(BaseModel):
    """Grouped (side-by-side) bar chart data with optional reference lines."""
    chart_type: Literal["grouped_bar"] = "grouped_bar"
    title: str = Field(..., min_length=1)
    categories: list[str] = Field(..., min_length=1)
    series: list[BarSeries] = Field(..., min_length=1)
    y_axis: str = Field(..., min_length=1)
    reference_lines: list[ReferenceLine] | None = None


class StackedBarChartData(BaseModel):
    """Stacked bar chart data."""
    chart_type: Literal["stacked_bar"] = "stacked_bar"
    title: str = Field(..., min_length=1)
    categories: list[str] = Field(..., min_length=1)
    series: list[BarSeries] = Field(..., min_length=1)
    y_axis: str = Field(..., min_length=1)


class HeatmapChartData(BaseModel):
    """2-D heatmap data with labelled axes and colour scale."""
    chart_type: Literal["heatmap"] = "heatmap"
    title: str = Field(..., min_length=1)
    x_labels: list[str] = Field(..., min_length=1)
    y_labels: list[str] = Field(..., min_length=1)
    values: list[list[float | None]] = Field(..., min_length=1)
    scale: list[float] = Field(..., min_length=1)


# ── Content Blocks (report-level: add type discriminators) ──────────

class HeadingBlock(BaseModel):
    """In-section heading with optional detail subtitle."""
    type: Literal["heading"]
    title: str = Field(..., min_length=1)
    detail: str | None = None


class TextBlock(BaseModel):
    """Narrative paragraph with optional style hint."""
    type: Literal["text"]
    body: str = Field(..., min_length=1)
    style: TextStyle | None = None


class TableBlock(TableData):
    """Data table content block."""
    type: Literal["table"] = "table"


class FindingsBlock(BaseModel):
    """Cluster of severity-tagged findings."""
    type: Literal["findings"]
    items: list[FindingItem] = Field(..., min_length=1)


class AssessmentBlock(AssessmentData):
    """Assessment content block."""
    type: Literal["assessment"] = "assessment"


class CardBlock(CardData):
    """Card content block."""
    type: Literal["card"] = "card"


class RadarChartBlock(RadarChartData):
    """Radar chart content block."""
    type: Literal["chart"] = "chart"


class GroupedBarChartBlock(GroupedBarChartData):
    """Grouped bar chart content block."""
    type: Literal["chart"] = "chart"


class StackedBarChartBlock(StackedBarChartData):
    """Stacked bar chart content block."""
    type: Literal["chart"] = "chart"


class HeatmapChartBlock(HeatmapChartData):
    """Heatmap chart content block."""
    type: Literal["chart"] = "chart"


# ── ContentBlock Union ───────────────────────────────────────────────

def _content_block_discriminator(v: Any) -> str:
    """
    Two-level discriminator for ContentBlock union.

    Non-chart blocks resolve on type alone.
    Chart blocks resolve on type + chart_type (e.g. "chart.radar").
    """
    if isinstance(v, dict):
        block_type = v.get("type", "")
        if block_type == "chart":
            return f"chart.{v.get('chart_type', '')}"
        return block_type
    block_type = getattr(v, "type", "")
    if block_type == "chart":
        return f"chart.{getattr(v, 'chart_type', '')}"
    return block_type


ContentBlock = Annotated[
    Union[
        Annotated[HeadingBlock, Tag("heading")],
        Annotated[TextBlock, Tag("text")],
        Annotated[TableBlock, Tag("table")],
        Annotated[FindingsBlock, Tag("findings")],
        Annotated[AssessmentBlock, Tag("assessment")],
        Annotated[CardBlock, Tag("card")],
        Annotated[RadarChartBlock, Tag("chart.radar")],
        Annotated[GroupedBarChartBlock, Tag("chart.grouped_bar")],
        Annotated[StackedBarChartBlock, Tag("chart.stacked_bar")],
        Annotated[HeatmapChartBlock, Tag("chart.heatmap")],
    ],
    Discriminator(_content_block_discriminator),
]


# ── Section ──────────────────────────────────────────────────────────

class Section(BaseModel):
    """
    One numbered report section with typed content blocks.

    part groups sections into report parts (e.g. "Agent Capability Assessment").
    None for ungrouped sections.
    """
    id: str = Field(..., min_length=1)
    number: int = Field(..., ge=1)
    part: SectionPart | None = None
    title: str = Field(..., min_length=1)
    intro: str
    content: list[ContentBlock] = Field(..., min_length=1)


# ── Top-Level Report ─────────────────────────────────────────────────

class CertificationReport(BaseModel):
    """
    Root model for an AgentCert certification report.

    Four top-level keys form the stable contract.
    Internal structures may evolve but the envelope stays fixed.
    """
    model_config = ConfigDict(extra="forbid")

    meta: Meta
    header: Header
    sections: list[Section] = Field(..., min_length=1)
    footer: str = Field(..., min_length=1)


# ── CLI validation ───────────────────────────────────────────────────

if __name__ == "__main__":
    import pathlib

    report_path = pathlib.Path(__file__).parent.parent / "data" / "output" / "certification_report.json"
    print(f"Validating: {report_path}")
    report = CertificationReport.model_validate_json(report_path.read_text())
    n_sections = len(report.sections)
    n_blocks = sum(len(s.content) for s in report.sections)
    print(f"  Sections: {n_sections}")
    print(f"  Content blocks: {n_blocks}")

    # Count by type
    from collections import Counter
    type_counts = Counter()
    for s in report.sections:
        for b in s.content:
            key = b.type
            if hasattr(b, "chart_type"):
                key = f"chart.{b.chart_type}"
            type_counts[key] += 1
    for t, c in sorted(type_counts.items()):
        print(f"    {t}: {c}")

    print("Validation passed!")
