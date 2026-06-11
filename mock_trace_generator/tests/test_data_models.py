"""Unit tests for mock_trace_generator.schema.data_models Pydantic models."""

import pytest
from pydantic import ValidationError

from mock_trace_generator.schema.data_models import (
    ClusterScanResult,
    FaultDefinition,
    FaultInvestigationResult,
    FaultPriority,
    FinalStabilityCheck,
    MultiFaultScenario,
    PostRemediationCheck,
    RemediationResult,
    SingleFaultDetail,
    ToolCallDetail,
    TriageDecision,
)


def _single_fault_detail(**overrides):
    base = dict(
        fault_name="pod-delete",
        target_pod_prefix="myapp-6f8d-",
        target_namespace="default",
        severity="high",
        symptoms=["pod missing"],
        detection_signals=["CrashLoopBackOff"],
        log_excerpts=["line1", "line2"],
        resource_metrics={"cpu": 0.9},
        remediation_tools=["k8s_pods_delete"],
        remediation_actions=["delete pod"],
        typical_ttd_seconds=5.0,
        typical_ttr_seconds=30.0,
    )
    base.update(overrides)
    return SingleFaultDetail(**base)


def _tool_call(**overrides):
    base = dict(
        tool_key="k8s_pods_log",
        tool_name="Pods: Log",
        input_params={"namespace": "default", "pod": "myapp"},
        raw_output="some\noutput",
        agent_reasoning="thinking",
        anomalies_found=["err"],
    )
    base.update(overrides)
    return ToolCallDetail(**base)


class TestFaultDefinition:
    def test_construction(self):
        fd = FaultDefinition(name="pod-delete", description="Deletes a pod")
        assert fd.name == "pod-delete"
        assert fd.description == "Deletes a pod"

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            FaultDefinition(name="pod-delete")

    def test_roundtrip_serialization(self):
        fd = FaultDefinition(name="a", description="b")
        dumped = fd.model_dump()
        assert dumped == {"name": "a", "description": "b"}
        assert FaultDefinition(**dumped) == fd

    def test_type_coercion_int_to_str(self):
        # Pydantic v2 does not coerce int->str in strict-ish default; expect error.
        with pytest.raises(ValidationError):
            FaultDefinition(name=123, description="x")


class TestSingleFaultDetail:
    def test_construction_all_fields(self):
        sfd = _single_fault_detail()
        assert sfd.fault_name == "pod-delete"
        assert sfd.symptoms == ["pod missing"]
        assert sfd.resource_metrics == {"cpu": 0.9}
        assert isinstance(sfd.typical_ttd_seconds, float)

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            SingleFaultDetail(fault_name="x")

    def test_float_coercion_from_int(self):
        sfd = _single_fault_detail(typical_ttd_seconds=5, typical_ttr_seconds=30)
        assert sfd.typical_ttd_seconds == 5.0
        assert isinstance(sfd.typical_ttd_seconds, float)

    def test_resource_metrics_accepts_arbitrary_dict(self):
        sfd = _single_fault_detail(resource_metrics={"nested": {"a": [1, 2]}})
        assert sfd.resource_metrics["nested"]["a"] == [1, 2]


class TestMultiFaultScenario:
    def test_construction_with_nested_models(self):
        scenario = MultiFaultScenario(
            cluster_name="prod-us-east-1",
            affected_namespaces=["default", "kube-system"],
            fault_scenarios=[_single_fault_detail()],
            cross_fault_interactions=["a impacts b"],
            overall_severity="critical",
            triage_order=["pod-delete"],
        )
        assert scenario.cluster_name == "prod-us-east-1"
        assert len(scenario.fault_scenarios) == 1
        assert isinstance(scenario.fault_scenarios[0], SingleFaultDetail)

    def test_nested_dict_is_coerced_to_model(self):
        scenario = MultiFaultScenario(
            cluster_name="c",
            affected_namespaces=[],
            fault_scenarios=[_single_fault_detail().model_dump()],
            cross_fault_interactions=[],
            overall_severity="high",
            triage_order=[],
        )
        assert isinstance(scenario.fault_scenarios[0], SingleFaultDetail)

    def test_serialization_roundtrip(self):
        scenario = MultiFaultScenario(
            cluster_name="c",
            affected_namespaces=["ns"],
            fault_scenarios=[_single_fault_detail()],
            cross_fault_interactions=["x"],
            overall_severity="medium",
            triage_order=["pod-delete"],
        )
        dumped = scenario.model_dump()
        rebuilt = MultiFaultScenario(**dumped)
        assert rebuilt == scenario


class TestFaultPriority:
    def test_construction_and_bool(self):
        fp = FaultPriority(
            fault_name="pod-delete",
            priority=1,
            severity="high",
            reason="most severe",
            blocks_other_faults=True,
        )
        assert fp.priority == 1
        assert fp.blocks_other_faults is True

    def test_priority_must_be_int(self):
        with pytest.raises(ValidationError):
            FaultPriority(
                fault_name="x",
                priority="not-an-int",
                severity="low",
                reason="r",
                blocks_other_faults=False,
            )


class TestTriageDecision:
    def test_construction(self):
        td = TriageDecision(
            reasoning_text="because",
            prioritized_faults=[
                FaultPriority(
                    fault_name="f1",
                    priority=1,
                    severity="high",
                    reason="r",
                    blocks_other_faults=False,
                )
            ],
            estimated_total_remediation_seconds=120.5,
            risk_assessment="high",
        )
        assert td.prioritized_faults[0].fault_name == "f1"
        assert td.estimated_total_remediation_seconds == 120.5


class TestToolCallDetail:
    def test_construction(self):
        tc = _tool_call()
        assert tc.tool_key == "k8s_pods_log"
        assert tc.input_params["pod"] == "myapp"
        assert tc.anomalies_found == ["err"]

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            ToolCallDetail(tool_key="x")


class TestInvestigationRemediationChecks:
    def test_fault_investigation_result(self):
        r = FaultInvestigationResult(
            tool_calls=[_tool_call()],
            diagnosis="d",
            root_cause="rc",
            confidence_score=0.85,
        )
        assert isinstance(r.tool_calls[0], ToolCallDetail)
        assert r.confidence_score == 0.85

    def test_remediation_result(self):
        r = RemediationResult(
            tool_calls=[_tool_call()],
            action_summary="restarted",
            recovery_time_seconds=12.0,
            success=True,
            confidence_score=0.9,
        )
        assert r.success is True
        assert r.recovery_time_seconds == 12.0

    def test_post_remediation_check(self):
        r = PostRemediationCheck(
            tool_calls=[_tool_call()],
            fault_resolved=True,
            system_stable=True,
            reasoning_text="stable",
            confidence_score=0.95,
        )
        assert r.fault_resolved is True
        assert r.system_stable is True

    def test_final_stability_check(self):
        r = FinalStabilityCheck(
            tool_calls=[_tool_call()],
            all_faults_resolved=True,
            cluster_health="healthy",
            reasoning_text="all good",
            confidence_score=1.0,
            recommendations=["add monitoring"],
        )
        assert r.all_faults_resolved is True
        assert r.cluster_health == "healthy"
        assert r.recommendations == ["add monitoring"]


class TestClusterScanResult:
    def test_construction(self):
        r = ClusterScanResult(
            pods_list_output="NAME ...",
            events_output="EVENTS ...",
            nodes_top_output="NODES ...",
            initial_anomalies=["pod down"],
            agent_reasoning="investigate",
        )
        assert r.pods_list_output.startswith("NAME")
        assert r.initial_anomalies == ["pod down"]

    def test_missing_field_raises(self):
        with pytest.raises(ValidationError):
            ClusterScanResult(pods_list_output="x")
