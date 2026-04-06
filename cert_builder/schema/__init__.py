"""Certification report Pydantic v2 schema."""

# ── Certified report models (final JSON contract) ──────────────────

from .certification_schema import (
    # Enums
    Confidence,
    FindingSeverity,
    Rating,
    SectionPart,
    TextStyle,
    # Leaf models
    BarSeries,
    CardItem,
    CategoryMeta,
    FindingItem,
    ReferenceLine,
    ScorecardDimension,
    # Base classes (shared with intermediate models)
    AssessmentData,
    CardData,
    GroupedBarChartData,
    HeatmapChartData,
    RadarChartData,
    StackedBarChartData,
    TableData,
    # Content blocks (report-level, with type discriminators)
    AssessmentBlock,
    CardBlock,
    FindingsBlock,
    GroupedBarChartBlock,
    HeadingBlock,
    HeatmapChartBlock,
    RadarChartBlock,
    StackedBarChartBlock,
    TableBlock,
    TextBlock,
    # Top-level
    ContentBlock,
    Header,
    Meta,
    Section,
    CertificationReport,
)

# ── Intermediate models (Phase 2 builder outputs) ──────────────────

from .intermediate import (
    AssessmentsResult,
    CardsResult,
    ChartsResult,
    ComputedContent,
    HardcodedContent,
    HardcodedResult,
    HeatmapChart,
    Scorecard,
    ScorecardResult,
    TablesResult,
)

__all__ = [
    # Enums
    "Confidence",
    "FindingSeverity",
    "Rating",
    "SectionPart",
    "TextStyle",
    # Leaf models
    "BarSeries",
    "CardItem",
    "CategoryMeta",
    "FindingItem",
    "ReferenceLine",
    "ScorecardDimension",
    # Base classes
    "AssessmentData",
    "CardData",
    "GroupedBarChartData",
    "HeatmapChartData",
    "RadarChartData",
    "StackedBarChartData",
    "TableData",
    # Content blocks
    "AssessmentBlock",
    "CardBlock",
    "FindingsBlock",
    "GroupedBarChartBlock",
    "HeadingBlock",
    "HeatmapChartBlock",
    "RadarChartBlock",
    "StackedBarChartBlock",
    "TableBlock",
    "TextBlock",
    # Top-level
    "ContentBlock",
    "Header",
    "Meta",
    "Section",
    "CertificationReport",
    # Intermediate
    "AssessmentsResult",
    "CardsResult",
    "ChartsResult",
    "ComputedContent",
    "HardcodedContent",
    "HardcodedResult",
    "HeatmapChart",
    "Scorecard",
    "ScorecardResult",
    "TablesResult",
]
