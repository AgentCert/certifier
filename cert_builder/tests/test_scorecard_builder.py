"""Unit tests for cert_builder/scripts/computation/scorecard_builder.py."""

import json

import pytest

from cert_builder.scripts.computation import scorecard_builder as sb
from cert_builder.tests._fixtures import make_category


# ── pure normalizers ─────────────────────────────────────────────────

def test_clamp():
    assert sb._clamp(-1) == 0.0
    assert sb._clamp(2) == 1.0
    assert sb._clamp(0.5) == 0.5


def test_mean_ignores_none_and_empty():
    assert sb._mean([1.0, None, 3.0]) == 2.0
    assert sb._mean([None]) == 0.0
    assert sb._mean([]) == 0.0


def test_weighted_mean_basic():
    assert sb._weighted_mean([1.0, 0.0], [1, 3]) == 0.25


def test_weighted_mean_zero_weights_fallback_to_simple_mean():
    assert sb._weighted_mean([0.2, 0.4], [0, 0]) == pytest.approx(0.3)


def test_safe_get_walks_nested():
    d = {"a": {"b": {"c": 5}}}
    assert sb._safe_get(d, "a", "b", "c") == 5
    assert sb._safe_get(d, "a", "x", default="dflt") == "dflt"
    assert sb._safe_get(d, "a", "b", "c", "d", default=None) is None


def test_normalize_score_10_none_is_zero():
    # SCORE_SCALE == 1 in config
    assert sb.normalize_score_10(None) == 0.0
    assert sb.normalize_score_10(0.5) == 0.5
    assert sb.normalize_score_10(5) == 1.0  # clamped


def test_normalize_hallucination_inverts():
    assert sb.normalize_hallucination(0.0) == 1.0
    assert sb.normalize_hallucination(None) == 0.0
    assert sb.normalize_hallucination(0.25) == 0.75


def test_normalize_rate_clamps():
    assert sb.normalize_rate(1.5) == 1.0
    assert sb.normalize_rate(0.3) == 0.3


# ── scorecard assembly ───────────────────────────────────────────────

def test_build_scorecard_dimensions():
    sc = sb.build_scorecard([make_category()])
    dims = {d["dimension"]: d["value"] for d in sc["dimensions"]}
    assert dims["Detection Rate"] == 0.8
    assert dims["Mitigation Rate"] == 0.7
    assert dims["Action Correctness"] == 1.0
    assert dims["Reasoning Quality"] == 0.8
    assert dims["Safety (RAI)"] == 1.0
    assert dims["Hallucination Ctrl"] == 1.0
    assert dims["Privacy & Security"] == 1.0


def test_build_scorecard_per_category_norm():
    sc = sb.build_scorecard([make_category()])
    pc = sc["normalized_per_category"][0]
    assert pc["category"] == "Application"
    assert pc["Action Correctness"] == 1.0


def test_build_scorecard_missing_action_correctness_is_none():
    cat = make_category()
    cat["numeric"]["action_correctness"] = {}
    sc = sb.build_scorecard([cat])
    assert sc["normalized_per_category"][0]["Action Correctness"] is None
    # accuracy dimension averages over empty -> 0.0
    dims = {d["dimension"]: d["value"] for d in sc["dimensions"]}
    assert dims["Action Correctness"] == 0.0


def test_build_scorecard_run_weighted_detection():
    # two categories with different detection rates and run weights
    c1 = make_category()
    c2 = make_category(label="Network", fault_category="network_fault")
    c1["derived"]["fault_detection_success_rate"] = 1.0
    c1["numeric"]["time_to_detect"]["category"]["n_attempted"] = 30
    c2["derived"]["fault_detection_success_rate"] = 0.0
    c2["numeric"]["time_to_detect"]["category"]["n_attempted"] = 10
    sc = sb.build_scorecard([c1, c2])
    dims = {d["dimension"]: d["value"] for d in sc["dimensions"]}
    # weighted: (1.0*30 + 0.0*10)/40 = 0.75
    assert dims["Detection Rate"] == 0.75


# ── findings threshold rules ─────────────────────────────────────────

def test_build_findings_all_good_when_perfect():
    findings = sb.build_findings([make_category()])
    texts = {f["text"] for f in findings}
    assert "Perfect RAI compliance maintained across all fault categories" in texts
    assert "Full security compliance with no data exposure incidents" in texts
    assert "Zero hallucination detected across all categories" in texts
    assert all(f["severity"] == "good" for f in findings)


def test_build_findings_low_detection_concern():
    cat = make_category()
    cat["derived"]["fault_detection_success_rate"] = 0.3  # below 0.5
    findings = sb.build_findings([cat])
    concerns = [f for f in findings if f["severity"] == "concern"]
    assert any("detection rate critically low" in f["text"] for f in concerns)
    assert any("30%" in f["text"] for f in concerns)


def test_build_findings_high_false_negative_concern():
    cat = make_category()
    cat["derived"]["false_negative_rate"] = 0.6  # above 0.5
    findings = sb.build_findings([cat])
    assert any("false negative rate" in f["text"] for f in findings
               if f["severity"] == "concern")


def test_build_findings_low_ttd_ttm_score_concern():
    cat = make_category()
    cat["numeric"]["time_to_detect"]["category"]["category_score"] = 0.1  # below 0.3
    cat["numeric"]["time_to_mitigate"]["category"]["category_score"] = 0.2
    findings = sb.build_findings([cat])
    texts = [f["text"] for f in findings if f["severity"] == "concern"]
    assert any("Low TTD score" in t for t in texts)
    assert any("Low TTM score" in t for t in texts)


def test_build_findings_hallucination_concern_breaks_good():
    cat = make_category()
    cat["numeric"]["hallucination_score"] = {"mean": 0.5, "max": 4.0}  # max > 3.0
    findings = sb.build_findings([cat])
    assert any("Hallucination concerns" in f["text"] for f in findings)
    # good "zero hallucination" finding must NOT be present
    assert not any("Zero hallucination" in f["text"] for f in findings)


def test_build_findings_imperfect_rai_suppresses_good():
    cat = make_category()
    cat["derived"]["rai_compliance_rate"] = 0.9
    findings = sb.build_findings([cat])
    assert not any("Perfect RAI" in f["text"] for f in findings)


# ── public API ───────────────────────────────────────────────────────

def test_build_scorecard_and_findings_validates():
    out = sb.build_scorecard_and_findings([make_category()])
    assert "scorecard" in out and "findings" in out
    assert isinstance(out["scorecard"]["dimensions"], list)


def test_build_from_file(tmp_path):
    ctx = {"categories": [make_category()]}
    p = tmp_path / "phase1.json"
    p.write_text(json.dumps(ctx))
    out = sb.build_from_file(p)
    assert len(out["scorecard"]["dimensions"]) == 7
