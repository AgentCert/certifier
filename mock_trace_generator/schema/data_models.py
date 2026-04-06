"""Pydantic data models for multi-fault trace generation."""

from typing import Any, Dict, List

from pydantic import BaseModel, Field


class FaultDefinition(BaseModel):
    """Input definition for a single fault to inject."""

    name: str = Field(description="Short fault identifier (e.g., 'pod-delete')")
    description: str = Field(description="Human-readable description of the fault")


class SingleFaultDetail(BaseModel):
    """LLM-generated details for one fault in a multi-fault scenario."""

    fault_name: str = Field(description="Fault identifier matching input")
    target_pod_prefix: str = Field(
        description="Realistic Kubernetes pod name prefix with deployment hash"
    )
    target_namespace: str = Field(description="Namespace for the target pod")
    severity: str = Field(description="Fault severity: 'low', 'medium', 'high', or 'critical'")
    symptoms: List[str] = Field(description="Observable symptoms for this fault")
    detection_signals: List[str] = Field(
        description="Signals agent would notice (e.g., 'pod CrashLoopBackOff', 'high latency')"
    )
    log_excerpts: List[str] = Field(
        description="3-5 realistic log lines during this fault"
    )
    resource_metrics: Dict[str, Any] = Field(
        description="Resource metrics snapshot during the fault"
    )
    remediation_tools: List[str] = Field(
        description="Tool keys from AVAILABLE_TOOLS the agent would use "
        "(e.g., ['k8s_pods_log', 'k8s_pods_delete', 'k8s_resources_scale'])"
    )
    remediation_actions: List[str] = Field(
        description="Ordered remediation steps the agent would take"
    )
    typical_ttd_seconds: float = Field(
        description="Typical time-to-detect in seconds"
    )
    typical_ttr_seconds: float = Field(
        description="Typical time-to-remediate in seconds"
    )


class MultiFaultScenario(BaseModel):
    """LLM-generated scenario for multiple simultaneous faults."""

    cluster_name: str = Field(
        description="Realistic Kubernetes cluster name (e.g., 'prod-us-east-1')"
    )
    affected_namespaces: List[str] = Field(
        description="Namespaces affected by the faults"
    )
    fault_scenarios: List[SingleFaultDetail] = Field(
        description="Detailed scenario for each injected fault"
    )
    cross_fault_interactions: List[str] = Field(
        description="Descriptions of how the faults interact or compound "
        "(e.g., 'network latency amplifies pod restart recovery time')"
    )
    overall_severity: str = Field(
        description="Overall cluster severity: 'medium', 'high', or 'critical'"
    )
    triage_order: List[str] = Field(
        description="Recommended fault remediation order by fault name "
        "(highest priority first)"
    )


class ClusterScanResult(BaseModel):
    """LLM-generated cluster health scan output."""

    pods_list_output: str = Field(
        description="Realistic 'kubectl get pods --all-namespaces' output showing "
        "multiple pods in various states including the faulty ones"
    )
    events_output: str = Field(
        description="Realistic 'kubectl get events --all-namespaces' output showing "
        "warning events related to the injected faults"
    )
    nodes_top_output: str = Field(
        description="Realistic 'kubectl top nodes' output showing resource usage"
    )
    initial_anomalies: List[str] = Field(
        description="Anomalies the agent notices from the scan results"
    )
    agent_reasoning: str = Field(
        description="Agent's internal reasoning about the cluster state and what "
        "to investigate further"
    )


class FaultPriority(BaseModel):
    """Priority assignment for a single fault during triage."""

    fault_name: str = Field(description="Fault identifier")
    priority: int = Field(description="Priority rank (1 = highest)")
    severity: str = Field(description="Assessed severity")
    reason: str = Field(description="Why this fault has this priority")
    blocks_other_faults: bool = Field(
        description="Whether remediating this fault is prerequisite for others"
    )


class TriageDecision(BaseModel):
    """LLM-generated triage reasoning for multiple faults."""

    reasoning_text: str = Field(
        description="Detailed paragraph explaining the agent's triage logic: "
        "why faults are prioritized in a certain order, cross-fault impact analysis"
    )
    prioritized_faults: List[FaultPriority] = Field(
        description="Faults ordered by remediation priority (highest first)"
    )
    estimated_total_remediation_seconds: float = Field(
        description="Agent's estimate of total time to remediate all faults"
    )
    risk_assessment: str = Field(
        description="Overall risk if faults are left unaddressed"
    )


class ToolCallDetail(BaseModel):
    """A single tool invocation by the agent."""

    tool_key: str = Field(
        description="Tool key from AVAILABLE_TOOLS (e.g., 'k8s_pods_log')"
    )
    tool_name: str = Field(
        description="Human-readable tool name (e.g., 'Pods: Log')"
    )
    input_params: Dict[str, Any] = Field(
        description="Parameters passed to the tool (e.g., {'namespace': 'default', 'pod': 'myapp-xyz'})"
    )
    raw_output: str = Field(
        description="Realistic raw output returned by the tool (multi-line terminal output)"
    )
    agent_reasoning: str = Field(
        description="Agent's reasoning about the tool output and next steps"
    )
    anomalies_found: List[str] = Field(
        description="Anomalies found in this tool's output"
    )


class FaultInvestigationResult(BaseModel):
    """LLM-generated investigation result for a specific fault."""

    tool_calls: List[ToolCallDetail] = Field(
        description="Ordered sequence of 3-5 tool calls the agent makes to investigate "
        "this specific fault. Each tool call uses a tool from AVAILABLE_TOOLS."
    )
    diagnosis: str = Field(
        description="Agent's diagnosis after investigation"
    )
    root_cause: str = Field(
        description="Identified root cause of the fault"
    )
    confidence_score: float = Field(
        description="Agent's confidence in the diagnosis (0.0 to 1.0)"
    )


class RemediationResult(BaseModel):
    """LLM-generated remediation execution result for a fault."""

    tool_calls: List[ToolCallDetail] = Field(
        description="Ordered tool calls the agent makes to remediate the fault"
    )
    action_summary: str = Field(
        description="Summary of remediation action taken"
    )
    recovery_time_seconds: float = Field(
        description="Time for the system to recover after remediation"
    )
    success: bool = Field(description="Whether remediation succeeded")
    confidence_score: float = Field(
        description="Confidence that remediation was successful (0.0 to 1.0)"
    )


class PostRemediationCheck(BaseModel):
    """LLM-generated post-remediation verification for a fault."""

    tool_calls: List[ToolCallDetail] = Field(
        description="Tool calls to verify the fault is resolved"
    )
    fault_resolved: bool = Field(description="Whether the fault is confirmed resolved")
    system_stable: bool = Field(description="Whether the system is stable post-remediation")
    reasoning_text: str = Field(
        description="Detailed reasoning confirming stability"
    )
    confidence_score: float = Field(
        description="Confidence in the stability assessment (0.0 to 1.0)"
    )


class FinalStabilityCheck(BaseModel):
    """LLM-generated final cross-fault stability confirmation."""

    tool_calls: List[ToolCallDetail] = Field(
        description="Final verification tool calls across all faults"
    )
    all_faults_resolved: bool = Field(
        description="Whether all faults are confirmed resolved"
    )
    cluster_health: str = Field(
        description="Overall cluster health: 'healthy', 'degraded', or 'critical'"
    )
    reasoning_text: str = Field(
        description="Comprehensive reasoning about cluster-wide stability"
    )
    confidence_score: float = Field(
        description="Overall confidence (0.0 to 1.0)"
    )
    recommendations: List[str] = Field(
        description="Post-incident recommendations for preventing recurrence"
    )
