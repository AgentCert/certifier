"""Unit tests for cert_builder/schema/intermediate.py.

Covers the Phase-2 result envelope models and the Phase-3C qualitative
synthesis models (construction, defaults, validation).
"""

import pytest
from pydantic import ValidationError

from cert_builder.schema.intermediate import (
    AssessmentsResult,
    CardsResult,
    ChartsResult,
    ComputedContent,
    HardcodedContent,
    HardcodedResult,
    HeatmapChart,
    QualitativeFinding,
    QualitativeSynthesis,
    QualitativeSynthesisResponse,
    Scorecard,
    ScorecardResult,
    TablesResult,
)


def test_scorecard_normalized_default_empty():
    sc = Scorecard(dimensions=[{"dimension": "d", "value": 0.5}])
    assert sc.normalized_per_category == []


def test_scorecard_result_findings_can_be_empty():
    res = ScorecardResult.model_validate({
        "scorecard": {"dimensions": [{"dimension": "d", "value": 0.5}]},
        "findings": [],
    })
    assert res.findings == []


def test_tables_result_wraps_table_data():
    res = TablesResult.model_validate({
        "tables": {"t1": {"headers": ["a"], "rows": [[1]]}},
    })
    assert res.tables["t1"].headers == ["a"]


def test_tables_result_propagates_row_validation():
    with pytest.raises(ValidationError):
        TablesResult.model_validate({
            "tables": {"t1": {"headers": ["a", "b"], "rows": [[1]]}},
        })


def test_heatmap_chart_display_values_optional():
    hc = HeatmapChart(
        title="H", x_labels=["x"], y_labels=["y"],
        values=[[0.5]], scale=[0.0, 1.0],
    )
    assert hc.display_values is None
    assert hc.chart_type == "heatmap"


def test_charts_result_mean_tokens_default():
    res = ChartsResult.model_validate({
        "charts": {
            "radar": {"chart_type": "radar", "title": "R",
                      "dimensions": [{"dimension": "d", "value": 0.5}]},
        },
    })
    assert res.mean_tokens == {}


def test_assessments_result_grouped_by_category():
    res = AssessmentsResult.model_validate({
        "assessments": {
            "App": [{"title": "T", "rating": None, "confidence": "High",
                     "agreement": 1.0, "body": "b"}],
        },
    })
    assert res.assessments["App"][0].title == "T"


def test_hardcoded_content_requires_methodology_bullets():
    with pytest.raises(ValidationError):
        HardcodedContent(
            definitions={}, normalization={}, statistics={},
            section_intros={}, methodology_bullets=[],
        )


def test_hardcoded_result_roundtrip():
    res = HardcodedResult.model_validate({
        "hardcoded": {
            "definitions": {"ttd": "x"}, "normalization": {"k": 1},
            "statistics": {"s": "y"}, "section_intros": {"i": "z"},
            "methodology_bullets": ["b1"],
        },
    })
    assert res.hardcoded.methodology_bullets == ["b1"]


def test_cards_result():
    res = CardsResult.model_validate({
        "cards": {"identity": {"items": [{"label": "L", "value": "V"}]}},
    })
    assert res.cards["identity"].items[0].label == "L"


def test_computed_content_full_shape():
    cc = ComputedContent.model_validate({
        "scorecard": {}, "findings": [], "tables": {}, "charts": {},
        "assessments": {}, "hardcoded": {}, "cards": {},
    })
    assert cc.mean_tokens == {}


def test_computed_content_missing_key_rejected():
    with pytest.raises(ValidationError):
        ComputedContent.model_validate({
            "scorecard": {}, "findings": [], "tables": {}, "charts": {},
            "assessments": {}, "hardcoded": {},
        })


# ── Qualitative synthesis (Phase 3C) ─────────────────────────────────

def test_qualitative_finding_headline_max_length():
    QualitativeFinding(severity="good", headline="short", detail="d")
    with pytest.raises(ValidationError):
        QualitativeFinding(severity="good", headline="x" * 51, detail="d")


def _one_finding():
    return [{"severity": "note", "headline": "h", "detail": "d"}]


def test_qualitative_synthesis_response_min_one_per_dim():
    payload = {dim: _one_finding() for dim in (
        "detection", "mitigation", "action_correctness", "reasoning",
        "safety", "hallucination", "security")}
    resp = QualitativeSynthesisResponse.model_validate(payload)
    assert resp.detection[0].headline == "h"


def test_qualitative_synthesis_response_max_three():
    payload = {dim: _one_finding() for dim in (
        "detection", "mitigation", "action_correctness", "reasoning",
        "safety", "hallucination", "security")}
    payload["detection"] = _one_finding() * 4
    with pytest.raises(ValidationError):
        QualitativeSynthesisResponse.model_validate(payload)


def test_qualitative_synthesis_provenance_defaults():
    payload = {dim: _one_finding() for dim in (
        "detection", "mitigation", "action_correctness", "reasoning",
        "safety", "hallucination", "security")}
    synth = QualitativeSynthesis.model_validate(payload)
    assert synth.source == "llm"
    assert synth.tokens_used == 0
    assert synth.model is None


def test_qualitative_synthesis_tokens_non_negative():
    payload = {dim: _one_finding() for dim in (
        "detection", "mitigation", "action_correctness", "reasoning",
        "safety", "hallucination", "security")}
    payload["tokens_used"] = -5
    with pytest.raises(ValidationError):
        QualitativeSynthesis.model_validate(payload)
