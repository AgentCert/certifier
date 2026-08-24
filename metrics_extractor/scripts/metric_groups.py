"""metric_groups.py — Pluggable MetricGroup abstraction for Phase 1 extraction.

Each MetricGroup encapsulates a coherent bucket of LLM calls.  Groups declare
which metrics they produce (``provides``) and implement ``execute(context) →
dict``.  The orchestrator (``run_extraction``) selects and runs only the groups
whose ``provides`` set intersects the caller-requested metric names.

Execution order (implicit dependency via context.results):
  1. DeterministicGroup                  — pure Python; zero LLM calls
  2. SpanIdentificationGroup             — 1–3 LLM calls; writes context.results["span_times"]
  3. KubernetesQuantitativeBatchGroup    — B+1 LLM calls; reads context.results["span_times"]
  4. CombinedJudgeGroup                  — S concurrent LLM calls; writes context.results["judge_result"]
  5. KubernetesQualitativeBatchGroup     — B+1 LLM calls; reads context.results["judge_result"]
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional

from metrics_extractor.schema.data_models import ExtractionResult, TokenUsage
from metrics_extractor.schema.metrics_model import (
    LLMQualitativeExtraction,
    LLMQuantitativeExtraction,
)
from metrics_extractor.scripts.span_aggregator import QuantitativeAggregator

logger = logging.getLogger(__name__)


# ── Shared context ─────────────────────────────────────────────────────────────

@dataclass
class ExtractionContext:
    """Shared state threaded through all MetricGroup.execute() calls."""
    spans: List[Dict[str, Any]]
    bucket_metadata: Dict[str, Any]
    extractor: Any  # TraceMetricsExtractor — kept as Any to avoid circular import
    results: Dict[str, Any] = field(default_factory=dict)


# ── Abstract base ──────────────────────────────────────────────────────────────

class MetricGroup(ABC):
    """Unit of Phase 1 extraction that owns a coherent set of LLM calls.

    Subclasses must declare ``provides: ClassVar[List[str]]`` listing every
    metric key they may return from ``execute()``.  Groups are stateless; all
    mutable state lives in ``ExtractionContext``.

    ``requires`` lists metric names (provided by other groups) whose producing
    group must run before this one.  The engine uses this to extend the selected
    group set automatically when a partial request would otherwise omit a
    dependency, preventing duplicate LLM calls and result inconsistency.
    """

    provides: ClassVar[List[str]]
    requires: ClassVar[List[str]] = []

    @abstractmethod
    async def execute(self, context: ExtractionContext) -> Dict[str, Any]:
        """Run this group's extraction and return a flat metric name → value dict."""
        ...


# ── Concrete groups ────────────────────────────────────────────────────────────

class DeterministicGroup(MetricGroup):
    """Pure-Python metrics derived directly from span data.  Zero LLM calls."""

    provides: ClassVar[List[str]] = [
        "input_tokens",
        "output_tokens",
        "tool_calls",
        "trajectory_steps",
        "personal_pii_detected",
        "sensitive_data_exposure_count",
    ]

    async def execute(self, context: ExtractionContext) -> Dict[str, Any]:
        spans = context.spans
        token_metrics = QuantitativeAggregator.extract_token_and_tool_metrics(spans)
        prescan = QuantitativeAggregator.prescan_spans_for_sensitive_data(spans)

        # Cache prescan so KubernetesQuantitativeBatchGroup can apply it as a floor.
        context.results["prescan"] = prescan

        return {
            "input_tokens": token_metrics.get("input_tokens", 0),
            "output_tokens": token_metrics.get("output_tokens", 0),
            "tool_calls": token_metrics.get("tool_calls", []),
            "trajectory_steps": len(spans),
            "personal_pii_detected": prescan.get("pii_detected"),
            "sensitive_data_exposure_count": prescan.get("pii_instance_count", 0),
        }


class SpanIdentificationGroup(MetricGroup):
    """LLM-identified detection/mitigation spans and TTD/TTR arithmetic."""

    provides: ClassVar[List[str]] = [
        "agent_fault_detection_time",
        "agent_fault_mitigation_time",
        "time_to_detect",
        "time_to_mitigate",
        "detection_success",
    ]

    async def execute(self, context: ExtractionContext) -> Dict[str, Any]:
        extractor = context.extractor
        spans = context.spans

        # LLM call A: identify which spans contain detection/mitigation events
        span_times = await extractor._identify_detection_mitigation_spans(spans)

        # LLM calls B0-B1 (0–2 calls): cross-validate against bucket timestamps
        validated = await extractor._validate_bucket_timestamps_with_llm(spans)
        span_times.update(validated)

        # Cache so KubernetesQuantitativeBatchGroup can skip these calls
        context.results["span_times"] = span_times

        detect_ts: Optional[str] = span_times.get("agent_fault_detection_time")
        mitigate_ts: Optional[str] = span_times.get("agent_fault_mitigation_time")
        inject_ts: Optional[str] = (context.bucket_metadata or {}).get("injection_timestamp")

        return {
            "agent_fault_detection_time": detect_ts,
            "agent_fault_mitigation_time": mitigate_ts,
            "detection_success": 1 if detect_ts else 0,
            "time_to_detect": _delta_seconds(inject_ts, detect_ts),
            "time_to_mitigate": _delta_seconds(inject_ts, mitigate_ts),
        }


class KubernetesQuantitativeBatchGroup(MetricGroup):
    """Batched LLM quantitative extraction followed by a text-consolidation call."""

    def __init__(self, domain_vocabulary: Optional[Dict[str, str]] = None) -> None:
        self._domain_vocabulary = domain_vocabulary

    # SpanIdentificationGroup must run first so _aggregate_quantitative_metrics can
    # consume precomputed span_times from context.results instead of making a
    # duplicate _identify_detection_mitigation_spans LLM call.
    requires: ClassVar[List[str]] = [
        "agent_fault_detection_time",
        "agent_fault_mitigation_time",
    ]

    provides: ClassVar[List[str]] = [
        "fault_detected",
        "detected_fault_type",
        "fault_target_service",
        "fault_namespace",
        "adversarial_input_count",
        "tool_selection_accuracy",
        # agent / fault metadata consolidated by the LLM then code-overridden
        "agent_name",
        "agent_id",
        "agent_version",
        "experiment_id",
        "run_id",
        "fault_injection_time",
        "injected_fault_name",
        "injected_fault_category",
        # may supersede DeterministicGroup's prescan value with max(prescan, batch sum)
        "sensitive_data_exposure_count",
    ]

    async def execute(self, context: ExtractionContext) -> Dict[str, Any]:
        extractor = context.extractor
        spans = context.spans

        batches = extractor._create_batches(spans)
        total_batches = len(batches)
        partial_metrics: List[Dict[str, Any]] = []
        for i, batch in enumerate(batches, 1):
            logger.info("KubernetesQuantitativeBatchGroup: processing batch %d/%d", i, total_batches)
            metrics = await extractor._extract_batch_quantitative(
                batch, i, total_batches, domain_vocabulary=self._domain_vocabulary
            )
            partial_metrics.append(metrics)

        # Inject prescan and token metrics so the aggregator can use them
        prescan = context.results.get("prescan")
        if prescan:
            extractor.quant_aggregator._prescan_result = prescan
        extractor.quant_aggregator._span_metrics = (
            QuantitativeAggregator.extract_token_and_tool_metrics(spans)
        )

        # Aggregation + LLM text consolidation.
        # Reuse span_times computed by SpanIdentificationGroup when available.
        span_times: Optional[Dict[str, Optional[str]]] = context.results.get("span_times")
        quant: LLMQuantitativeExtraction = await extractor._aggregate_quantitative_metrics(
            partial_metrics,
            len(spans),
            spans,
            precomputed_span_times=span_times,
        )
        return quant.model_dump(mode="json")


class CombinedJudgeGroup(MetricGroup):
    """Per-step hallucination + reasoning quality judge (S concurrent LLM calls).

    The ``domain_vocabulary`` parameter controls which domain-specific terms get
    substituted into the judge prompt template.  Pass ``None`` (the default) to
    use the built-in Kubernetes vocabulary defined in ``combined_judge.py``.
    """

    def __init__(self, domain_vocabulary: Optional[Dict[str, str]] = None) -> None:
        self._domain_vocabulary = domain_vocabulary

    provides: ClassVar[List[str]] = [
        "hallucination_count",
        "total_response_count",
        "hallucination_score",
        "hallucination_notes",
        "hallucination_ungrounded_external_count",
        "hallucination_fabricated_tool_count",
        "hallucination_trajectory_deviation_count",
        "hallucination_non_operational_count",
        "reasoning_quality_score",
        "reasoning_logical_coherence",
        "reasoning_diagnostic_depth",
        "reasoning_tool_usage_relevance",
        "reasoning_explanation_clarity",
        "reasoning_quality_notes",
    ]

    async def execute(self, context: ExtractionContext) -> Dict[str, Any]:
        from metrics_extractor.scripts.combined_judge import judge_combined

        extractor = context.extractor
        extractor._init_llm_client()

        cj = await judge_combined(
            extractor.llm_client,
            {"events": context.spans},
            model="gpt-4o",
            domain_vocabulary=self._domain_vocabulary,
        )
        # Cache so KubernetesQualitativeBatchGroup can skip its own judge_combined call
        context.results["judge_result"] = cj

        result: Dict[str, Any] = {}
        if cj.total_response_count > 0:
            result["hallucination_count"] = cj.hallucination_count
            result["total_response_count"] = cj.total_response_count
            result["hallucination_score"] = round(
                cj.hallucination_count / cj.total_response_count, 3
            )
            if cj.hallucination_notes:
                result["hallucination_notes"] = cj.hallucination_notes
            if cj.breakdown:
                result["hallucination_ungrounded_external_count"] = cj.breakdown.get(
                    "ungrounded_external", 0
                )
                result["hallucination_fabricated_tool_count"] = cj.breakdown.get(
                    "fabricated_tool_calls", 0
                )
                result["hallucination_trajectory_deviation_count"] = cj.breakdown.get(
                    "trajectory_deviations", 0
                )
                result["hallucination_non_operational_count"] = cj.breakdown.get(
                    "non_operational", 0
                )
        else:
            logger.info("CombinedJudgeGroup: no reasoning steps found in trace.")

        if cj.mean_composite > 0:
            result["reasoning_quality_score"] = cj.mean_composite
            result["reasoning_logical_coherence"] = cj.mean_logical_coherence
            result["reasoning_diagnostic_depth"] = cj.mean_diagnostic_depth
            result["reasoning_tool_usage_relevance"] = cj.mean_tool_usage_relevance
            result["reasoning_explanation_clarity"] = cj.mean_explanation_clarity
            if cj.overall_reasoning_notes:
                result["reasoning_quality_notes"] = cj.overall_reasoning_notes

        return result


class KubernetesQualitativeBatchGroup(MetricGroup):
    """Batched LLM qualitative extraction followed by a narrative-synthesis call."""

    # CombinedJudgeGroup must run first so _aggregate_qualitative_metrics can
    # consume the precomputed judge_result from context.results instead of making
    # a duplicate judge_combined call.
    requires: ClassVar[List[str]] = ["hallucination_count"]

    provides: ClassVar[List[str]] = [
        "fairness_check_status",
        "fairness_check_notes",
        "bias_detected",
        "bias_types",
        "guardrail_violation_detected",
        "guardrail_violation_notes",
        "security_compliance_status",
        "security_compliance_notes",
        "sensitive_data_exposure_notes",
        "plan_adherence",
        "agent_summary",
        "collateral_damage",
        "unsafe_action_detected",
    ]

    async def execute(self, context: ExtractionContext) -> Dict[str, Any]:
        extractor = context.extractor
        spans = context.spans

        batches = extractor._create_batches(spans)
        total_batches = len(batches)
        partial_observations: List[Dict[str, Any]] = []
        for i, batch in enumerate(batches, 1):
            logger.info("KubernetesQualitativeBatchGroup: processing batch %d/%d", i, total_batches)
            obs = await extractor._extract_batch_qualitative(batch, i, total_batches)
            partial_observations.append(obs)

        # Narrative synthesis + code override.
        # Reuse the judge result from CombinedJudgeGroup when available.
        judge_result = context.results.get("judge_result")
        qual: LLMQualitativeExtraction = await extractor._aggregate_qualitative_metrics(
            partial_observations,
            len(spans),
            spans=spans,
            precomputed_judge_result=judge_result,
        )
        return qual.model_dump(mode="json")


# ── Orchestration ──────────────────────────────────────────────────────────────

_GROUP_ORDER: List[MetricGroup] = [
    DeterministicGroup(),
    SpanIdentificationGroup(),
    KubernetesQuantitativeBatchGroup(),
    CombinedJudgeGroup(),
    KubernetesQualitativeBatchGroup(),
]


def list_available_metrics() -> Dict[str, List[str]]:
    """Return {group_name: [metric, ...]} for all registered groups in execution order."""
    return {type(g).__name__: list(g.provides) for g in _GROUP_ORDER}


# ── Dependency resolution helpers ─────────────────────────────────────────────

def _build_dep_graph(groups: List[MetricGroup]) -> Dict[str, List[str]]:
    """Return {class_name: [required_class_name, ...]} for all groups.

    Each group's ``requires`` list is resolved from metric names to the class
    name of the group that provides each metric.
    """
    metric_to_name: Dict[str, str] = {}
    for g in groups:
        for m in g.provides:
            metric_to_name[m] = type(g).__name__

    graph: Dict[str, List[str]] = {}
    for g in groups:
        name = type(g).__name__
        deps: List[str] = []
        for m in getattr(g, "requires", []):
            producer = metric_to_name.get(m)
            if producer is not None and producer != name and producer not in deps:
                deps.append(producer)
        graph[name] = deps
    return graph


def _check_no_cycles(groups: List[MetricGroup]) -> None:
    """Raise ValueError if ``requires`` declarations form a cycle.

    Called once at module load time so that a misconfigured group fails
    immediately on import rather than hanging at runtime.
    """
    graph = _build_dep_graph(groups)

    UNVISITED, VISITING, VISITED = 0, 1, 2
    state: Dict[str, int] = {name: UNVISITED for name in graph}

    def _dfs(name: str) -> None:
        if state[name] == VISITING:
            raise ValueError(
                f"Cycle in MetricGroup dependency graph: {name!r} depends "
                "(transitively) on itself — fix the 'requires' declarations."
            )
        if state[name] == VISITED:
            return
        state[name] = VISITING
        for dep in graph[name]:
            _dfs(dep)
        state[name] = VISITED

    for name in graph:
        if state[name] == UNVISITED:
            _dfs(name)


# Run cycle check once at import time — catches misconfigured requires early.
_check_no_cycles(_GROUP_ORDER)


def _select_groups(requested: Optional[List[str]]) -> List[MetricGroup]:
    """Return groups to execute for the requested metric names.

    When ``requested`` is None every group runs (unchanged full-extraction
    behavior).

    When a subset is requested, any group selected by ``requested`` whose
    ``requires`` names a metric provided by another group automatically pulls
    that producer group in — so precomputed caches (span_times, judge_result)
    are always available and duplicate LLM calls are avoided.

    The returned list preserves the original ``_GROUP_ORDER`` execution order.
    """
    if requested is None:
        return list(_GROUP_ORDER)

    # Build metric → group instance map (first occurrence wins; avoids duplicates)
    metric_to_group: Dict[str, MetricGroup] = {}
    for g in _GROUP_ORDER:
        for m in g.provides:
            if m not in metric_to_group:
                metric_to_group[m] = g

    requested_set = set(requested)

    # Seed: groups that directly provide a requested metric
    selected: Dict[int, MetricGroup] = {
        id(g): g
        for g in _GROUP_ORDER
        if set(g.provides) & requested_set
    }

    # Expand: pull in any group required by an already-selected group.
    # Loop until no new groups are added (handles transitive deps).
    changed = True
    while changed:
        changed = False
        for g in _GROUP_ORDER:
            if id(g) not in selected:
                continue
            for req_metric in getattr(g, "requires", []):
                producer = metric_to_group.get(req_metric)
                if producer is not None and id(producer) not in selected:
                    selected[id(producer)] = producer
                    changed = True

    # Return in original execution order
    return [g for g in _GROUP_ORDER if id(g) in selected]


async def run_extraction(
    data: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
    requested: Optional[List[str]] = None,
    bucket_metadata: Optional[Dict[str, Any]] = None,
) -> ExtractionResult:
    """Run Phase 1 extraction using the MetricGroup abstraction.

    This is a drop-in replacement for
    ``TraceMetricsExtractor.extract_metrics_async_from_dict``.  When
    ``requested=None`` it runs all five groups and produces identical output.

    Args:
        data:            Bucket dict with an ``"events"`` key, or a bare list of
                         span dicts.
        config:          Optional certifier config dict.
        requested:       Optional list of metric names.  Only groups that provide
                         at least one of these names are executed.  ``None`` runs
                         everything.
        bucket_metadata: Optional explicit bucket metadata dict.  When ``None``
                         the metadata is extracted from *data* (standard bucket
                         format).

    Returns:
        ExtractionResult with quantitative, qualitative, and token_usage fields.
    """
    # Import here to avoid circular imports at module level
    from metrics_extractor.scripts.metrics_extractor_from_trace import TraceMetricsExtractor

    extractor = TraceMetricsExtractor(config=config, bucket_metadata=bucket_metadata)
    extractor._init_llm_client()
    extractor.token_usage = TokenUsage()

    spans = extractor.load_trace_dict(data)

    ctx = ExtractionContext(
        spans=spans,
        bucket_metadata=extractor.bucket_metadata or {},
        extractor=extractor,
    )

    groups = _select_groups(requested)
    merged: Dict[str, Any] = {}
    for group in groups:
        try:
            partial = await group.execute(ctx)
            merged.update(partial)
        except Exception as exc:
            logger.error(
                "MetricGroup %s raised an exception: %s",
                type(group).__name__,
                exc,
                exc_info=True,
            )

    quant_fields = set(LLMQuantitativeExtraction.model_fields)
    qual_fields = set(LLMQualitativeExtraction.model_fields)

    try:
        quant = LLMQuantitativeExtraction.model_validate(
            {k: v for k, v in merged.items() if k in quant_fields}
        )
    except Exception as exc:
        logger.warning("Quantitative model validation failed: %s", exc)
        quant = LLMQuantitativeExtraction()

    try:
        qual = LLMQualitativeExtraction.model_validate(
            {k: v for k, v in merged.items() if k in qual_fields}
        )
    except Exception as exc:
        logger.warning("Qualitative model validation failed: %s", exc)
        qual = LLMQualitativeExtraction()

    return ExtractionResult(
        quantitative=quant,
        qualitative=qual,
        token_usage=extractor.token_usage,
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _delta_seconds(
    from_ts: Optional[str],
    to_ts: Optional[str],
) -> Optional[float]:
    """Return |to_ts − from_ts| in seconds, or None if either is missing/unparseable."""
    if not from_ts or not to_ts:
        return None
    try:
        dt_from = QuantitativeAggregator._parse_timestamp(from_ts)
        dt_to = QuantitativeAggregator._parse_timestamp(to_ts)
        if dt_from is None or dt_to is None:
            return None
        return round(abs((dt_to - dt_from).total_seconds()), 3)
    except Exception:
        return None
