"""Unit tests for cert_builder/scripts/computation/table_builder.py."""

import json

import pytest

from cert_builder.scripts.computation import table_builder as tb
from cert_builder.tests._fixtures import make_category, make_meta


def _sh_ok():
    return {"status": "ok", "results": {"results": {
        "h01": {
            "time_to_detect": {"per_category": [
                {"category": "application_fault", "iqm": 12.0, "ci_lower": 10.0, "ci_upper": 15.0}]},
            "time_to_mitigate": {"per_category": [
                {"category": "application_fault", "iqm": 100.0, "ci_lower": 90.0, "ci_upper": 120.0}]},
            "reasoning_quality_score": {"per_category": [
                {"category": "application_fault", "iqm": 0.8, "ci_lower": 0.7, "ci_upper": 0.9}]},
            "hallucination_score": {"per_category": [
                {"category": "application_fault", "iqm": 0.05, "ci_lower": 0.0, "ci_upper": 0.1}]},
        },
        "h02": {
            "fault_detection_success_rate": {"per_category": [
                {"category": "application_fault", "rate": 0.8, "wilson_lower": 0.6, "wilson_upper": 0.95}]},
            "fault_mitigation_success_rate": {"per_category": [
                {"category": "application_fault", "rate": 0.7, "wilson_lower": 0.5, "wilson_upper": 0.85}]},
            "rai_compliance_rate": {"per_category": [
                {"category": "application_fault", "rate": 1.0, "wilson_lower": 0.9, "wilson_upper": 1.0}]},
            "security_compliance_rate": {"per_category": [
                {"category": "application_fault", "rate": 1.0, "wilson_lower": 0.9, "wilson_upper": 1.0}]},
        },
    }}}


# ── formatting helpers ───────────────────────────────────────────────

def test_fmt_time():
    assert tb._fmt_time(None) == "N/A"
    assert tb._fmt_time(12.34) == "12.3s"


def test_fmt_rate():
    assert tb._fmt_rate(None) == "N/A"
    assert tb._fmt_rate(0.8) == "80%"
    assert tb._fmt_rate(1.0) == "100%"


def test_fmt_score():
    assert tb._fmt_score(None) == "N/A"
    assert tb._fmt_score(0.8) == "0.80"
    assert tb._fmt_score(0.123, decimals=3) == "0.123"


def test_safe_get():
    assert tb._safe_get({"a": {"b": 1}}, "a", "b") == 1
    assert tb._safe_get({"a": 1}, "a", "b", default="d") == "d"


# ── H1/H2 lookups ────────────────────────────────────────────────────

def test_h01_lookup_empty_when_not_ok():
    assert tb._h01_per_cat_lookup({"status": "skipped"}, "time_to_detect") == {}
    assert tb._h01_per_cat_lookup(None, "time_to_detect") == {}


def test_h01_lookup_keyed_by_category():
    lookup = tb._h01_per_cat_lookup(_sh_ok(), "time_to_detect")
    assert lookup["application_fault"]["iqm"] == 12.0


def test_h02_lookup_keyed_by_category():
    lookup = tb._h02_per_cat_lookup(_sh_ok(), "fault_detection_success_rate")
    assert lookup["application_fault"]["wilson_lower"] == 0.6


# ── individual table builders (no sh) ────────────────────────────────

def test_judge_models_from_config():
    jm = tb._build_judge_models()
    assert jm["headers"] == ["Judge", "Model", "Provider", "Role"]
    assert len(jm["rows"]) >= 1


def test_ttd_category_stats_basic():
    out = tb._build_ttd_category_stats([make_category()])
    assert out["headers"] == ["Category", "Sub-Faults", "Runs", "SLA Compliance", "Detection Rate"]
    assert out["rows"][0] == ["Application", 2, 18, "50%", "80%"]


def test_ttd_category_stats_empty_category_block():
    cat = make_category()
    cat["numeric"]["time_to_detect"]["category"] = {}
    out = tb._build_ttd_category_stats([cat])
    assert out["rows"][0][0] == "Application"
    assert out["rows"][0][1:3] == [0, 0]
    assert all(c == "N/A" for c in out["rows"][0][3:])


def test_ttd_stats_subfault_detail():
    out = tb._build_ttd_stats([make_category()])
    # SLA for pod-delete in time_to_detect is 60
    assert out["rows"][0] == [
        "Application", "pod-delete", 9, "60s", "100%", "90%", "12.3s", "10.0s", "20.0s"]


def test_ttd_stats_no_subfaults():
    cat = make_category()
    cat["numeric"]["time_to_detect"]["subfault"] = {}
    out = tb._build_ttd_stats([cat])
    assert out["rows"][0][1] == "N/A"


def test_detection_rates_no_sh():
    out = tb._build_detection_rates([make_category()])
    assert out["headers"] == ["Category", "Detected", "False Neg", "False Pos", "Mitigated"]
    assert out["rows"][0] == ["Application", "80%", "20%", "0%", "70%"]


def test_safety_summary():
    out = tb._build_safety_summary([make_category()])
    assert out["rows"][0] == ["Application", "100%", "100%", False, False]


def test_action_correctness_perfect():
    out = tb._build_action_correctness([make_category()])
    assert out["rows"][0] == ["Application", "Perfect", 1.0, 1.0, 0.0]


def test_action_correctness_partial():
    cat = make_category()
    cat["numeric"]["action_correctness"] = {"mean": 0.5, "median": 0.5, "std_dev": 0.1}
    out = tb._build_action_correctness([cat])
    assert out["rows"][0][1] == "Partial"


def test_action_correctness_missing():
    cat = make_category()
    cat["numeric"]["action_correctness"] = {}
    out = tb._build_action_correctness([cat])
    assert out["rows"][0] == ["Application", "N/A", "N/A", "N/A", "N/A"]


def test_reasoning_quality_no_sh():
    out = tb._build_reasoning_quality([make_category()])
    assert out["headers"] == ["Category", "Reasoning Mean", "Reasoning Median"]
    assert out["rows"][0] == ["Application", "0.80", "0.75"]


def test_hallucination_assessment_bands():
    out = tb._build_hallucination([make_category()])
    assert out["rows"][0][-1] == "Clean"  # max 0
    cat = make_category()
    cat["numeric"]["hallucination_score"] = {"mean": 0.1, "max": 0.2}
    assert tb._build_hallucination([cat])["rows"][0][-1] == "Minor"
    cat["numeric"]["hallucination_score"] = {"mean": 0.5, "max": 0.9}
    assert tb._build_hallucination([cat])["rows"][0][-1] == "Significant"


def test_hallucination_flagged_runs_capped():
    cat = make_category()
    cat["distinct_runs"] = 10
    cat["boolean"]["hallucination_detection"] = {"detection_rate": 0.3, "any_detected": True}
    out = tb._build_hallucination([cat])
    # round(0.3*10)=3 / 10
    assert out["rows"][0][3] == "3/10"


def test_rai_compliance_pass_fail():
    out = tb._build_rai_compliance([make_category()])
    assert out["rows"][0][1] == "Pass"  # rate 1.0
    cat = make_category()
    cat["derived"]["rai_compliance_rate"] = 0.5
    assert tb._build_rai_compliance([cat])["rows"][0][1] == "Fail"


def test_security_compliance_status():
    out = tb._build_security_compliance([make_category()])
    assert out["rows"][0][1] == "Pass"
    cat = make_category()
    cat["numeric"]["adversarial_inputs"] = {"sum": 3.0}
    assert tb._build_security_compliance([cat])["rows"][0][1] == "Fail"


def test_token_usage_sums():
    out = tb._build_token_usage([make_category()])
    assert out["rows"][0] == ["Application", 10, 100.0, 50.0, 1000, 500, 1500]


# ── merged limitations / recommendations sorting ─────────────────────

def test_limitations_sorted_by_severity():
    out = tb._build_limitations([make_category()])
    sevs = [r[3] for r in out["rows"]]
    assert sevs == ["High", "Low"]  # High before Low per config order
    assert out["rows"][0][0] == 1


def test_limitations_empty_placeholder():
    cat = make_category()
    cat["textual"]["known_limitations"] = {"ranked_items": []}
    out = tb._build_limitations([cat])
    assert "No limitations identified" in out["rows"][0][1]


def test_recommendations_sorted_by_priority():
    out = tb._build_recommendations([make_category()])
    pris = [r[1] for r in out["rows"]]
    assert pris == ["High", "Low"]


def test_recommendations_empty_placeholder():
    cat = make_category()
    cat["textual"]["recommendations"] = {"prioritized_items": []}
    out = tb._build_recommendations([cat])
    assert "No recommendations generated" in out["rows"][0][2]


# ── sh=ok variants ───────────────────────────────────────────────────

def test_ttd_category_stats_with_h01_adds_ci_columns():
    out = tb._build_ttd_category_stats([make_category()], _sh_ok())
    assert "95% CI Lower" in out["headers"]
    assert out["rows"][0][-2:] == ["10.0s", "15.0s"]


def test_detection_rates_with_h02():
    out = tb._build_detection_rates([make_category()], _sh_ok())
    assert "Detection 95% Wilson CI" in out["headers"]
    assert out["rows"][0][2] == "[60.0%, 95.0%]"


def test_reasoning_quality_with_h01():
    out = tb._build_reasoning_quality([make_category()], _sh_ok())
    assert out["rows"][0][-1] == "[0.700, 0.900]"


def test_rai_compliance_with_h02_kn():
    out = tb._build_rai_compliance([make_category()], _sh_ok())
    # successful_runs=18, rate 1.0 -> 18/18
    assert out["rows"][0][1] == "18/18"
    assert out["rows"][0][3] == "90.0%"  # certified floor = wilson_lower


# ── RAI decision / evidence / framework tables ───────────────────────

def test_rai_decision_table():
    out = tb._build_rai_decision(make_meta()["responsible_ai"])
    assert out["headers"][0] == "Principle"
    # final score row PASS decision
    assert out["rows"][-1][-1] == "PASS"
    assert out["rows"][0][-1] == "PASS"  # privacy_security passed


def test_rai_decision_none():
    out = tb._build_rai_decision(None)
    # gate defaults to passed True
    assert out["rows"][0][-1] == "PASS"


def test_rai_category_evidence_table():
    out = tb._build_rai_category_evidence(make_meta()["responsible_ai"])
    assert out["rows"][0] == ["Privacy & Security", "Low", "None"]


def test_rai_category_evidence_empty():
    out = tb._build_rai_category_evidence({"evidence": []})
    assert "No RAI evidence available" in out["rows"][0][-1]


def test_framework_coverage_fail_when_gate_fails():
    rai = {"gates": {"privacy_security_passed": False}}
    out = tb._build_framework_coverage(rai)
    assert out["rows"][0][-1] == "FAIL"
    assert out["rows"][1][-1] == "PASS"  # transparency always pass


# ── public API ───────────────────────────────────────────────────────

def test_build_all_tables_has_18_tables():
    out = tb.build_all_tables([make_category()], None, make_meta()["responsible_ai"])
    assert len(out["tables"]) == 18
    assert "judge_models" in out["tables"]
    assert "framework_coverage" in out["tables"]


def test_build_from_file(tmp_path):
    ctx = {"categories": [make_category()], "meta": make_meta(),
           "statistical_hypothesis": {"status": "not_requested"}}
    p = tmp_path / "phase1.json"
    p.write_text(json.dumps(ctx))
    out = tb.build_from_file(p)
    assert len(out["tables"]) == 18
