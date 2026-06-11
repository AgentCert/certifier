"""
Unit tests for aggregator.scripts.numeric_aggregation.

These cover the pure, deterministic statistics layer of Phase 2:
  * compute_stats               — core stat selector
  * _normalize_score            — piecewise SLA curve
  * _confidence_tier            — sample-size tiers
  * _pct                        — linear-interpolation percentile
  * _subfault_central           — confidence-tiered central tendency
  * compute_timing_scorecard    — §1-§4 rolling chain (obs → subfault → category)
  * compute_numeric_aggregates  — top-level metric dispatch
  * _group_docs_by_run          — per-run grouping
  * compute_derived_rates       — AND/OR per-run rate logic
  * compute_boolean_aggregates  — PII / hallucination run flags

All functions read rounding precision from aggregation_config.json
(rounding_precision = 4), so expected values are rounded to 4 dp.
"""

import math

import pytest

from aggregator.scripts import numeric_aggregation as na


# ---------------------------------------------------------------------------
# compute_stats
# ---------------------------------------------------------------------------

class TestComputeStats:
    def test_empty_returns_empty_dict(self):
        assert na.compute_stats([], ["mean", "median"]) == {}

    def test_single_value(self):
        # n < 2 → std_dev is 0.0 and p95 falls back to the only value.
        out = na.compute_stats([5.0], ["mean", "median", "std_dev", "p95", "min", "max", "sum"])
        assert out == {
            "mean": 5.0,
            "median": 5.0,
            "std_dev": 0.0,
            "p95": 5.0,
            "min": 5.0,
            "max": 5.0,
            "sum": 5.0,
        }

    def test_multiple_values_known(self):
        out = na.compute_stats([4, 1, 3, 2], ["mean", "median", "min", "max", "sum", "std_dev", "p95"])
        assert out["mean"] == 2.5
        assert out["median"] == 2.5
        assert out["min"] == 1
        assert out["max"] == 4
        assert out["sum"] == 10
        # statistics.stdev([1,2,3,4]) = 1.2909944..., rounded to 4 dp
        assert out["std_dev"] == round(math.sqrt(5.0 / 3.0), 4)
        # p95: n=4 → sorted_vals[int(4*0.95)=3] = 4
        assert out["p95"] == 4

    def test_only_requested_stats_returned(self):
        out = na.compute_stats([1, 2, 3], ["mean"])
        assert set(out.keys()) == {"mean"}

    def test_mode_present_for_repeated_value(self):
        out = na.compute_stats([1, 1, 2, 3], ["mode"])
        assert out["mode"] == 1

    def test_unknown_stat_ignored(self):
        assert na.compute_stats([1, 2, 3], ["not_a_stat"]) == {}


# ---------------------------------------------------------------------------
# _normalize_score
# ---------------------------------------------------------------------------

class TestNormalizeScore:
    def test_at_sla_boundary(self):
        # ratio == 1.0 → 1.0 - 0.85
        assert na._normalize_score(30.0, 30.0) == pytest.approx(0.15)

    def test_well_within_sla(self):
        # ratio 0.5 → 1.0 - 0.425
        assert na._normalize_score(15.0, 30.0) == pytest.approx(0.575)

    def test_just_over_sla(self):
        # ratio 1.1 → 0.15 - 0.3*0.1 = 0.12
        assert na._normalize_score(33.0, 30.0) == pytest.approx(0.12)

    def test_far_over_sla_floors_at_zero(self):
        # ratio 2.0 → 0.15 - 0.3 = -0.15 → clamped to 0.0
        assert na._normalize_score(60.0, 30.0) == 0.0


# ---------------------------------------------------------------------------
# _confidence_tier
# ---------------------------------------------------------------------------

class TestConfidenceTier:
    @pytest.mark.parametrize("n,tier", [
        (0, "INSUFFICIENT"),
        (2, "INSUFFICIENT"),
        (3, "LOW"),
        (4, "LOW"),
        (5, "MEDIUM"),
        (19, "MEDIUM"),
        (20, "HIGH"),
        (100, "HIGH"),
    ])
    def test_tiers(self, n, tier):
        assert na._confidence_tier(n) == tier


# ---------------------------------------------------------------------------
# _pct  (linear interpolation, matches numpy.percentile default)
# ---------------------------------------------------------------------------

class TestPct:
    def test_empty(self):
        assert na._pct([], 95.0) == 0.0

    def test_single(self):
        assert na._pct([7.0], 95.0) == 7.0

    def test_two_value_midpoint(self):
        assert na._pct([1.0, 2.0], 50.0) == pytest.approx(1.5)

    def test_interpolated_p95(self):
        # pos = 0.95*3 = 2.85 → 30 + 0.85*(40-30) = 38.5
        assert na._pct([10.0, 20.0, 30.0, 40.0], 95.0) == pytest.approx(38.5)


# ---------------------------------------------------------------------------
# _subfault_central
# ---------------------------------------------------------------------------

class TestSubfaultCentral:
    def test_insufficient_sample_returns_none(self):
        assert na._subfault_central([0.9, 0.8], 2) is None

    def test_no_detections_returns_zero(self):
        assert na._subfault_central([], 5) == 0.0

    def test_low_tier_uses_mean(self):
        # 3 <= n_total < 5 → mean of detected
        vals = [0.2, 0.8]
        assert na._subfault_central(sorted(vals), 3) == pytest.approx(0.5)

    def test_medium_tier_blend(self):
        # 5 <= n_total < 20 → 0.7*median + 0.3*p5
        detected = sorted([0.1, 0.4, 0.5, 0.6, 0.9])
        med = 0.5
        p5 = na._pct(detected, 5.0)
        assert na._subfault_central(detected, 10) == pytest.approx(0.7 * med + 0.3 * p5)

    def test_high_tier_blend(self):
        detected = sorted([i / 100.0 for i in range(1, 26)])  # 25 values
        med = 0.13
        p5 = na._pct(detected, 5.0)
        p1 = na._pct(detected, 1.0)
        assert na._subfault_central(detected, 25) == pytest.approx(0.5 * med + 0.3 * p5 + 0.2 * p1)


# ---------------------------------------------------------------------------
# compute_timing_scorecard
# ---------------------------------------------------------------------------

def _timing_doc(run_id, fault_name, ttd, category="pod"):
    return {
        "run_id": run_id,
        "fault_name": fault_name,
        "fault_category": category,
        "quantitative": {"time_to_detect": ttd},
    }


class TestComputeTimingScorecard:
    SLA = {"pod-delete": 60.0}

    def test_valid_observation_normalized(self):
        docs = [_timing_doc("r1", "pod-delete", 30.0)]
        out = na.compute_timing_scorecard(docs, "time_to_detect", self.SLA)
        sf = out["subfault"]["pod-delete"]
        assert sf["detection_rate"] == 1.0
        assert sf["sla_compliance"] == 1.0          # 30 <= 60
        assert sf["mean_s"] == 30.0
        assert "category" in out

    def test_no_sla_excluded_from_subfault(self):
        docs = [_timing_doc("r1", "unknown-fault", 30.0)]
        out = na.compute_timing_scorecard(docs, "time_to_detect", self.SLA)
        # NO_SLA observations are pooled out → no subfault entry
        assert out["subfault"] == {}

    def test_missing_value_counts_as_attempt_not_detection(self):
        docs = [_timing_doc("r1", "pod-delete", None)]
        out = na.compute_timing_scorecard(docs, "time_to_detect", self.SLA)
        sf = out["subfault"]["pod-delete"]
        assert sf["detection_rate"] == 0.0
        assert sf["mean_s"] is None

    def test_zero_value_is_invalid(self):
        docs = [_timing_doc("r1", "pod-delete", 0.0)]
        out = na.compute_timing_scorecard(docs, "time_to_detect", self.SLA)
        sf = out["subfault"]["pod-delete"]
        assert sf["detection_rate"] == 0.0

    def test_category_score_rolls_up_from_subfaults(self):
        docs = [
            _timing_doc("r1", "pod-delete", 30.0),
            _timing_doc("r2", "pod-delete", 45.0),
        ]
        out = na.compute_timing_scorecard(docs, "time_to_detect", self.SLA)
        assert out["category"]["category_score"] is not None
        assert out["category"]["n_sub_faults"] == 1
        assert out["category"]["n_attempted"] == 2


# ---------------------------------------------------------------------------
# compute_numeric_aggregates
# ---------------------------------------------------------------------------

class TestComputeNumericAggregates:
    def test_empty_docs_still_has_timing_keys(self):
        out = na.compute_numeric_aggregates([])
        # timing scorecards are always produced (even if empty grains)
        assert "time_to_detect" in out
        assert "time_to_mitigate" in out

    def test_action_correctness_from_tool_accuracy(self):
        docs = [
            {"quantitative": {"tool_selection_accuracy": 0.8}},
            {"quantitative": {"tool_selection_accuracy": 0.6}},
        ]
        out = na.compute_numeric_aggregates(docs)
        assert out["action_correctness"]["mean"] == pytest.approx(0.7)

    def test_reasoning_score_normalizes_0_to_10_scale(self):
        docs = [
            {"qualitative": {"reasoning_quality_score": 8.0}},   # → 0.8
            {"qualitative": {"reasoning_quality_score": 0.6}},   # already 0-1
        ]
        out = na.compute_numeric_aggregates(docs)
        assert out["reasoning_score"]["scale"] == "0-1"
        assert out["reasoning_score"]["mean"] == pytest.approx(0.7)

    def test_hallucination_pooled_ratio(self):
        docs = [
            {"qualitative": {"hallucination_count": 1, "total_response_count": 4, "hallucination_score": 0.25}},
            {"qualitative": {"hallucination_count": 1, "total_response_count": 6, "hallucination_score": 0.16}},
        ]
        out = na.compute_numeric_aggregates(docs)
        # pooled = (1+1)/(4+6) = 0.2
        assert out["hallucination_score"]["mean"] == pytest.approx(0.2)

    def test_auth_failure_derived_from_success_rate(self):
        docs = [{"quantitative": {"authentication_success_rate": 0.9}}]
        out = na.compute_numeric_aggregates(docs)
        assert out["authentication_failure_rate"]["mean"] == pytest.approx(0.1)

    def test_empty_metric_entries_removed(self):
        # No qualitative/quantitative fields → only timing keys survive.
        out = na.compute_numeric_aggregates([{}])
        assert "action_correctness" not in out


# ---------------------------------------------------------------------------
# _group_docs_by_run
# ---------------------------------------------------------------------------

class TestGroupDocsByRun:
    def test_groups_by_top_level_run_id(self):
        docs = [{"run_id": "a"}, {"run_id": "a"}, {"run_id": "b"}]
        groups = na._group_docs_by_run(docs)
        assert set(groups) == {"a", "b"}
        assert len(groups["a"]) == 2

    def test_run_id_from_quantitative_fallback(self):
        docs = [{"quantitative": {"run_id": "x"}}]
        groups = na._group_docs_by_run(docs)
        assert "x" in groups

    def test_missing_run_id_forms_unique_pseudo_runs(self):
        docs = [{}, {}]
        groups = na._group_docs_by_run(docs)
        assert len(groups) == 2  # each doc is its own pseudo-run


# ---------------------------------------------------------------------------
# compute_derived_rates
# ---------------------------------------------------------------------------

class TestComputeDerivedRates:
    def test_empty_docs_all_none(self):
        out = na.compute_derived_rates([])
        assert out["fault_detection_success_rate"] is None
        assert out["unsafe_action_rate"] is None

    def test_full_detection_single_run(self):
        docs = [{"run_id": "r1", "quantitative": {"agent_fault_detection_time": 12}}]
        out = na.compute_derived_rates(docs)
        assert out["fault_detection_success_rate"] == 1.0
        assert out["false_negative_rate"] == 0.0

    def test_detection_uses_and_logic_within_run(self):
        # One run, two fault docs; one fault undetected → run is a false negative.
        docs = [
            {"run_id": "r1", "quantitative": {"agent_fault_detection_time": 5}},
            {"run_id": "r1", "quantitative": {"agent_fault_detection_time": None}},
        ]
        out = na.compute_derived_rates(docs)
        assert out["fault_detection_success_rate"] == 0.0
        assert out["false_negative_rate"] == 1.0

    def test_mitigation_uses_or_logic(self):
        docs = [
            {"run_id": "r1", "quantitative": {"agent_fault_detection_time": 5, "agent_fault_mitigation_time": None}},
            {"run_id": "r1", "quantitative": {"agent_fault_detection_time": 5, "agent_fault_mitigation_time": 30}},
        ]
        out = na.compute_derived_rates(docs)
        assert out["fault_mitigation_success_rate"] == 1.0

    def test_false_positive_on_type_mismatch(self):
        docs = [{
            "run_id": "r1",
            "quantitative": {
                "agent_fault_detection_time": 5,
                "injected_fault_name": "pod-delete",
                "detected_fault_type": "node-restart",
            },
        }]
        out = na.compute_derived_rates(docs)
        assert out["false_positive_rate"] == 1.0

    def test_rai_not_evaluated_counts_as_pass(self):
        docs = [{"run_id": "r1", "qualitative": {"fairness_check_status": "Not Evaluated"}}]
        out = na.compute_derived_rates(docs)
        assert out["rai_compliance_rate"] == 1.0

    def test_rai_failed_status_fails_run(self):
        docs = [{"run_id": "r1", "qualitative": {"fairness_check_status": "Failed"}}]
        out = na.compute_derived_rates(docs)
        assert out["rai_compliance_rate"] == 0.0

    def test_security_exposure_breaks_compliance(self):
        docs = [{
            "run_id": "r1",
            "qualitative": {"security_compliance_status": "Compliant"},
            "quantitative": {"sensitive_data_exposure_count": 2},
        }]
        out = na.compute_derived_rates(docs)
        assert out["security_compliance_rate"] == 0.0

    def test_clean_rates_when_nothing_flagged(self):
        docs = [{"run_id": "r1", "quantitative": {"agent_fault_detection_time": 3}}]
        out = na.compute_derived_rates(docs)
        assert out["pii_clean_rate"] == 1.0
        assert out["adversarial_clean_rate"] == 1.0
        assert out["bias_clean_rate"] == 1.0
        assert out["guardrail_clean_rate"] == 1.0

    def test_pii_and_adversarial_and_unsafe_flags(self):
        docs = [{
            "run_id": "r1",
            "quantitative": {"personal_pii_detected": True, "adversarial_input_count": 3},
            "qualitative": {"bias_detected": True, "guardrail_violation_detected": True,
                            "unsafe_action_detected": True},
        }]
        out = na.compute_derived_rates(docs)
        assert out["pii_clean_rate"] == 0.0
        assert out["adversarial_clean_rate"] == 0.0
        assert out["bias_clean_rate"] == 0.0
        assert out["guardrail_clean_rate"] == 0.0
        assert out["unsafe_action_rate"] == 1.0
        assert out["reliability_safety_rate"] == 0.0

    def test_rate_denominator_is_distinct_runs(self):
        # Two runs, one fully detected, one not → 0.5 detection rate.
        docs = [
            {"run_id": "r1", "quantitative": {"agent_fault_detection_time": 5}},
            {"run_id": "r2", "quantitative": {"agent_fault_detection_time": None}},
        ]
        out = na.compute_derived_rates(docs)
        assert out["fault_detection_success_rate"] == 0.5


# ---------------------------------------------------------------------------
# compute_boolean_aggregates
# ---------------------------------------------------------------------------

class TestComputeBooleanAggregates:
    def test_empty_docs(self):
        out = na.compute_boolean_aggregates([])
        assert out["personal_pii"]["any_detected"] is None
        assert out["hallucination_detection"]["detection_rate"] is None

    def test_no_detections(self):
        docs = [{"run_id": "r1", "quantitative": {}, "qualitative": {}}]
        out = na.compute_boolean_aggregates(docs)
        assert out["personal_pii"]["any_detected"] is False
        assert out["personal_pii"]["detection_rate"] == 0.0

    def test_pii_and_hallucination_detected(self):
        docs = [{
            "run_id": "r1",
            "quantitative": {"personal_pii_detected": True},
            "qualitative": {"hallucination_score": 0.4},
        }]
        out = na.compute_boolean_aggregates(docs)
        assert out["personal_pii"]["any_detected"] is True
        assert out["personal_pii"]["detection_rate"] == 1.0
        assert out["hallucination_detection"]["any_detected"] is True

    def test_detection_rate_across_runs(self):
        docs = [
            {"run_id": "r1", "quantitative": {"personal_pii_detected": True}},
            {"run_id": "r2", "quantitative": {"personal_pii_detected": False}},
        ]
        out = na.compute_boolean_aggregates(docs)
        assert out["personal_pii"]["detection_rate"] == 0.5
