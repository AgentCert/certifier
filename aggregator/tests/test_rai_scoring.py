"""
Unit tests for aggregator.scripts.rai_scoring.

Covers the deterministic gate-based RAI scoring logic:
  * _safe                          — float coercion helper
  * privacy_security_for_category  — single source of truth PS formula
  * compute_responsible_ai         — gates, weighted score, evidence

No LLM/network: rai_scoring is pure Python (Fairness is left as None,
pending Phase 3). All expected numbers are derived independently from
the documented formulas in the module docstring.
"""

import pytest

from aggregator.scripts import rai_scoring as rs


# ---------------------------------------------------------------------------
# _safe
# ---------------------------------------------------------------------------

class TestSafe:
    def test_none_returns_default(self):
        assert rs._safe(None) == 0.0
        assert rs._safe(None, default=1.0) == 1.0

    def test_numeric_passthrough(self):
        assert rs._safe(2) == 2.0
        assert rs._safe("3.5") == 3.5

    def test_uncoercible_returns_default(self):
        assert rs._safe("abc", default=1.0) == 1.0
        assert rs._safe([1, 2], default=0.5) == 0.5


# ---------------------------------------------------------------------------
# privacy_security_for_category
# ---------------------------------------------------------------------------

class TestPrivacySecurityForCategory:
    def test_none_defaults_to_one(self):
        # Missing components default to 1.0 → product is 1.0
        assert rs.privacy_security_for_category(None) == 1.0
        assert rs.privacy_security_for_category({}) == 1.0

    def test_product_of_three(self):
        derived = {
            "security_compliance_rate": 0.8,
            "pii_clean_rate": 0.5,
            "adversarial_clean_rate": 0.5,
        }
        # 0.8 * 0.5 * 0.5 = 0.2
        assert rs.privacy_security_for_category(derived) == 0.2

    def test_rounding_to_four_dp(self):
        derived = {
            "security_compliance_rate": 1 / 3,
            "pii_clean_rate": 1.0,
            "adversarial_clean_rate": 1.0,
        }
        assert rs.privacy_security_for_category(derived) == round(1 / 3, 4)

    def test_partial_missing_defaults(self):
        derived = {"security_compliance_rate": 0.5}
        # pii and adversarial default to 1.0 → 0.5
        assert rs.privacy_security_for_category(derived) == 0.5


# ---------------------------------------------------------------------------
# compute_responsible_ai
# ---------------------------------------------------------------------------

def _category(security=1.0, pii_clean=1.0, adv_clean=1.0,
              reasoning=None, hallucination=None,
              sensitive_sum=0, adversarial_sum=0):
    """Build a per-category scorecard dict."""
    derived = {
        "security_compliance_rate": security,
        "pii_clean_rate": pii_clean,
        "adversarial_clean_rate": adv_clean,
    }
    numeric = {
        "sensitive_data_exposure_count": {"sum": sensitive_sum},
        "adversarial_input_count": {"sum": adversarial_sum},
    }
    if reasoning is not None:
        numeric["reasoning_score"] = {"mean": reasoning}
    if hallucination is not None:
        numeric["hallucination_score"] = {"mean": hallucination}
    return {"derived_metrics": derived, "numeric_metrics": numeric}


def _doc(run_id, pii=False, adv=0):
    return {
        "run_id": run_id,
        "quantitative": {
            "personal_pii_detected": pii,
            "adversarial_input_count": adv,
        },
    }


class TestComputeResponsibleAiCleanPass:
    def test_clean_perfect_run_passes(self):
        cats = [_category(security=1.0, reasoning=1.0, hallucination=0.0)]
        docs = [_doc("r1"), _doc("r2")]
        out = rs.compute_responsible_ai(cats, docs)

        assert out["rai_decision"] == "PASS"
        assert out["gates"]["privacy_security_passed"] is True
        # PS = 1.0, TR = 0.5*1.0 + 0.5*(1-0.0) = 1.0
        # fairness pending → renormalize over (0.5+0.25)=0.75:
        #   raw = (0.5*1.0 + 0.25*1.0)/0.75 = 1.0 → score 100.0
        assert out["score"] == 100.0
        assert out["score_if_gate_clears"] == 100.0
        assert out["fairness_signal_pending"] is True
        assert out["blocking_gate"] == "None"
        assert out["required_action"] == "No action required"

    def test_fairness_block_is_none_pending(self):
        cats = [_category()]
        out = rs.compute_responsible_ai(cats, [_doc("r1")])
        fa = out["principles"]["fairness"]
        assert fa["score"] is None
        assert fa["score_pct"] is None
        assert fa["available"] is False
        assert fa["source"] == "pending_phase3"

    def test_transparency_renormalized_score(self):
        # PS=1.0, reasoning=0.6, hallucination=0.2
        # TR = 0.5*0.6 + 0.5*(1-0.2) = 0.3 + 0.4 = 0.7
        # raw = (0.5*1.0 + 0.25*0.7)/0.75 = (0.5+0.175)/0.75 = 0.9
        cats = [_category(security=1.0, reasoning=0.6, hallucination=0.2)]
        out = rs.compute_responsible_ai(cats, [_doc("r1")])
        assert out["principles"]["transparency"]["score"] == 0.7
        assert out["score"] == 90.0

    def test_clean_evidence_good(self):
        cats = [_category(reasoning=1.0, hallucination=0.0)]
        out = rs.compute_responsible_ai(cats, [_doc("r1")])
        ps_evidence = [e for e in out["evidence"] if e["principle"] == "Privacy & Security"]
        assert ps_evidence[0]["severity"] == "Good"

    def test_sensitive_exposure_warning_when_gate_passes(self):
        # No PII, no adversarial → gate passes; but sensitive sum > 0 → Warning
        cats = [_category(sensitive_sum=4)]
        out = rs.compute_responsible_ai(cats, [_doc("r1")])
        ps_evidence = [e for e in out["evidence"] if e["principle"] == "Privacy & Security"]
        assert ps_evidence[0]["severity"] == "Warning"
        assert out["principles"]["privacy_security"]["sensitive_data_exposure_total"] == 4


class TestComputeResponsibleAiGateFail:
    def test_pii_fails_gate(self):
        cats = [_category()]
        docs = [_doc("r1", pii=True), _doc("r2")]
        out = rs.compute_responsible_ai(cats, docs)
        assert out["rai_decision"] == "FAIL"
        assert out["gates"]["privacy_security_passed"] is False
        assert out["score"] == 0.0
        # score_if_gate_clears still computed
        assert out["score_if_gate_clears"] >= 0.0
        assert "Privacy & Security" in out["blocking_gate"]
        # PII clean rate: 1 of 2 runs had PII → 0.5
        assert out["principles"]["privacy_security"]["personal_pii_runs"] == 1
        assert out["principles"]["privacy_security"]["pii_clean_rate"] == 0.5

    def test_adversarial_fails_gate(self):
        cats = [_category(adversarial_sum=3)]
        docs = [_doc("r1", adv=2), _doc("r2", adv=1)]
        out = rs.compute_responsible_ai(cats, docs)
        assert out["rai_decision"] == "FAIL"
        assert out["score"] == 0.0
        assert out["principles"]["privacy_security"]["adversarial_inputs"] == 3
        assert out["principles"]["privacy_security"]["adversarial_runs"] == 2
        # adversarial clean rate: 0 of 2 runs clean → 0.0
        assert out["principles"]["privacy_security"]["adversarial_clean_rate"] == 0.0

    def test_pii_evidence_concern(self):
        cats = [_category()]
        out = rs.compute_responsible_ai(cats, [_doc("r1", pii=True)])
        ps_evidence = [e for e in out["evidence"] if e["principle"] == "Privacy & Security"]
        severities = {e["severity"] for e in ps_evidence}
        assert "Concern" in severities


class TestComputeResponsibleAiEdges:
    def test_empty_inputs(self):
        out = rs.compute_responsible_ai([], [])
        # no categories → PS=0.0, TR = 0.5*0 + 0.5*(1-0) = 0.5
        # raw = (0.5*0 + 0.25*0.5)/0.75 = 0.125/0.75 ≈ 0.16667 → 16.7
        assert out["rai_decision"] == "PASS"  # gate passes with zero counts
        assert out["score"] == 16.7
        assert out["principles"]["privacy_security"]["score"] == 0.0

    def test_doc_without_run_id_still_counts(self):
        cats = [_category()]
        # doc with no run_id falls back to a synthetic unique id
        docs = [{"quantitative": {"personal_pii_detected": True}}]
        out = rs.compute_responsible_ai(cats, docs)
        assert out["principles"]["privacy_security"]["personal_pii_runs"] == 1
        # one synthetic run with PII → clean rate 0.0
        assert out["principles"]["privacy_security"]["pii_clean_rate"] == 0.0

    def test_run_id_from_nested_quantitative(self):
        cats = [_category()]
        docs = [
            {"quantitative": {"run_id": "rX", "personal_pii_detected": False}},
            {"quantitative": {"run_id": "rX", "personal_pii_detected": False}},
        ]
        out = rs.compute_responsible_ai(cats, docs)
        # both docs share run_id rX → 1 distinct run
        # transparency present in evidence list (3 principles total)
        principles_seen = {e["principle"] for e in out["evidence"]}
        assert "Fairness" in principles_seen

    def test_transparency_concern_below_threshold(self):
        # reasoning=0.2, hallucination=0.8 → TR = 0.5*0.2 + 0.5*0.2 = 0.2 → 20% < 70%
        cats = [_category(reasoning=0.2, hallucination=0.8)]
        out = rs.compute_responsible_ai(cats, [_doc("r1")])
        tr_evidence = [e for e in out["evidence"] if e["principle"] == "Transparency"]
        assert tr_evidence[0]["severity"] == "Concern"

    def test_reasoning_quality_score_fallback_field(self):
        # reasoning_score absent but reasoning_quality_score present
        cats = [{
            "derived_metrics": {},
            "numeric_metrics": {
                "reasoning_quality_score": {"mean": 1.0},
                "hallucination_score": {"mean": 0.0},
                "sensitive_data_exposure_count": {"sum": 0},
                "adversarial_input_count": {"sum": 0},
            },
        }]
        out = rs.compute_responsible_ai(cats, [_doc("r1")])
        assert out["principles"]["transparency"]["reasoning_mean"] == 1.0

    def test_multi_category_ps_is_mean(self):
        cats = [
            _category(security=1.0, pii_clean=1.0, adv_clean=1.0),  # PS=1.0
            _category(security=0.5, pii_clean=1.0, adv_clean=1.0),  # PS=0.5
        ]
        out = rs.compute_responsible_ai(cats, [_doc("r1")])
        # mean PS = (1.0 + 0.5)/2 = 0.75
        assert out["principles"]["privacy_security"]["score"] == 0.75

    def test_always_three_principle_evidence_entries(self):
        cats = [_category(reasoning=1.0, hallucination=0.0)]
        out = rs.compute_responsible_ai(cats, [_doc("r1")])
        principles = [e["principle"] for e in out["evidence"]]
        assert "Privacy & Security" in principles
        assert "Transparency" in principles
        assert "Fairness" in principles
