"""Unit tests for cert_builder/schema/certification_schema.py.

Covers Pydantic model construction, validation, defaults, serialization,
and the two-level ContentBlock discriminated union.
"""

import pytest
from pydantic import TypeAdapter, ValidationError

from cert_builder.schema.certification_schema import (
    AssessmentBlock,
    CardBlock,
    CardItem,
    CategoryMeta,
    CertificationReport,
    CIBarChartData,
    CIBarPoint,
    Confidence,
    ContentBlock,
    EnumeratedItemBlock,
    FindingItem,
    FindingSeverity,
    GroupedBarChartBlock,
    Header,
    HeadingBlock,
    HypothesisFact,
    HypothesisStripBlock,
    Meta,
    NoticeBlock,
    NoticeSeverity,
    Rating,
    RadarChartBlock,
    ScopeStat,
    ScorecardDimension,
    Section,
    SectionPart,
    TableBlock,
    TableData,
    TaxonomyTableBlock,
    TextBlock,
    TextStyle,
    _content_block_discriminator,
)


# ── Leaf model validation ────────────────────────────────────────────

def test_scorecard_dimension_valid():
    d = ScorecardDimension(dimension="Detection", value=0.5)
    assert d.value == 0.5


@pytest.mark.parametrize("bad", [-0.01, 1.01, 2.0])
def test_scorecard_dimension_value_out_of_range(bad):
    with pytest.raises(ValidationError):
        ScorecardDimension(dimension="X", value=bad)


def test_scorecard_dimension_empty_name_rejected():
    with pytest.raises(ValidationError):
        ScorecardDimension(dimension="", value=0.5)


def test_finding_item_enum_coercion():
    f = FindingItem(severity="good", text="all clear")
    assert f.severity is FindingSeverity.good


def test_finding_item_bad_severity():
    with pytest.raises(ValidationError):
        FindingItem(severity="critical", text="x")


def test_category_meta_runs_non_negative():
    with pytest.raises(ValidationError):
        CategoryMeta(name="App", fault="kill", runs=-1)


def test_card_item_value_union():
    assert CardItem(label="Runs", value=30).value == 30
    assert CardItem(label="Name", value="agent").value == "agent"


# ── TableData row-length validator ───────────────────────────────────

def test_table_data_valid_rows():
    t = TableData(headers=["a", "b"], rows=[[1, 2], [3, 4]])
    assert len(t.rows) == 2


def test_table_data_row_length_mismatch():
    with pytest.raises(ValidationError) as exc:
        TableData(headers=["a", "b"], rows=[[1, 2], [3]])
    assert "Row 1 has 1 cells but headers has 2" in str(exc.value)


def test_table_block_adds_type_default():
    block = TableBlock(headers=["h"], rows=[[1]])
    assert block.type == "table"


# ── Assessment ───────────────────────────────────────────────────────

def test_assessment_block_rating_optional():
    a = AssessmentBlock(title="Summary", rating=None, confidence="High",
                        agreement=0.9, body="text")
    assert a.rating is None
    assert a.type == "assessment"
    assert a.confidence is Confidence.high


def test_assessment_agreement_accepts_string():
    a = AssessmentBlock(title="T", rating="Strong", confidence="Low",
                        agreement="N/A", body="b")
    assert a.agreement == "N/A"
    assert a.rating is Rating.strong


# ── Chart defaults ───────────────────────────────────────────────────

def test_radar_block_chart_type_default():
    block = RadarChartBlock(title="R", dimensions=[{"dimension": "d", "value": 0.5}])
    assert block.chart_type == "radar"
    assert block.type == "chart"


def test_grouped_bar_requires_series():
    with pytest.raises(ValidationError):
        GroupedBarChartBlock(title="T", categories=["a"], series=[], y_axis="Y")


def test_ci_bar_reference_lines_default_factory():
    c = CIBarChartData(title="T", points=[CIBarPoint(label="a", value=1.0)])
    assert c.reference_lines == []
    # default-factory list must be independent per-instance
    c2 = CIBarChartData(title="T2", points=[CIBarPoint(label="b", value=2.0)])
    c.reference_lines.append({"x": 1})
    assert c2.reference_lines == []


# ── Notice / enumerated / hypothesis ─────────────────────────────────

def test_notice_block_default_severity():
    n = NoticeBlock(type="notice", body="hello")
    assert n.severity is NoticeSeverity.info


def test_enumerated_item_tags_default_and_index_min():
    item = EnumeratedItemBlock(type="enumerated_item", kind="limitation",
                               index=1, severity="High", scope="App", body="b")
    assert item.tags == []
    with pytest.raises(ValidationError):
        EnumeratedItemBlock(type="enumerated_item", kind="limitation",
                            index=0, severity="High", scope="App", body="b")


def test_hypothesis_strip_defaults():
    s = HypothesisStripBlock(type="hypothesis_strip", verdict="pass",
                             hypothesis_id="H1")
    assert s.facts == []
    assert s.summary is None


def test_hypothesis_fact_tone_literal():
    HypothesisFact(label="App", text="ok", tone="flag")
    with pytest.raises(ValidationError):
        HypothesisFact(label="App", text="ok", tone="bogus")


def test_scope_stat_default_tone():
    s = ScopeStat(value="30", label="Total Runs")
    assert s.tone == "neutral"


def test_taxonomy_table_inherits_row_validation():
    with pytest.raises(ValidationError):
        TaxonomyTableBlock(headers=["a", "b"], rows=[[1]])
    t = TaxonomyTableBlock(headers=["a"], rows=[[1]])
    assert t.type == "taxonomy_table"


# ── Discriminator function ───────────────────────────────────────────

def test_discriminator_non_chart_dict():
    assert _content_block_discriminator({"type": "text"}) == "text"


def test_discriminator_chart_dict():
    assert _content_block_discriminator(
        {"type": "chart", "chart_type": "radar"}) == "chart.radar"


def test_discriminator_object_attr():
    block = HeadingBlock(type="heading", title="t")
    assert _content_block_discriminator(block) == "heading"


def test_discriminator_chart_object():
    block = RadarChartBlock(title="r", dimensions=[{"dimension": "d", "value": 0.1}])
    assert _content_block_discriminator(block) == "chart.radar"


# ── ContentBlock union resolution ────────────────────────────────────

_ADAPTER = TypeAdapter(ContentBlock)


def test_content_block_union_resolves_text():
    block = _ADAPTER.validate_python({"type": "text", "body": "hi"})
    assert isinstance(block, TextBlock)


def test_content_block_union_resolves_chart_radar():
    block = _ADAPTER.validate_python({
        "type": "chart", "chart_type": "radar", "title": "R",
        "dimensions": [{"dimension": "d", "value": 0.5}],
    })
    assert isinstance(block, RadarChartBlock)


def test_content_block_union_resolves_grouped_bar():
    block = _ADAPTER.validate_python({
        "type": "chart", "chart_type": "grouped_bar", "title": "G",
        "categories": ["a"], "series": [{"name": "s", "values": [1.0]}],
        "y_axis": "Y",
    })
    assert isinstance(block, GroupedBarChartBlock)


# ── Section + top-level report ───────────────────────────────────────

def _minimal_meta():
    return Meta(
        agent_name="Agent", agent_id="id-1", certification_run_id="run-1",
        certification_date="2026-01-01", subtitle="sub", total_runs=10,
        successful_runs=8, failed_runs=2, total_faults=3, total_categories=2,
        categories=[CategoryMeta(name="App", fault="kill", runs=5)],
    )


def test_meta_defaults():
    m = _minimal_meta()
    assert m.runs_per_fault_configured == 0
    assert m.responsible_ai is None


def test_meta_requires_at_least_one_category():
    with pytest.raises(ValidationError):
        Meta(
            agent_name="A", agent_id="i", certification_run_id="r",
            certification_date="d", subtitle="s", total_runs=1,
            successful_runs=1, failed_runs=0, total_faults=1,
            total_categories=1, categories=[],
        )


def test_section_part_optional_and_enum():
    sec = Section(id="s1", number=1, part="Agent Capability Assessment",
                  title="T", intro="", content=[HeadingBlock(type="heading", title="h")])
    assert sec.part is SectionPart.agent_capability


def test_section_number_zero_allowed():
    sec = Section(id="banner", number=0, title="Part I", intro="",
                  content=[HeadingBlock(type="heading", title="h")])
    assert sec.number == 0


def test_certification_report_header_optional():
    report = CertificationReport(
        meta=_minimal_meta(),
        sections=[Section(id="s", number=1, title="T", intro="",
                          content=[TextBlock(type="text", body="b")])],
        footer="end",
    )
    assert report.header is None


def test_certification_report_extra_forbidden():
    with pytest.raises(ValidationError):
        CertificationReport(
            meta=_minimal_meta(),
            sections=[Section(id="s", number=1, title="T", intro="",
                              content=[TextBlock(type="text", body="b")])],
            footer="end",
            unexpected_field="x",
        )


def test_certification_report_roundtrip_json():
    report = CertificationReport(
        meta=_minimal_meta(),
        header=Header(
            scorecard=[ScorecardDimension(dimension="Detection", value=0.5)],
            findings=[FindingItem(severity="good", text="ok")],
        ),
        sections=[Section(id="s", number=1, title="T", intro="i",
                          content=[CardBlock(items=[CardItem(label="L", value="V")])])],
        footer="end",
    )
    dumped = report.model_dump_json()
    restored = CertificationReport.model_validate_json(dumped)
    assert restored.footer == "end"
    assert restored.header.scorecard[0].value == 0.5
    assert restored.sections[0].content[0].type == "card"
