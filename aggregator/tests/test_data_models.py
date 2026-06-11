"""
Unit tests for aggregator.schema.data_models.

Covers construction, defaults, validation, serialization and the
TokenUsage helper methods. All models are pure Pydantic — no I/O.
"""

import pytest
from pydantic import ValidationError

from aggregator.schema.data_models import (
    StatsSummary,
    SubFaultTimingScore,
    CategoryTimingScore,
    CumulativeTimingScore,
    TimingScorecard,
    DetectionStatus,
    BooleanAggregates,
    DerivedRates,
    TextualConsensus,
    RankedLimitation,
    PrioritizedRecommendation,
    KnownLimitations,
    Recommendations,
    NumericMetrics,
    FaultCategoryScorecard,
    CertificationScorecard,
    TokenUsage,
)


class TestStatsSummary:
    def test_all_defaults_none(self):
        s = StatsSummary()
        assert s.mean is None
        assert s.median is None
        assert s.std_dev is None
        assert s.p95 is None
        assert s.min is None
        assert s.max is None
        assert s.sum is None
        assert s.mode is None
        assert s.unit is None
        assert s.scale is None

    def test_assignment_and_dump(self):
        s = StatsSummary(mean=1.5, median=2.0, unit="s", scale="linear")
        dumped = s.model_dump()
        assert dumped["mean"] == 1.5
        assert dumped["unit"] == "s"
        assert dumped["std_dev"] is None


class TestTimingScores:
    def test_subfault_defaults(self):
        sf = SubFaultTimingScore()
        assert sf.n_attempted == 0
        assert sf.detection_rate == 0.0
        assert sf.sla_compliance is None
        assert sf.weighted_score is None
        assert sf.confidence == "INSUFFICIENT"

    def test_category_defaults(self):
        c = CategoryTimingScore()
        assert c.n_sub_faults == 0
        assert c.n_attempted == 0
        assert c.detection_rate == 0.0
        assert c.sla_compliance is None
        assert c.category_score == 0.0

    def test_cumulative_quality_flags_default_factory(self):
        # default_factory must produce a fresh list each time, not a shared ref
        a = CumulativeTimingScore()
        b = CumulativeTimingScore()
        assert a.quality_flags == ["none"]
        a.quality_flags.append("x")
        assert b.quality_flags == ["none"]

    def test_timing_scorecard_nested_defaults(self):
        ts = TimingScorecard()
        assert ts.subfault == {}
        assert isinstance(ts.category, CategoryTimingScore)
        assert ts.category.category_score == 0.0

    def test_timing_scorecard_with_subfaults(self):
        ts = TimingScorecard(
            subfault={"pod-delete": SubFaultTimingScore(n_attempted=5)},
            category=CategoryTimingScore(n_sub_faults=1),
        )
        assert ts.subfault["pod-delete"].n_attempted == 5
        assert ts.category.n_sub_faults == 1

    def test_subfault_dict_coercion_from_plain_dict(self):
        ts = TimingScorecard(subfault={"x": {"n_attempted": 3, "detection_rate": 0.5}})
        assert isinstance(ts.subfault["x"], SubFaultTimingScore)
        assert ts.subfault["x"].detection_rate == 0.5


class TestDetectionAndBoolean:
    def test_detection_status_defaults(self):
        d = DetectionStatus()
        assert d.any_detected is None
        assert d.detection_rate is None

    def test_boolean_aggregates_default_factories(self):
        b = BooleanAggregates()
        assert isinstance(b.personal_pii, DetectionStatus)
        assert isinstance(b.hallucination_detection, DetectionStatus)
        assert b.personal_pii.any_detected is None

    def test_boolean_aggregates_independent_instances(self):
        a = BooleanAggregates()
        b = BooleanAggregates()
        a.personal_pii.any_detected = True
        assert b.personal_pii.any_detected is None


class TestDerivedRates:
    def test_defaults_all_none(self):
        d = DerivedRates()
        assert d.fault_detection_success_rate is None
        assert d.fault_mitigation_success_rate is None
        assert d.false_negative_rate is None
        assert d.false_positive_rate is None
        assert d.rai_compliance_rate is None
        assert d.security_compliance_rate is None

    def test_partial_assignment(self):
        d = DerivedRates(fault_detection_success_rate=0.9)
        assert d.fault_detection_success_rate == 0.9
        assert d.false_negative_rate is None


class TestTextualConsensus:
    def test_defaults(self):
        t = TextualConsensus()
        assert t.consensus_summary == ""
        assert t.severity_label is None
        assert t.confidence is None
        assert t.inter_judge_agreement is None

    def test_populated(self):
        t = TextualConsensus(
            consensus_summary="ok",
            severity_label="Strong",
            confidence="High",
            inter_judge_agreement=0.8,
        )
        assert t.consensus_summary == "ok"
        assert t.inter_judge_agreement == 0.8


class TestRankedAndRecommendation:
    def test_ranked_limitation_requires_limitation(self):
        with pytest.raises(ValidationError):
            RankedLimitation()

    def test_ranked_limitation_defaults(self):
        r = RankedLimitation(limitation="slow detection")
        assert r.limitation == "slow detection"
        assert r.frequency == 0
        assert r.severity == "Medium"

    def test_prioritized_recommendation_requires_recommendation(self):
        with pytest.raises(ValidationError):
            PrioritizedRecommendation()

    def test_prioritized_recommendation_defaults(self):
        p = PrioritizedRecommendation(recommendation="add retries")
        assert p.recommendation == "add retries"
        assert p.priority == "Medium"
        assert p.frequency == 0

    def test_known_limitations_default_empty(self):
        k = KnownLimitations()
        assert k.ranked_items == []

    def test_known_limitations_with_items(self):
        k = KnownLimitations(
            ranked_items=[{"limitation": "x", "frequency": 2, "severity": "High"}]
        )
        assert isinstance(k.ranked_items[0], RankedLimitation)
        assert k.ranked_items[0].severity == "High"

    def test_recommendations_default_empty(self):
        r = Recommendations()
        assert r.prioritized_items == []


class TestNumericMetrics:
    def test_defaults_all_none(self):
        nm = NumericMetrics()
        assert nm.time_to_detect is None
        assert nm.time_to_mitigate is None
        assert nm.action_correctness is None
        assert nm.reasoning_score is None
        assert nm.hallucination_score is None
        assert nm.input_tokens is None
        assert nm.output_tokens is None
        assert nm.sensitive_data_exposure_count is None
        assert nm.adversarial_input_count is None
        assert nm.authentication_failure_rate is None

    def test_extra_allow(self):
        # model_config = {"extra": "allow"} — unknown fields are retained
        nm = NumericMetrics(some_new_metric={"mean": 3.0})
        dumped = nm.model_dump()
        assert dumped["some_new_metric"] == {"mean": 3.0}

    def test_nested_stats_coercion(self):
        nm = NumericMetrics(action_correctness={"mean": 0.7, "max": 1.0})
        assert isinstance(nm.action_correctness, StatsSummary)
        assert nm.action_correctness.mean == 0.7

    def test_timing_scorecard_coercion(self):
        nm = NumericMetrics(time_to_detect={"category": {"category_score": 0.5}})
        assert isinstance(nm.time_to_detect, TimingScorecard)
        assert nm.time_to_detect.category.category_score == 0.5


class TestFaultCategoryScorecard:
    def test_requires_fault_category(self):
        with pytest.raises(ValidationError):
            FaultCategoryScorecard()

    def test_defaults(self):
        sc = FaultCategoryScorecard(fault_category="network")
        assert sc.fault_category == "network"
        assert sc.faults_tested == []
        assert sc.total_runs == 0
        assert sc.successful_runs == 0
        assert sc.failed_runs == 0
        assert sc.distinct_runs == 0
        assert isinstance(sc.numeric_metrics, NumericMetrics)
        assert sc.derived_metrics == {}
        assert sc.boolean_status_metrics == {}
        assert sc.textual_metrics == {}

    def test_populated_round_trip(self):
        sc = FaultCategoryScorecard(
            fault_category="network",
            faults_tested=["pod-delete"],
            total_runs=10,
            successful_runs=9,
            failed_runs=1,
            distinct_runs=9,
            derived_metrics={"rai_compliance_rate": 0.5},
            boolean_status_metrics={"personal_pii": {"any_detected": False}},
            textual_metrics={"agent_summary": {"consensus_summary": "good"}},
        )
        d = sc.model_dump()
        assert d["faults_tested"] == ["pod-delete"]
        assert d["derived_metrics"]["rai_compliance_rate"] == 0.5
        assert d["boolean_status_metrics"]["personal_pii"]["any_detected"] is False


class TestCertificationScorecard:
    def test_defaults(self):
        cs = CertificationScorecard()
        assert cs.agent_id == ""
        assert cs.agent_name == ""
        assert cs.certification_run_id == ""
        assert cs.total_runs == 0
        assert cs.runs_per_fault == 30
        assert cs.fault_category_scorecards == []
        # created_at default_factory yields an ISO-8601 string
        assert isinstance(cs.created_at, str)
        assert "T" in cs.created_at

    def test_created_at_differs_per_instance_is_string(self):
        a = CertificationScorecard()
        b = CertificationScorecard()
        # Both are valid ISO timestamps (strings); the factory is called per instance
        assert isinstance(a.created_at, str) and isinstance(b.created_at, str)

    def test_nested_category_scorecards_coercion(self):
        cs = CertificationScorecard(
            agent_id="a1",
            fault_category_scorecards=[{"fault_category": "net"}],
        )
        assert isinstance(cs.fault_category_scorecards[0], FaultCategoryScorecard)
        assert cs.fault_category_scorecards[0].fault_category == "net"


class TestTokenUsage:
    def test_defaults(self):
        t = TokenUsage()
        assert t.input_tokens == 0
        assert t.output_tokens == 0
        assert t.total_tokens == 0

    def test_add_accumulates(self):
        t = TokenUsage(input_tokens=1, output_tokens=2, total_tokens=3)
        t.add({"input_tokens": 10, "output_tokens": 20, "total_tokens": 30})
        assert t.input_tokens == 11
        assert t.output_tokens == 22
        assert t.total_tokens == 33

    def test_add_missing_keys_default_zero(self):
        t = TokenUsage()
        t.add({"input_tokens": 5})
        assert t.input_tokens == 5
        assert t.output_tokens == 0
        assert t.total_tokens == 0

    def test_to_dict(self):
        t = TokenUsage(input_tokens=4, output_tokens=6, total_tokens=10)
        assert t.to_dict() == {
            "input_tokens": 4,
            "output_tokens": 6,
            "total_tokens": 10,
        }
