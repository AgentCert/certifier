"""
Unit tests for the Aggregation module.

Tests cover:
- Schema / Pydantic data models (creation, validation, serialization)
- Numeric aggregation functions (compute_stats, compute_numeric_aggregates, etc.)
- LLM Council (config/prompt loading, mocked judge/meta-judge calls)
- Orchestrator helpers (MetricsQueryService, ScorecardAssembler, ScorecardStorage)
"""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Schema / Data Model Tests
# ---------------------------------------------------------------------------


class TestStatsSummary:
    """Tests for StatsSummary Pydantic model."""

    def test_default_creation(self):
        from aggregator.schema.data_models import StatsSummary

        s = StatsSummary()
        assert s.mean is None
        assert s.median is None
        assert s.unit is None

    def test_full_creation(self):
        from aggregator.schema.data_models import StatsSummary

        s = StatsSummary(
            mean=1.5, median=1.0, std_dev=0.5, p95=2.0,
            min=0.5, max=3.0, sum=10.0, mode=1.0,
            unit="seconds", scale="0-10",
        )
        assert s.mean == 1.5
        assert s.unit == "seconds"
        assert s.scale == "0-10"

    def test_serialization(self):
        from aggregator.schema.data_models import StatsSummary

        s = StatsSummary(mean=2.0, median=1.5)
        d = s.model_dump()
        assert d["mean"] == 2.0
        assert d["median"] == 1.5
        assert d["std_dev"] is None


class TestDetectionStatus:
    """Tests for DetectionStatus Pydantic model."""

    def test_default_creation(self):
        from aggregator.schema.data_models import DetectionStatus

        ds = DetectionStatus()
        assert ds.any_detected is None
        assert ds.detection_rate is None

    def test_full_creation(self):
        from aggregator.schema.data_models import DetectionStatus

        ds = DetectionStatus(any_detected=True, detection_rate=0.75)
        assert ds.any_detected is True
        assert ds.detection_rate == 0.75


class TestBooleanAggregates:
    """Tests for BooleanAggregates model."""

    def test_default_creation(self):
        from aggregator.schema.data_models import BooleanAggregates

        ba = BooleanAggregates()
        assert ba.pii_detection.any_detected is None
        assert ba.hallucination_detection.detection_rate is None


class TestDerivedRates:
    """Tests for DerivedRates model."""

    def test_default_creation(self):
        from aggregator.schema.data_models import DerivedRates

        dr = DerivedRates()
        assert dr.fault_detection_success_rate is None
        assert dr.rai_compliance_rate is None

    def test_full_creation(self):
        from aggregator.schema.data_models import DerivedRates

        dr = DerivedRates(
            fault_detection_success_rate=0.9,
            fault_mitigation_success_rate=0.8,
            false_negative_rate=0.1,
            false_positive_rate=0.05,
            rai_compliance_rate=0.95,
            security_compliance_rate=1.0,
        )
        assert dr.fault_detection_success_rate == 0.9
        assert dr.security_compliance_rate == 1.0


class TestTextualConsensus:
    """Tests for TextualConsensus model."""

    def test_default_creation(self):
        from aggregator.schema.data_models import TextualConsensus

        tc = TextualConsensus()
        assert tc.consensus_summary == ""
        assert tc.severity_label is None

    def test_full_creation(self):
        from aggregator.schema.data_models import TextualConsensus

        tc = TextualConsensus(
            consensus_summary="Agent performs well.",
            severity_label="Strong",
            confidence="High",
            inter_judge_agreement=0.9,
        )
        assert tc.consensus_summary == "Agent performs well."
        assert tc.inter_judge_agreement == 0.9


class TestRankedLimitation:
    """Tests for RankedLimitation model."""

    def test_creation(self):
        from aggregator.schema.data_models import RankedLimitation

        rl = RankedLimitation(limitation="Slow detection", frequency=5, severity="High")
        assert rl.limitation == "Slow detection"
        assert rl.frequency == 5

    def test_defaults(self):
        from aggregator.schema.data_models import RankedLimitation

        rl = RankedLimitation(limitation="Some issue")
        assert rl.frequency == 0
        assert rl.severity == "Medium"


class TestPrioritizedRecommendation:
    """Tests for PrioritizedRecommendation model."""

    def test_creation(self):
        from aggregator.schema.data_models import PrioritizedRecommendation

        pr = PrioritizedRecommendation(
            recommendation="Improve alerting", priority="High", frequency=10
        )
        assert pr.recommendation == "Improve alerting"
        assert pr.priority == "High"


class TestKnownLimitations:
    """Tests for KnownLimitations model."""

    def test_empty(self):
        from aggregator.schema.data_models import KnownLimitations

        kl = KnownLimitations()
        assert kl.ranked_items == []

    def test_with_items(self):
        from aggregator.schema.data_models import KnownLimitations, RankedLimitation

        kl = KnownLimitations(ranked_items=[
            RankedLimitation(limitation="Issue A", frequency=3, severity="High"),
        ])
        assert len(kl.ranked_items) == 1
        assert kl.ranked_items[0].limitation == "Issue A"


class TestRecommendations:
    """Tests for Recommendations model."""

    def test_empty(self):
        from aggregator.schema.data_models import Recommendations

        r = Recommendations()
        assert r.prioritized_items == []


class TestFaultCategoryScorecard:
    """Tests for FaultCategoryScorecard model."""

    def test_creation(self):
        from aggregator.schema.data_models import FaultCategoryScorecard

        sc = FaultCategoryScorecard(
            fault_category="pod-kill",
            faults_tested=["pod-delete", "pod-restart"],
            total_runs=30,
        )
        assert sc.fault_category == "pod-kill"
        assert len(sc.faults_tested) == 2
        assert sc.total_runs == 30


class TestCertificationScorecard:
    """Tests for CertificationScorecard model."""

    def test_creation(self):
        from aggregator.schema.data_models import CertificationScorecard

        cs = CertificationScorecard(
            agent_id="agent-001",
            agent_name="TestAgent",
            total_runs=60,
            total_faults_tested=4,
            total_fault_categories=2,
        )
        assert cs.agent_id == "agent-001"
        assert cs.total_runs == 60
        assert cs.created_at  # auto-generated


class TestTokenUsage:
    """Tests for TokenUsage model."""

    def test_default(self):
        from aggregator.schema.data_models import TokenUsage

        tu = TokenUsage()
        assert tu.input_tokens == 0
        assert tu.total_tokens == 0

    def test_add(self):
        from aggregator.schema.data_models import TokenUsage

        tu = TokenUsage()
        tu.add({"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
        assert tu.input_tokens == 100
        assert tu.output_tokens == 50
        assert tu.total_tokens == 150

    def test_add_multiple(self):
        from aggregator.schema.data_models import TokenUsage

        tu = TokenUsage()
        tu.add({"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
        tu.add({"input_tokens": 200, "output_tokens": 100, "total_tokens": 300})
        assert tu.input_tokens == 300
        assert tu.total_tokens == 450

    def test_to_dict(self):
        from aggregator.schema.data_models import TokenUsage

        tu = TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
        d = tu.to_dict()
        assert d == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


# ---------------------------------------------------------------------------
# Numeric Aggregation Tests
# ---------------------------------------------------------------------------


class TestComputeStats:
    """Tests for compute_stats()."""

    def test_empty_values(self):
        from aggregator.scripts.numeric_aggregation import compute_stats

        result = compute_stats([], ["mean", "median"])
        assert result == {}

    def test_single_value(self):
        from aggregator.scripts.numeric_aggregation import compute_stats

        result = compute_stats([5.0], ["mean", "median", "min", "max"])
        assert result["mean"] == 5.0
        assert result["median"] == 5.0
        assert result["min"] == 5.0
        assert result["max"] == 5.0

    def test_single_value_std_dev(self):
        from aggregator.scripts.numeric_aggregation import compute_stats

        result = compute_stats([5.0], ["std_dev"])
        assert result["std_dev"] == 0.0

    def test_multiple_values(self):
        from aggregator.scripts.numeric_aggregation import compute_stats

        result = compute_stats([1.0, 2.0, 3.0, 4.0, 5.0], ["mean", "median", "sum"])
        assert result["mean"] == 3.0
        assert result["median"] == 3.0
        assert result["sum"] == 15.0

    def test_p95(self):
        from aggregator.scripts.numeric_aggregation import compute_stats

        values = list(range(1, 101))
        result = compute_stats([float(v) for v in values], ["p95"])
        assert "p95" in result
        assert result["p95"] >= 95.0

    def test_mode(self):
        from aggregator.scripts.numeric_aggregation import compute_stats

        result = compute_stats([1.0, 2.0, 2.0, 3.0], ["mode"])
        assert result["mode"] == 2.0

    def test_unknown_stat_ignored(self):
        from aggregator.scripts.numeric_aggregation import compute_stats

        result = compute_stats([1.0, 2.0], ["mean", "nonexistent"])
        assert "mean" in result
        assert "nonexistent" not in result


class TestComputeNumericAggregates:
    """Tests for compute_numeric_aggregates()."""

    def _make_docs(self, n=3):
        return [
            {
                "quantitative": {
                    "time_to_detect": 10.0 + i,
                    "time_to_mitigate": 20.0 + i,
                    "tool_selection_accuracy": 0.8 + (i * 0.05),
                    "input_tokens": 1000 + (i * 100),
                    "output_tokens": 500 + (i * 50),
                    "number_of_pii_instances_detected": i,
                    "malicious_prompts_detected": 0,
                },
                "qualitative": {
                    "reasoning_quality_score": 7.0 + (i * 0.5),
                    "hallucination_score": 0.1 * i,
                },
            }
            for i in range(n)
        ]

    def test_basic(self):
        from aggregator.scripts.numeric_aggregation import compute_numeric_aggregates

        docs = self._make_docs()
        result = compute_numeric_aggregates(docs)
        assert "time_to_detect" in result
        assert "unit" in result["time_to_detect"]
        assert result["time_to_detect"]["unit"] == "seconds"
        assert "action_correctness" in result
        assert "response_quality_score" in result
        assert result["response_quality_score"].get("scale") == "0-10"

    def test_empty_docs(self):
        from aggregator.scripts.numeric_aggregation import compute_numeric_aggregates

        result = compute_numeric_aggregates([])
        assert result == {}

    def test_missing_fields(self):
        from aggregator.scripts.numeric_aggregation import compute_numeric_aggregates

        docs = [{"quantitative": {}, "qualitative": {}}]
        result = compute_numeric_aggregates(docs)
        assert result == {}


class TestComputeDerivedRates:
    """Tests for compute_derived_rates()."""

    def test_empty_docs(self):
        from aggregator.scripts.numeric_aggregation import compute_derived_rates

        result = compute_derived_rates([])
        assert result["fault_detection_success_rate"] is None
        assert result["rai_compliance_rate"] is None

    def test_all_detected(self):
        from aggregator.scripts.numeric_aggregation import compute_derived_rates

        docs = [
            {
                "quantitative": {
                    "fault_detected": "pod-kill",
                    "detected_fault_type": "pod-kill",
                    "injected_fault_name": "pod-kill",
                    "agent_fault_mitigation_time": 15.0,
                },
                "qualitative": {
                    "rai_check_status": "Passed",
                    "security_compliance_status": "Compliant",
                },
            }
            for _ in range(5)
        ]
        result = compute_derived_rates(docs)
        assert result["fault_detection_success_rate"] == 1.0
        assert result["fault_mitigation_success_rate"] == 1.0
        assert result["false_negative_rate"] == 0.0
        assert result["rai_compliance_rate"] == 1.0
        assert result["security_compliance_rate"] == 1.0

    def test_partial_detection(self):
        from aggregator.scripts.numeric_aggregation import compute_derived_rates

        docs = [
            {
                "quantitative": {"fault_detected": "pod-kill"},
                "qualitative": {"rai_check_status": "Passed"},
            },
            {
                "quantitative": {"fault_detected": "Unknown"},
                "qualitative": {"rai_check_status": "Failed"},
            },
        ]
        result = compute_derived_rates(docs)
        assert result["fault_detection_success_rate"] == 0.5
        assert result["false_negative_rate"] == 0.5
        assert result["rai_compliance_rate"] == 0.5


class TestComputeBooleanAggregates:
    """Tests for compute_boolean_aggregates()."""

    def test_empty_docs(self):
        from aggregator.scripts.numeric_aggregation import compute_boolean_aggregates

        result = compute_boolean_aggregates([])
        assert result["pii_detection"]["any_detected"] is None
        assert result["hallucination_detection"]["any_detected"] is None

    def test_no_detections(self):
        from aggregator.scripts.numeric_aggregation import compute_boolean_aggregates

        docs = [
            {"quantitative": {"pii_detection": False}, "qualitative": {"hallucination_score": 0}}
            for _ in range(3)
        ]
        result = compute_boolean_aggregates(docs)
        assert result["pii_detection"]["any_detected"] is False
        assert result["pii_detection"]["detection_rate"] == 0.0

    def test_some_detections(self):
        from aggregator.scripts.numeric_aggregation import compute_boolean_aggregates

        docs = [
            {"quantitative": {"pii_detection": True}, "qualitative": {"hallucination_score": 0.5}},
            {"quantitative": {"pii_detection": False}, "qualitative": {"hallucination_score": 0}},
        ]
        result = compute_boolean_aggregates(docs)
        assert result["pii_detection"]["any_detected"] is True
        assert result["pii_detection"]["detection_rate"] == 0.5
        assert result["hallucination_detection"]["any_detected"] is True
        assert result["hallucination_detection"]["detection_rate"] == 0.5


# ---------------------------------------------------------------------------
# Config & Prompt Loading Tests
# ---------------------------------------------------------------------------


class TestConfigLoading:
    """Tests for module config/prompt loading."""

    def test_config_file_exists(self):
        config_path = Path(__file__).resolve().parent.parent / "config" / "aggregation_config.json"
        assert config_path.exists(), f"Config file not found: {config_path}"

    def test_config_valid_json(self):
        config_path = Path(__file__).resolve().parent.parent / "config" / "aggregation_config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        assert "llm_council" in config
        assert "pipeline" in config
        assert config["llm_council"]["council_size"] == 3
        assert config["pipeline"]["aggregated_scorecards_collection"] == "aggregated_scorecards"

    def test_prompt_file_exists(self):
        prompt_path = Path(__file__).resolve().parent.parent / "prompt" / "prompt.yml"
        assert prompt_path.exists(), f"Prompt file not found: {prompt_path}"

    def test_prompt_valid_yaml(self):
        import yaml

        prompt_path = Path(__file__).resolve().parent.parent / "prompt" / "prompt.yml"
        with open(prompt_path, "r", encoding="utf-8") as f:
            prompts = yaml.safe_load(f)
        assert "judge" in prompts
        assert "meta_judge" in prompts
        assert "scorecard_synthesis" in prompts
        assert "system_prompt" in prompts["judge"]
        assert "narrative" in prompts["judge"]


# ---------------------------------------------------------------------------
# LLM Council Tests (Mocked)
# ---------------------------------------------------------------------------


class TestLLMCouncil:
    """Tests for LLMCouncil with mocked LLM calls."""

    def _make_council(self):
        from aggregator.scripts.llm_council import LLMCouncil

        mock_client = AsyncMock()
        mock_client.call_llm = AsyncMock(return_value=(
            {
                "consensus_summary": "Test summary.",
                "severity_label": "Adequate",
                "confidence": "Medium",
            },
            {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        ))
        return LLMCouncil(llm_client=mock_client, council_size=2)

    @pytest.mark.asyncio
    async def test_run_single_judge(self):
        council = self._make_council()
        result, usage = await council._run_single_judge("test prompt", "system", 0)
        assert result["consensus_summary"] == "Test summary."
        assert usage["total_tokens"] == 150

    @pytest.mark.asyncio
    async def test_run_meta_judge(self):
        council = self._make_council()
        council.llm_client.call_llm = AsyncMock(return_value=(
            {
                "consensus_summary": "Meta summary.",
                "severity_label": "Strong",
                "confidence": "High",
                "inter_judge_agreement": 0.9,
            },
            {"input_tokens": 200, "output_tokens": 100, "total_tokens": 300},
        ))
        result, usage = await council._run_meta_judge(
            [{"consensus_summary": "A"}], "test_metric", "test_category", 5
        )
        assert result["consensus_summary"] == "Meta summary."
        assert result["inter_judge_agreement"] == 0.9

    @pytest.mark.asyncio
    async def test_synthesize_textual_metric_empty(self):
        council = self._make_council()
        result, usage = await council.synthesize_textual_metric(
            [], "test_metric", "test_category"
        )
        assert result == {}
        assert usage["total_tokens"] == 0

    @pytest.mark.asyncio
    async def test_synthesize_textual_metric(self):
        council = self._make_council()
        result, usage = await council.synthesize_textual_metric(
            ["narrative 1", "narrative 2"],
            "rai_check_summary",
            "pod-kill",
        )
        assert "consensus_summary" in result
        assert usage["total_tokens"] > 0

    @pytest.mark.asyncio
    async def test_synthesize_list_metric_empty(self):
        council = self._make_council()
        result, usage = await council.synthesize_list_metric(
            [], "limitations", "test_category",
            "template {metric_name} {fault_category} {n} {narratives}"
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_synthesize_list_metric(self):
        from aggregator.scripts.llm_council import LLMCouncil

        mock_client = AsyncMock()
        mock_client.call_llm = AsyncMock(return_value=(
            {
                "ranked_items": [
                    {"limitation": "Slow detection", "frequency": 3, "severity": "High"},
                ]
            },
            {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        ))
        council = LLMCouncil(llm_client=mock_client, council_size=2)
        result, usage = await council.synthesize_list_metric(
            ["item1", "item2"],
            "known_limitations",
            "pod-kill",
            "{metric_name} {fault_category} {n} {narratives}",
        )
        assert "ranked_items" in result
        assert len(result["ranked_items"]) == 1


# ---------------------------------------------------------------------------
# Scorecard Assembler Tests
# ---------------------------------------------------------------------------


class TestScorecardAssembler:
    """Tests for ScorecardAssembler."""

    def test_assemble_category_scorecard(self):
        from aggregator.scripts.aggregation import ScorecardAssembler

        docs = [
            {"fault_name": "pod-delete", "quantitative": {}},
            {"fault_name": "pod-restart", "quantitative": {}},
            {"quantitative": {"injected_fault_name": "pod-kill"}},
        ]
        result = ScorecardAssembler.assemble_category_scorecard(
            fault_category="pod-faults",
            docs=docs,
            numeric_aggs={"time_to_detect": {"mean": 10.0}},
            derived_rates={"fault_detection_success_rate": 0.9},
            boolean_aggs={"pii_detection": {"any_detected": False}},
            textual_aggs={"agent_summary": {"consensus_summary": "Good."}},
        )
        assert result["fault_category"] == "pod-faults"
        assert result["total_runs"] == 3
        assert "pod-delete" in result["faults_tested"]
        assert "pod-kill" in result["faults_tested"]

    def test_assemble_final_scorecard(self):
        from aggregator.scripts.aggregation import ScorecardAssembler

        category_scorecards = [
            {"total_runs": 10, "faults_tested": ["pod-delete"]},
            {"total_runs": 20, "faults_tested": ["network-loss", "network-latency"]},
        ]
        result = ScorecardAssembler.assemble_final_scorecard(
            category_scorecards=category_scorecards,
            agent_id="agent-001",
            agent_name="TestAgent",
            certification_run_id="run-001",
        )
        assert result["agent_id"] == "agent-001"
        assert result["total_runs"] == 30
        assert result["total_faults_tested"] == 3
        assert result["total_fault_categories"] == 2
        assert "created_at" in result


# ---------------------------------------------------------------------------
# Narrative Helpers Tests
# ---------------------------------------------------------------------------


class TestCollectNarratives:
    """Tests for _collect_narratives helper."""

    def test_empty_docs(self):
        from aggregator.scripts.llm_council import _collect_narratives

        result = _collect_narratives([], "qualitative", "agent_summary")
        assert result == []

    def test_collects_non_empty(self):
        from aggregator.scripts.llm_council import _collect_narratives

        docs = [
            {"qualitative": {"agent_summary": "Good agent."}},
            {"qualitative": {"agent_summary": "  "}},
            {"qualitative": {"agent_summary": "Needs improvement."}},
            {"qualitative": {}},
        ]
        result = _collect_narratives(docs, "qualitative", "agent_summary")
        assert len(result) == 2
        assert "Good agent." in result
        assert "Needs improvement." in result

    def test_skips_non_string(self):
        from aggregator.scripts.llm_council import _collect_narratives

        docs = [
            {"qualitative": {"agent_summary": 123}},
            {"qualitative": {"agent_summary": None}},
        ]
        result = _collect_narratives(docs, "qualitative", "agent_summary")
        assert result == []
