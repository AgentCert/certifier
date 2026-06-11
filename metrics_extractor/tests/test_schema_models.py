"""Unit tests for metrics_extractor schema models.

Covers:
- metrics_extractor/schema/data_models.py: TokenUsage, ExtractionResult
- metrics_extractor/schema/metrics_model.py: BaseModelWrapper, enums,
  ToolCall, FaultInfo, MetricsExtractionResult, the LLM extraction models,
  ClaimClassification, JudgedClaim, CombinedStepJudgment, CombinedJudgeResponse
"""

import pytest

from metrics_extractor.schema.data_models import ExtractionResult, TokenUsage
from metrics_extractor.schema.metrics_model import (
    BaseModelWrapper,
    ClaimClassification,
    CombinedJudgeResponse,
    CombinedStepJudgment,
    FaultInfo,
    JudgedClaim,
    LLMQualitativeExtraction,
    LLMQuantitativeExtraction,
    MetricsExtractionResult,
    RAICheckStatus,
    SecurityComplianceStatus,
    ToolCall,
)


# ---------------------------------------------------------------------------
# TokenUsage (dataclass)
# ---------------------------------------------------------------------------

class TestTokenUsage:
    def test_defaults_zero(self):
        tu = TokenUsage()
        assert tu.input_tokens == 0
        assert tu.output_tokens == 0
        assert tu.total_tokens == 0

    def test_add_accumulates(self):
        tu = TokenUsage()
        tu.add({"input_tokens": 10, "output_tokens": 3, "total_tokens": 13})
        tu.add({"input_tokens": 5, "output_tokens": 2, "total_tokens": 7})
        assert tu.input_tokens == 15
        assert tu.output_tokens == 5
        assert tu.total_tokens == 20

    def test_add_missing_keys_default_zero(self):
        tu = TokenUsage()
        tu.add({"input_tokens": 4})
        assert tu.input_tokens == 4
        assert tu.output_tokens == 0
        assert tu.total_tokens == 0

    def test_to_dict(self):
        tu = TokenUsage(input_tokens=1, output_tokens=2, total_tokens=3)
        assert tu.to_dict() == {
            "input_tokens": 1,
            "output_tokens": 2,
            "total_tokens": 3,
        }


# ---------------------------------------------------------------------------
# ExtractionResult (dataclass)
# ---------------------------------------------------------------------------

class TestExtractionResult:
    def test_to_dict_uses_model_to_dict(self):
        quant = LLMQuantitativeExtraction(agent_name="ops", trajectory_steps=5)
        qual = LLMQualitativeExtraction(agent_summary="did things")
        res = ExtractionResult(quantitative=quant, qualitative=qual)
        d = res.to_dict()
        assert d["quantitative"]["agent_name"] == "ops"
        assert d["quantitative"]["trajectory_steps"] == 5
        assert d["qualitative"]["agent_summary"] == "did things"
        assert d["token_usage"] == {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        # mongodb_document_id absent by default
        assert "mongodb_document_id" not in d

    def test_to_dict_includes_mongo_id_when_set(self):
        res = ExtractionResult(
            quantitative=LLMQuantitativeExtraction(),
            qualitative=LLMQualitativeExtraction(),
            mongodb_document_id="abc123",
        )
        assert res.to_dict()["mongodb_document_id"] == "abc123"

    def test_default_token_usage_factory(self):
        a = ExtractionResult(
            quantitative=LLMQuantitativeExtraction(),
            qualitative=LLMQualitativeExtraction(),
        )
        b = ExtractionResult(
            quantitative=LLMQuantitativeExtraction(),
            qualitative=LLMQualitativeExtraction(),
        )
        a.token_usage.add({"input_tokens": 9})
        assert b.token_usage.input_tokens == 0  # independent instances


# ---------------------------------------------------------------------------
# BaseModelWrapper
# ---------------------------------------------------------------------------

class TestBaseModelWrapper:
    def test_get_returns_attr(self):
        tc = ToolCall(tool_name="kubectl")
        assert tc.get("tool_name") == "kubectl"

    def test_get_missing_returns_default(self):
        tc = ToolCall(tool_name="kubectl")
        assert tc.get("nonexistent", "fallback") == "fallback"
        assert tc.get("nonexistent") is None

    def test_to_dict_excludes_none(self):
        tc = ToolCall(tool_name="kubectl")  # arguments/response_summary None
        d = tc.to_dict()
        assert d["tool_name"] == "kubectl"
        assert "arguments" not in d  # None excluded
        assert d["was_successful"] is True

    def test_to_json_roundtrip(self):
        import json
        tc = ToolCall(tool_name="kubectl", was_successful=False)
        parsed = json.loads(tc.to_json())
        assert parsed["tool_name"] == "kubectl"
        assert parsed["was_successful"] is False


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TestEnums:
    def test_rai_values(self):
        assert RAICheckStatus.PASSED.value == "Passed"
        assert RAICheckStatus.FAILED.value == "Failed"
        assert RAICheckStatus.NOT_EVALUATED.value == "Not Evaluated"

    def test_security_values(self):
        assert SecurityComplianceStatus.COMPLIANT.value == "Compliant"
        assert SecurityComplianceStatus.NON_COMPLIANT.value == "Non-Compliant"
        assert SecurityComplianceStatus.PARTIALLY_COMPLIANT.value == "Partially Compliant"

    def test_claim_classification_members(self):
        assert ClaimClassification.GROUNDED.value == "GROUNDED"
        assert ClaimClassification.FABRICATED_TOOL_CALL.value == "FABRICATED_TOOL_CALL"
        assert ClaimClassification("NON_OPERATIONAL") is ClaimClassification.NON_OPERATIONAL


# ---------------------------------------------------------------------------
# ToolCall / FaultInfo
# ---------------------------------------------------------------------------

class TestToolCallFaultInfo:
    def test_toolcall_defaults(self):
        tc = ToolCall(tool_name="get_pods")
        assert tc.arguments is None
        assert tc.response_summary is None
        assert tc.was_successful is True
        assert tc.timestamp is None

    def test_toolcall_requires_name(self):
        with pytest.raises(Exception):
            ToolCall()

    def test_faultinfo_all_required(self):
        fi = FaultInfo(fault_type="Misconfig", target_service="cart", namespace="sock-shop")
        assert fi.fault_type == "Misconfig"
        assert fi.namespace == "sock-shop"

    def test_faultinfo_missing_field_raises(self):
        with pytest.raises(Exception):
            FaultInfo(fault_type="Misconfig")


# ---------------------------------------------------------------------------
# MetricsExtractionResult
# ---------------------------------------------------------------------------

class TestMetricsExtractionResult:
    def test_defaults(self):
        r = MetricsExtractionResult(success=True)
        assert r.success is True
        assert r.metrics is None
        assert r.errors == []
        assert r.warnings == []

    def test_with_errors(self):
        r = MetricsExtractionResult(success=False, errors=["boom"], warnings=["careful"])
        assert r.errors == ["boom"]
        assert r.warnings == ["careful"]


# ---------------------------------------------------------------------------
# LLMQuantitativeExtraction
# ---------------------------------------------------------------------------

class TestLLMQuantitativeExtraction:
    def test_defaults(self):
        q = LLMQuantitativeExtraction()
        assert q.agent_name is None
        assert q.fault_detected == "Unknown"
        assert q.trajectory_steps == 0
        assert q.input_tokens == 0
        assert q.output_tokens == 0
        assert q.tool_calls == []
        assert q.detection_success is None

    def test_extra_allowed(self):
        # model_config extra=allow on quantitative model
        q = LLMQuantitativeExtraction(some_unexpected_field="x")
        assert q.some_unexpected_field == "x"

    def test_tool_calls_independent_default(self):
        a = LLMQuantitativeExtraction()
        b = LLMQuantitativeExtraction()
        a.tool_calls.append({"tool_name": "t"})
        assert b.tool_calls == []

    def test_serialization_roundtrip(self):
        q = LLMQuantitativeExtraction(
            agent_name="ops", time_to_detect=12.5, detection_success=1,
            tool_calls=[{"tool_name": "kubectl"}],
        )
        dumped = q.model_dump()
        restored = LLMQuantitativeExtraction.model_validate(dumped)
        assert restored.agent_name == "ops"
        assert restored.time_to_detect == pytest.approx(12.5)
        assert restored.tool_calls == [{"tool_name": "kubectl"}]


# ---------------------------------------------------------------------------
# LLMQualitativeExtraction
# ---------------------------------------------------------------------------

class TestLLMQualitativeExtraction:
    def test_defaults(self):
        q = LLMQualitativeExtraction()
        assert q.fairness_check_status == "Not Evaluated"
        assert q.security_compliance_status == "Not Evaluated"
        assert q.agent_summary == ""
        assert q.bias_detected is None
        assert q.hallucination_score is None
        assert q.reasoning_quality_score is None

    def test_populated_fields(self):
        q = LLMQualitativeExtraction(
            agent_summary="summary",
            hallucination_count=2,
            total_response_count=10,
            reasoning_logical_coherence=0.8,
            unsafe_action_detected=True,
        )
        assert q.hallucination_count == 2
        assert q.reasoning_logical_coherence == pytest.approx(0.8)
        assert q.unsafe_action_detected is True


# ---------------------------------------------------------------------------
# JudgedClaim / CombinedStepJudgment / CombinedJudgeResponse
# ---------------------------------------------------------------------------

class TestJudgeModels:
    def test_judged_claim(self):
        jc = JudgedClaim(claim="pod is down", classification=ClaimClassification.GROUNDED)
        assert jc.classification is ClaimClassification.GROUNDED
        assert jc.reasoning == ""

    def test_judged_claim_classification_from_string(self):
        jc = JudgedClaim(claim="x", classification="UNGROUNDED")
        assert jc.classification is ClaimClassification.UNGROUNDED

    def test_combined_step_judgment_defaults(self):
        s = CombinedStepJudgment()
        assert s.step_index == 0
        assert s.claims == []
        assert s.ungrounded_count == 0
        assert s.tool_usage_relevance == pytest.approx(0.5)  # non-zero default
        assert s.logical_coherence == pytest.approx(0.0)

    def test_combined_step_judgment_bounds_enforced(self):
        # ge=0 on counts; le=1 on score dimensions
        with pytest.raises(Exception):
            CombinedStepJudgment(ungrounded_count=-1)
        with pytest.raises(Exception):
            CombinedStepJudgment(logical_coherence=1.5)

    def test_combined_step_judgment_valid(self):
        s = CombinedStepJudgment(
            step_index=3,
            claims=[JudgedClaim(claim="c", classification=ClaimClassification.INFERRED)],
            ungrounded_count=1,
            total_claims=4,
            composite=0.75,
        )
        assert s.step_index == 3
        assert s.total_claims == 4
        assert s.composite == pytest.approx(0.75)

    def test_combined_judge_response_defaults(self):
        r = CombinedJudgeResponse()
        assert r.hallucination_count == 0
        assert r.total_response_count == 0
        assert r.breakdown == {}
        assert r.mean_composite == pytest.approx(0.0)
