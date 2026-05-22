"""
Pydantic models for aggregation scorecards.

Defines the structure for fault-category and certification-level scorecards
matching the schema in mock_aggregated_scorecards.json.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class StatsSummary(BaseModel):
    """Computed statistics for a numeric metric."""

    mean: Optional[float] = None
    median: Optional[float] = None
    std_dev: Optional[float] = None
    p95: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    sum: Optional[float] = None
    mode: Optional[float] = None
    unit: Optional[str] = None
    scale: Optional[str] = None


class SubFaultTimingScore(BaseModel):
    """§3 sub-fault grain from the timing scorecard."""

    n_attempted: int = 0
    detection_rate: float = 0.0
    sla_compliance: Optional[float] = None
    weighted_score: Optional[float] = None
    confidence: str = "INSUFFICIENT"


class CategoryTimingScore(BaseModel):
    """§4 category grain from the timing scorecard."""

    n_sub_faults: int = 0
    n_attempted: int = 0
    detection_rate: float = 0.0
    sla_compliance: Optional[float] = None
    category_score: float = 0.0


class CumulativeTimingScore(BaseModel):
    """§5 cumulative (agent-level) grain from the timing scorecard."""

    cumulative_score: float = 0.0
    detection_rate: float = 0.0
    sla_compliance: Optional[float] = None
    n_attempted: int = 0
    quality_flags: List[str] = Field(default_factory=lambda: ["none"])


class TimingScorecard(BaseModel):
    """SLA-aware, detection-weighted scorecard for TTD or TTM."""

    subfault: Dict[str, SubFaultTimingScore] = Field(default_factory=dict)
    category: CategoryTimingScore = Field(default_factory=CategoryTimingScore)


class DetectionStatus(BaseModel):
    """Boolean detection aggregate (PII, hallucination)."""

    any_detected: Optional[bool] = None
    detection_rate: Optional[float] = None


class BooleanAggregates(BaseModel):
    """Aggregated boolean/status metrics."""

    pii_detection: DetectionStatus = Field(default_factory=DetectionStatus)
    hallucination_detection: DetectionStatus = Field(default_factory=DetectionStatus)


class DerivedRates(BaseModel):
    """Derived rate metrics computed from per-run boolean/status fields."""

    fault_detection_success_rate: Optional[float] = None
    fault_mitigation_success_rate: Optional[float] = None
    false_negative_rate: Optional[float] = None
    false_positive_rate: Optional[float] = None
    rai_compliance_rate: Optional[float] = None
    security_compliance_rate: Optional[float] = None


class TextualConsensus(BaseModel):
    """LLM Council consensus for a textual metric."""

    consensus_summary: str = ""
    severity_label: Optional[str] = None
    confidence: Optional[str] = None
    inter_judge_agreement: Optional[float] = None


class RankedLimitation(BaseModel):
    """A single known limitation entry."""

    limitation: str
    frequency: int = 0
    severity: str = "Medium"


class PrioritizedRecommendation(BaseModel):
    """A single recommendation entry."""

    recommendation: str
    priority: str = "Medium"
    frequency: int = 0


class KnownLimitations(BaseModel):
    """Ranked list of agent limitations."""

    ranked_items: List[RankedLimitation] = Field(default_factory=list)


class Recommendations(BaseModel):
    """Prioritized list of improvement recommendations."""

    prioritized_items: List[PrioritizedRecommendation] = Field(default_factory=list)


class NumericMetrics(BaseModel):
    """Typed numeric metrics for a fault-category scorecard."""

    time_to_detect: Optional[TimingScorecard] = None
    time_to_mitigate: Optional[TimingScorecard] = None
    action_correctness: Optional[StatsSummary] = None
    response_quality_score: Optional[StatsSummary] = None
    reasoning_score: Optional[StatsSummary] = None
    hallucination_score: Optional[StatsSummary] = None
    input_tokens: Optional[StatsSummary] = None
    output_tokens: Optional[StatsSummary] = None
    number_of_pii_instances_detected: Optional[StatsSummary] = None
    malicious_prompts_detected: Optional[StatsSummary] = None
    authentication_failure_rate: Optional[StatsSummary] = None

    model_config = {"extra": "allow"}


class FaultCategoryScorecard(BaseModel):
    """Aggregated scorecard for a single fault category."""

    fault_category: str
    faults_tested: List[str] = Field(default_factory=list)
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    distinct_runs: int = 0
    numeric_metrics: NumericMetrics = Field(default_factory=NumericMetrics)
    derived_metrics: Dict[str, Optional[float]] = Field(default_factory=dict)
    boolean_status_metrics: Dict[str, Any] = Field(default_factory=dict)
    textual_metrics: Dict[str, Any] = Field(default_factory=dict)


class CertificationScorecard(BaseModel):
    """Top-level certification scorecard combining all fault categories."""

    agent_id: str = ""
    agent_name: str = ""
    certification_run_id: str = ""
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    total_runs: int = 0
    total_successful_runs: int = 0
    total_failed_runs: int = 0
    total_faults_tested: int = 0
    total_fault_categories: int = 0
    runs_per_fault: int = 30
    fault_category_scorecards: List[FaultCategoryScorecard] = Field(
        default_factory=list
    )


class TokenUsage(BaseModel):
    """Tracks LLM token consumption."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: Dict[str, int]) -> None:
        self.input_tokens += other.get("input_tokens", 0)
        self.output_tokens += other.get("output_tokens", 0)
        self.total_tokens += other.get("total_tokens", 0)

    def to_dict(self) -> Dict[str, int]:
        return self.model_dump()
