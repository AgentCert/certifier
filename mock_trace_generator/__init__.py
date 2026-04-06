"""Multi-fault trace generation package — generates OTEL-compliant Langfuse-format traces
for ITOps agent scenarios with multiple simultaneous Kubernetes faults."""

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
from mock_trace_generator.scripts.trace_generator import (
    MultiFaultTraceGenerator,
)
from mock_trace_generator.scripts.tools_registry import (
    AVAILABLE_TOOLS,
)
