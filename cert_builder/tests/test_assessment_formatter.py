"""Unit tests for cert_builder/scripts/computation/assessment_formatter.py."""

import json

from cert_builder.scripts.computation import assessment_formatter as af
from cert_builder.tests._fixtures import make_category


def test_format_assessment_with_rating():
    obj = {"severity_label": "Strong", "confidence": "High",
           "inter_judge_agreement": 0.9, "consensus_summary": "Good."}
    out = af._format_assessment(obj, "Quality", has_rating=True)
    assert out == {"title": "Quality", "rating": "Strong", "confidence": "High",
                   "agreement": 0.9, "body": "Good."}


def test_format_assessment_without_rating():
    obj = {"confidence": "Medium", "consensus_summary": "Summary."}
    out = af._format_assessment(obj, "Agent Summary", has_rating=False)
    assert out["rating"] is None
    assert out["agreement"] == 1.0  # default


def test_build_all_assessments_field_order():
    out = af.build_all_assessments([make_category()])
    blocks = out["assessments"]["Application"]
    titles = [b["title"] for b in blocks]
    assert titles == ["Agent Summary", "Response & Reasoning Quality", "Security Compliance"]
    # agent_summary has no rating
    assert blocks[0]["rating"] is None
    # response quality has rating
    assert blocks[1]["rating"] == "Strong"


def test_build_all_assessments_skips_missing_fields():
    cat = make_category()
    cat["textual"] = {"agent_summary": {"consensus_summary": "only this"}}
    out = af.build_all_assessments([cat])
    blocks = out["assessments"]["Application"]
    assert len(blocks) == 1
    assert blocks[0]["title"] == "Agent Summary"


def test_build_all_assessments_unknown_label():
    cat = make_category()
    del cat["label"]
    cat["textual"] = {}
    out = af.build_all_assessments([cat])
    assert "Unknown" in out["assessments"]


def test_build_from_file(tmp_path):
    p = tmp_path / "phase1.json"
    p.write_text(json.dumps({"categories": [make_category()]}))
    out = af.build_from_file(p)
    assert "Application" in out["assessments"]
