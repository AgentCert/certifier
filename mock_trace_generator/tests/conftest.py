"""Shared fixtures: a deterministic fake LLM client and fault fixtures."""

import random

import pytest

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


def _tool_call(tool_key="k8s_pods_log", reasoning="agent reasoning here"):
    return ToolCallDetail(
        tool_key=tool_key,
        tool_name="Pods: Log",
        input_params={"namespace": "default", "pod": "myapp"},
        raw_output="terminal output line 1\nterminal output line 2",
        agent_reasoning=reasoning,
        anomalies_found=["anomaly-A"],
    )


def make_single_fault_detail(name, ns="default"):
    return SingleFaultDetail(
        fault_name=name,
        target_pod_prefix=f"{name}-pod-6f8d-",
        target_namespace=ns,
        severity="high",
        symptoms=["symptom-1", "symptom-2"],
        detection_signals=["CrashLoopBackOff"],
        log_excerpts=["log line 1", "log line 2"],
        resource_metrics={"cpu": 0.95, "mem": 0.80},
        remediation_tools=["k8s_pods_delete", "k8s_pods_log"],
        remediation_actions=["delete pod", "watch restart"],
        typical_ttd_seconds=5.0,
        typical_ttr_seconds=30.0,
    )


class FakeLLMClient:
    """Returns deterministic pydantic instances keyed by requested output_format.

    Records every call so tests can assert on prompt/model usage.
    """

    def __init__(self, fault_names):
        self.fault_names = list(fault_names)
        self.calls = []
        self.closed = False

    async def with_structured_output(
        self, model_name, messages, output_format, system_prompt=None
    ):
        self.calls.append(
            {
                "model_name": model_name,
                "messages": messages,
                "output_format": output_format,
                "system_prompt": system_prompt,
            }
        )
        result = self._build(output_format)
        cost = 0.001
        return result, cost

    async def close(self):
        self.closed = True

    def _build(self, output_format):
        details = [make_single_fault_detail(n) for n in self.fault_names]

        if output_format is MultiFaultScenario:
            return MultiFaultScenario(
                cluster_name="prod-us-east-1",
                affected_namespaces=["default"],
                fault_scenarios=details,
                cross_fault_interactions=["fault A amplifies fault B"],
                overall_severity="critical",
                triage_order=list(self.fault_names),
            )
        if output_format is ClusterScanResult:
            return ClusterScanResult(
                pods_list_output="NAME READY STATUS\nmyapp 0/1 CrashLoopBackOff",
                events_output="WARN BackOff restarting failed container",
                nodes_top_output="NODE CPU MEM\nnode-1 90% 80%",
                initial_anomalies=["pod CrashLoopBackOff"],
                agent_reasoning="multiple anomalies across namespaces",
            )
        if output_format is TriageDecision:
            return TriageDecision(
                reasoning_text="triage reasoning",
                prioritized_faults=[
                    FaultPriority(
                        fault_name=n,
                        priority=i + 1,
                        severity="high",
                        reason="severe",
                        blocks_other_faults=False,
                    )
                    for i, n in enumerate(self.fault_names)
                ],
                estimated_total_remediation_seconds=120.0,
                risk_assessment="high risk if unaddressed",
            )
        if output_format is FaultInvestigationResult:
            return FaultInvestigationResult(
                tool_calls=[_tool_call("k8s_pods_log"), _tool_call("k8s_pods_get")],
                diagnosis="diagnosis text",
                root_cause="root cause text",
                confidence_score=0.88,
            )
        if output_format is RemediationResult:
            return RemediationResult(
                tool_calls=[_tool_call("k8s_pods_delete")],
                action_summary="deleted the pod",
                recovery_time_seconds=8.0,
                success=True,
                confidence_score=0.91,
            )
        if output_format is PostRemediationCheck:
            return PostRemediationCheck(
                tool_calls=[_tool_call("k8s_pods_get")],
                fault_resolved=True,
                system_stable=True,
                reasoning_text="pod is healthy",
                confidence_score=0.93,
            )
        if output_format is FinalStabilityCheck:
            return FinalStabilityCheck(
                tool_calls=[_tool_call("k8s_pods_list"), _tool_call("prom_query")],
                all_faults_resolved=True,
                cluster_health="healthy",
                reasoning_text="cluster stable",
                confidence_score=0.97,
                recommendations=["add alerting"],
            )
        raise AssertionError(f"Unexpected output_format: {output_format}")


@pytest.fixture(autouse=True)
def _seed_rng():
    """Seed RNG before every test for deterministic generation."""
    random.seed(1234)
    yield


@pytest.fixture
def faults():
    return [
        FaultDefinition(name="pod-delete", description="Deletes a running pod"),
        FaultDefinition(name="disk-fill", description="Fills the node disk"),
    ]


@pytest.fixture
def fake_llm(faults):
    return FakeLLMClient([f.name for f in faults])
