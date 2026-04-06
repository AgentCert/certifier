"""
Data models for the Fault Bucketing pipeline.

Contains Pydantic models for LLM classifier I/O, the FaultBucket dataclass,
and parsing helpers for trace event fields.
"""

import ast
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class EventClassification(BaseModel):
    """LLM classifier output for a single trace event."""

    event_id: str = Field(description="The unique identifier of the event being classified.")
    related_faults: List[str] = Field(
        default_factory=list,
        description="One or more fault IDs this event relates to. "
        "A single event can apply to multiple faults. "
        "Can be empty if this is the first detection of a new fault."
    )
    fault_detected: Optional[str] = Field(
        default=None,
        description="If this event represents the agent first recognizing "
        "a fault, the fault name (e.g. 'pod-delete', 'disk-fill'). "
        "This can be a NEW fault not yet in the known faults list. Null otherwise.",
    )
    detected_fault_severity: Optional[str] = Field(
        default=None,
        description="If fault_detected is set, the severity of the fault "
        "(e.g. 'critical', 'high', 'medium', 'low'). Null otherwise.",
    )
    detected_fault_target_pod: Optional[str] = Field(
        default=None,
        description="If fault_detected is set, the target pod or resource "
        "affected by the fault. Null otherwise.",
    )
    detected_fault_namespace: Optional[str] = Field(
        default=None,
        description="If fault_detected is set, the Kubernetes namespace "
        "of the affected resource. Null otherwise.",
    )
    detected_fault_signals: List[str] = Field(
        default_factory=list,
        description="If fault_detected is set, the symptoms or signals "
        "that led to detection (e.g. 'CrashLoopBackOff', 'high latency').",
    )
    fault_mitigated: Optional[str] = Field(
        default=None,
        description="If this event represents the agent confirming that "
        "a fault has been successfully remediated, the fault name/ID. "
        "Null otherwise.",
    )
    has_quantitative_value: bool = Field(
        default=False,
        description="Whether the event contains a measurable numeric value "
        "(latency, count, threshold, etc.).",
    )
    has_qualitative_value: bool = Field(
        default=False,
        description="Whether the event contains a subjective or descriptive "
        "assessment (severity label, root-cause hypothesis, etc.).",
    )
    has_cost_token_details: bool = Field(
        default=False,
        description="Whether the event contains LLM cost or token usage information.",
    )
    confidence: float = Field(
        default=0.0,
        description="Confidence score (0-1) for the classification.",
    )


class BatchClassificationResult(BaseModel):
    """Wrapper for a batch of LLM-classified events."""

    classifications: List[EventClassification] = Field(
        description="List of per-event classification results."
    )


@dataclass
class FaultBucket:
    """Container for events related to a single fault lifecycle."""

    fault_id: str
    fault_name: str
    severity: Optional[str] = None
    target_pod: Optional[str] = None
    namespace: Optional[str] = None
    detection_signals: List[str] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "active"  # "active" or "closed"
    detected_at: Optional[str] = None
    mitigated_at: Optional[str] = None
    injection_timestamp: Optional[str] = None
    ground_truth: Optional[Dict[str, Any]] = None
    ideal_course_of_action: Optional[List[Any]] = None
    ideal_tool_usage_trajectory: Optional[List[Any]] = None
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    agent_version: Optional[str] = None
    experiment_id: Optional[str] = None
    run_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the bucket metadata and events."""
        return {
            "fault_id": self.fault_id,
            "fault_name": self.fault_name,
            "severity": self.severity,
            "target_pod": self.target_pod,
            "namespace": self.namespace,
            "detection_signals": self.detection_signals,
            "status": self.status,
            "detected_at": self.detected_at,
            "mitigated_at": self.mitigated_at,
            "injection_timestamp": self.injection_timestamp,
            "ground_truth": self.ground_truth,
            "ideal_course_of_action": self.ideal_course_of_action,
            "ideal_tool_usage_trajectory": self.ideal_tool_usage_trajectory,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "experiment_id": self.experiment_id,
            "run_id": self.run_id,
            "event_count": len(self.events),
            "events": self.events,
        }


# ---------------------------------------------------------------------------
# Parsing Helpers
# ---------------------------------------------------------------------------

def safe_parse_json(value: Any) -> Any:
    """Parse a JSON string field; return as-is if already parsed or unparseable."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value


def safe_parse_python_literal(value: Any) -> Any:
    """Parse a Python literal string (e.g. from FAULT_DATA input fields).

    Falls back to JSON parsing, then returns the raw value if both fail.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            pass
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            pass
    return value


def parse_iso_timestamp(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string to datetime, or None on failure."""
    if not ts:
        return None
    try:
        ts_clean = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_clean)
    except (ValueError, TypeError):
        return None
