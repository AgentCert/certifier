"""Scripts for the Aggregation module."""

__all__ = [
    "AggregationOrchestrator",
    "MetricsQueryService",
    "ScorecardAssembler",
    "ScorecardStorage",
    "LLMCouncil",
    "compute_boolean_aggregates",
    "compute_derived_rates",
    "compute_numeric_aggregates",
    "compute_stats",
]


def __getattr__(name):
    if name in ("AggregationOrchestrator", "MetricsQueryService", "ScorecardAssembler", "ScorecardStorage"):
        from aggregator.scripts.aggregation import (
            AggregationOrchestrator,
            MetricsQueryService,
            ScorecardAssembler,
            ScorecardStorage,
        )
        return locals()[name]
    if name == "LLMCouncil":
        from aggregator.scripts.llm_council import LLMCouncil
        return LLMCouncil
    if name in ("compute_boolean_aggregates", "compute_derived_rates", "compute_numeric_aggregates", "compute_stats"):
        from aggregator.scripts.numeric_aggregation import (
            compute_boolean_aggregates,
            compute_derived_rates,
            compute_numeric_aggregates,
            compute_stats,
        )
        return locals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
