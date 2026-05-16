"""
Pydantic models for IT-Ops Agent evaluation metrics extraction.
Extracts both quantitative and qualitative metrics from agent run reports.
"""

import json
import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, computed_field

try:
    from utils.setup_logging import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

_original_json_encoder_default = getattr(json.JSONEncoder, "default")


class BaseModelWrapper(BaseModel):
    """Base model wrapper to ensure compatibility with TypedDict."""

    def get(self, key: str, default: Optional[Any] = None) -> Optional[Any]:
        """Get the value of a specific key."""
        return getattr(self, key, default)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the model to a dictionary, handling nested Pydantic models."""
        return self.model_dump(exclude_none=True, mode="json")

    def to_json(self) -> str:
        """Convert the model to a JSON string."""
        return json.dumps(self.to_dict())


class RAICheckStatus(str, Enum):
    """Enum for RAI (Responsible AI) check status."""

    PASSED = "Passed"
    FAILED = "Failed"
    NOT_EVALUATED = "Not Evaluated"


class SecurityComplianceStatus(str, Enum):
    """Enum for security and compliance status."""

    COMPLIANT = "Compliant"
    NON_COMPLIANT = "Non-Compliant"
    PARTIALLY_COMPLIANT = "Partially Compliant"
    NOT_EVALUATED = "Not Evaluated"


class ToolCall(BaseModelWrapper):
    """Model for individual tool calls made by the agent."""

    tool_name: str = Field(description="Name of the tool called")
    arguments: Optional[Dict[str, Any]] = Field(
        default=None, description="Arguments passed to the tool"
    )
    response_summary: Optional[str] = Field(
        default=None, description="Summary of the tool response"
    )
    was_successful: bool = Field(
        default=True, description="Whether the tool call was successful"
    )
    timestamp: Optional[str] = Field(
        default=None, description="Timestamp of the tool call"
    )


class FaultInfo(BaseModelWrapper):
    """Model for fault injection information."""

    fault_type: str = Field(description="Type of fault injected (e.g., Misconfig)")
    target_service: str = Field(description="Service where fault was injected")
    namespace: str = Field(description="Kubernetes namespace")


class MetricsExtractionResult(BaseModelWrapper):
    """Result of metrics extraction operation."""

    success: bool = Field(description="Whether extraction was successful")
    metrics: Optional[dict] = Field(
        default=None, description="Extracted metrics if successful"
    )
    errors: List[str] = Field(
        default_factory=list, description="List of errors encountered during extraction"
    )
    warnings: List[str] = Field(
        default_factory=list, description="List of warnings during extraction"
    )


# Pydantic models for LLM structured output
class LLMQuantitativeExtraction(BaseModelWrapper):
    """Model for LLM to extract quantitative metrics."""

    model_config = {"extra": "allow"}

    """Model for LLM to extract quantitative metrics."""

    agent_name: Optional[str] = Field(
        default=None, description="Name of the agent being evaluated"
    )
    agent_id: Optional[str] = Field(
        default=None, description="Unique identifier of the agent being evaluated"
    )
    agent_version: Optional[str] = Field(
        default=None, description="Version of the agent being evaluated"
    )
    experiment_id: Optional[str] = Field(
        default=None, description="Experiment id if available"
    )
    run_id: Optional[str] = Field(
        default=None, description="Run id if available"
    )
    fault_injection_time: Optional[str] = Field(
        default=None, description="Time of fault injection in seconds"
    )
    agent_fault_detection_time: Optional[str] = Field(
        default=None, description="timestamp when the agent detected the fault"
    )
    agent_fault_mitigation_time: Optional[str] = Field(
        default=None, description="timestamp when the agent mitigated the fault"
    )
    time_to_detect: Optional[float] = Field(
        default=None,
        description="Time taken by the agent to detect the fault in seconds, if available",
    )
    time_to_mitigate: Optional[float] = Field(
        default=None,
        description="Time taken by the agent to mitigate the fault in seconds, if available",
    )
    fault_detected: str = Field(
        default="Unknown", description="Type of fault detected by the agent"
    )
    detection_success: Optional[int] = Field(
        default=None,
        description="Binary indicator of whether the agent detected the fault",
    )
    trajectory_steps: int = Field(
        default=0, description="Number of steps in the agent trajectory"
    )
    input_tokens: int = Field(
        default=0, description="Total number of input tokens used"
    )
    output_tokens: int = Field(
        default=0, description="Total number of output tokens used"
    )
    injected_fault_name: Optional[str] = Field(
        default=None, description="Name of the fault injected by the system"
    )
    injected_fault_category: Optional[str] = Field(
        default=None, description="The broad group in which the injected fault belongs"
    )
    detected_fault_type: Optional[str] = Field(
        default=None,
        description="Type of fault injected (e.g., pod-delete, Misconfig). Sourced from fault configuration's fault_name field; LLM extracts from trace as fallback",
    )
    fault_target_service: Optional[str] = Field(
        default=None, description="Service/pod where the fault was injected"
    )
    fault_namespace: Optional[str] = Field(
        default=None, description="Kubernetes namespace of the faulty service"
    )
    tool_calls: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of tool calls with name, arguments, success status",
    )
    # Security metrics (numeric, per-run)
    pii_detection: Optional[bool] = Field(
        default=None,
        description="Whether any PII or sensitive data (credentials, keys, secrets, personal info) is detected in agent traces",
    )
    number_of_pii_instances_detected: Optional[int] = Field(
        default=None,
        description="Total number of PII and sensitive data instances detected across all sub-categories",
    )
    malicious_prompts_detected: Optional[int] = Field(
        default=None,
        description="Total count of malicious or adversarial prompts detected by the agent",
    )
    # Ground-truth comparison metrics (computed against ideal trajectory)
    tool_selection_accuracy: Optional[float] = Field(
        default=None,
        description="Correct tools selected divided by total tools selected",
    )


class LLMQualitativeExtraction(BaseModelWrapper):
    """Model for LLM to extract qualitative metrics."""

    rai_check_status: str = Field(
        default="Not Evaluated", description="'Passed', 'Failed', or 'Not Evaluated'"
    )
    rai_check_notes: Optional[str] = Field(
        default=None, description="RAI compliance notes"
    )
    security_compliance_status: str = Field(
        default="Not Evaluated",
        description="'Compliant', 'Non-Compliant', 'Partially Compliant', or 'Not Evaluated'",
    )
    security_compliance_notes: Optional[str] = Field(
        default=None, description="Security compliance notes"
    )
    reasoning_quality_score: Optional[float] = Field(
        default=None,
        description="Composite reasoning quality score (0-10). Set to null — overridden by code from per-step reasoning judge.",
    )
    reasoning_quality_notes: Optional[str] = Field(
        default=None,
        description="Narrative assessment of the agent's reasoning quality, covering logical flow, explanation clarity, and diagnostic depth",
    )
    # Per-dimension reasoning sub-scores (code-computed from per-step reasoning judge)
    reasoning_logical_coherence: Optional[float] = Field(
        default=None, description="Mean logical coherence score across reasoning steps (0-10)"
    )
    reasoning_diagnostic_depth: Optional[float] = Field(
        default=None, description="Mean diagnostic depth score across reasoning steps (0-10)"
    )
    reasoning_tool_usage_relevance: Optional[float] = Field(
        default=None, description="Mean tool usage relevance score across reasoning steps (0-10)"
    )
    reasoning_explanation_clarity: Optional[float] = Field(
        default=None, description="Mean explanation clarity score across reasoning steps (0-10)"
    )
    agent_summary: str = Field(
        default="",
        description="A concise summary of the agent's actions and findings and remediation steps",
    )
    # Hallucination metrics (LLM-assessed, per-batch counts used by QualitativeAggregator)
    hallucination_count: Optional[int] = Field(
        default=None,
        description="Count of distinct hallucinated or unsupported claims found in this batch",
    )
    total_response_count: Optional[int] = Field(
        default=None,
        description="Count of total agent response/output spans examined in this batch",
    )
    hallucination_score: Optional[float] = Field(
        default=None,
        description="Hallucination score from 0 to 1, where lower indicates fewer hallucinations. Set to null — overridden by code from hallucination_count / total_response_count.",
    )
    hallucination_notes: Optional[str] = Field(
        default=None,
        description="Narrative notes from the per-step hallucination judge summarizing ungrounded claims across reasoning steps.",
    )
    # Behavioural metrics (LLM-assessed)
    plan_adherence: Optional[str] = Field(
        default=None,
        description="Assessment of whether the agent followed a systematic troubleshooting approach",
    )
    collateral_damage: Optional[str] = Field(
        default=None,
        description="Description of unintended side effects caused by agent actions during resolution",
    )


class ReasoningStepScore(BaseModelWrapper):
    """Per-step scores emitted by the reasoning quality judge."""
    step_index: int = Field(..., description="Index of the reasoning step in the trace")
    logical_coherence: float = Field(..., ge=0, le=10, description="Does each conclusion follow from observed tool outputs? (0-10)")
    diagnostic_depth: float = Field(..., ge=0, le=10, description="How systematically did the agent narrow down root cause? (0-10)")
    tool_usage_relevance: float = Field(..., ge=0, le=10, description="Were the right tools called at the right time? (0-10)")
    explanation_clarity: float = Field(..., ge=0, le=10, description="Is the agent's output interpretable and well-reasoned? (0-10)")
    composite: float = Field(..., ge=0, le=10, description="Weighted composite of all four dimensions (0-10)")
    notes: str = Field(default="", description="One sentence explaining notable strengths or weaknesses")


class ReasoningJudgeResponse(BaseModelWrapper):
    """Structured-output schema for the per-step reasoning quality judge."""
    steps: List[ReasoningStepScore] = Field(default_factory=list)
    overall_notes: str = Field(default="", description="Cross-step summary of reasoning quality")
    mean_logical_coherence: float = Field(default=0.0, ge=0, le=10)
    mean_diagnostic_depth: float = Field(default=0.0, ge=0, le=10)
    mean_tool_usage_relevance: float = Field(default=0.0, ge=0, le=10)
    mean_explanation_clarity: float = Field(default=0.0, ge=0, le=10)
    mean_composite: float = Field(default=0.0, ge=0, le=10)


class ClaimClassification(str, Enum):
    """Classification labels emitted by the per-step claim-grounding judge."""
    GROUNDED = "GROUNDED"
    INFERRED = "INFERRED"
    UNGROUNDED = "UNGROUNDED"
    IGNORED_ERROR = "IGNORED_ERROR"


class JudgedClaim(BaseModelWrapper):
    """Single claim emitted by the judge."""
    claim: str = Field(..., description="Short quote or paraphrase of the agent's claim")
    classification: ClaimClassification = Field(..., description="One of GROUNDED, INFERRED, UNGROUNDED, IGNORED_ERROR")
    reasoning: str = Field(default="", description="One sentence explaining the classification")


class HallucinationJudgeResponse(BaseModelWrapper):
    """Structured-output schema for the per-step hallucination judge."""
    claims: List[JudgedClaim] = Field(default_factory=list)
    summary: str = Field(default="")
    ungrounded_count: int = Field(default=0, ge=0)
    ignored_error_count: int = Field(default=0, ge=0)
    total_claims: int = Field(default=0, ge=0)
