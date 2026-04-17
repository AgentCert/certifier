"""
Pydantic result models for the 9 hypothesis tests (H-01 through H-09).

Each hypothesis result includes per-category sub-results and an overall summary.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field


# ── Shared base ──────────────────────────────────────────────────────────

class HypothesisResult(BaseModel):
    """Base result for all hypothesis tests."""

    hypothesis_id: str
    hypothesis_name: str
    metric_name: str = ""
    null_hypothesis: str = ""
    alt_hypothesis: str = ""
    alpha: float = 0.05
    overall_assessment: str = ""
    warnings: List[str] = Field(default_factory=list)


# ── Per-category sub-results ─────────────────────────────────────────────

class SubFaultCIResult(BaseModel):
    """Per sub-fault result within a category for H-01."""

    fault_name: str
    n: int = 0
    iqm: float = 0.0
    median: float = 0.0
    mean: float = 0.0
    p95: float = 0.0


class CategoryCIResult(BaseModel):
    """Per-category result for H-01.

    Category-level IQM is the equal-weight average of sub-fault IQMs,
    preventing sub-faults with more detections from dominating.
    """

    category: str
    n: int = 0
    n_sub_faults: int = 0
    iqm: float = 0.0
    median: float = 0.0
    mean: float = 0.0
    p95: float = 0.0
    ci_lower: float = 0.0
    ci_upper: float = 0.0
    ci_width: float = 0.0
    aggregation_method: str = "equal_weight_subfault_iqm"
    sub_faults: List[SubFaultCIResult] = Field(default_factory=list)
    worst_sub_fault: str = ""


class SubFaultRateResult(BaseModel):
    """Per sub-fault rate result within a category for H-02."""

    fault_name: str
    successes: int = 0
    trials: int = 0
    rate: float = 0.0
    wilson_lower: float = 0.0
    wilson_upper: float = 0.0


class CategoryRateResult(BaseModel):
    """Per-category result for H-02.

    Category-level rate is the equal-weight average of sub-fault rates,
    so each fault type contributes equally regardless of run count.
    """

    category: str
    successes: int = 0
    trials: int = 0
    rate: float = 0.0
    wilson_lower: float = 0.0
    wilson_upper: float = 0.0
    certified_floor: float = 0.0
    n_sub_faults: int = 0
    aggregation_method: str = "equal_weight_subfault_rate"
    sub_faults: List[SubFaultRateResult] = Field(default_factory=list)
    worst_sub_fault: str = ""


class SubFaultComparisonDetail(BaseModel):
    """Per sub-fault summary within a category for H-03."""

    fault_name: str
    n: int = 0
    iqm: float = 0.0
    median: float = 0.0
    mean: float = 0.0
    std: float = 0.0


class CategoryComparisonDetail(BaseModel):
    """Per-category detail for H-03.

    All summary stats (pooled_*) are computed on pooled sub-fault data.
    equal_weight_iqm is provided as a reference for H-01 consistency.
    """

    category: str
    n: int = 0
    n_sub_faults: int = 0
    pooled_iqm: float = 0.0
    pooled_median: float = 0.0
    pooled_mean: float = 0.0
    pooled_std: float = 0.0
    equal_weight_iqm: float = 0.0
    is_normal: bool = False
    within_heterogeneous: bool = False
    within_kw_p: Optional[float] = None
    aggregation_method: str = "pooled_per_run"
    sub_faults: List[SubFaultComparisonDetail] = Field(default_factory=list)


class CategoryRateComparisonDetail(BaseModel):
    """Per-category detail for H-04.

    Pooled counts used for the contingency table test.
    Equal-weight rate provided for H-02 consistency.
    """

    category: str
    successes: int = 0
    trials: int = 0
    rate: float = 0.0
    n_sub_faults: int = 0
    equal_weight_rate: float = 0.0
    within_heterogeneous: bool = False
    within_p: Optional[float] = None
    aggregation_method: str = "pooled_counts"
    sub_faults: List[SubFaultRateResult] = Field(default_factory=list)


class PairwiseComparison(BaseModel):
    """Pairwise result for H-03 post-hoc tests."""

    pair: str
    u_statistic: float = 0.0
    p_value_raw: float = 0.0
    p_value_adjusted: float = 0.0
    significant: bool = False
    a12: float = 0.5
    effect_magnitude: str = "negligible"


class CategorySLAResult(BaseModel):
    """Per-category result for H-06."""

    category: str
    n: int = 0
    median: float = 0.0
    wilcoxon_p: Optional[float] = None
    ci_upper: Optional[float] = None
    tost_equivalent: Optional[bool] = None
    tost_p: Optional[float] = None
    km_survival_at_sla: Optional[float] = None
    verdict: str = ""


class CategoryBreachResult(BaseModel):
    """Per-category result for H-07."""

    category: str
    breaches: int = 0
    trials: int = 0
    observed_rate: float = 0.0
    target_rate: float = 0.05
    binomial_p: float = 1.0
    ci_lower: float = 0.0
    ci_upper: float = 0.0
    verdict: str = ""


class CategoryTailResult(BaseModel):
    """Per-category result for H-08."""

    category: str
    p95: float = 0.0
    cvar: float = 0.0
    n_tail: int = 0
    cvar_iqm_ratio: Optional[float] = None
    expected_overshoot: Optional[float] = None
    n_breaches: Optional[int] = None
    risk_level: str = ""


class CategoryDriftResult(BaseModel):
    """Per-category result for H-09."""

    category: str
    cusum_final: float = 0.0
    cusum_alarm: bool = False
    ewma_final: float = 0.0
    ewma_alarm: bool = False
    drift_verdict: str = "STABLE"


# ── Hypothesis result models ────────────────────────────────────────────

class H01Result(HypothesisResult):
    """H-01: Confidence Intervals for Continuous Metrics."""

    hypothesis_id: str = "H-01"
    hypothesis_name: str = "Confidence Intervals for Continuous Metrics"
    null_hypothesis: str = "The observed IQM is a noisy estimate; the true typical performance is unknown."
    alt_hypothesis: str = "The Bootstrap CI provides a reliable bound on the true typical performance."
    per_category: List[CategoryCIResult] = Field(default_factory=list)


class H02Result(HypothesisResult):
    """H-02: Success Rate Estimation with Safety Floor."""

    hypothesis_id: str = "H-02"
    hypothesis_name: str = "Success Rate Estimation with Safety Floor"
    null_hypothesis: str = "The observed success rate may overstate the true rate due to small sample size."
    alt_hypothesis: str = "The Wilson CI lower bound provides a conservative floor on the true success rate."
    per_category: List[CategoryRateResult] = Field(default_factory=list)


class H03Result(HypothesisResult):
    """H-03: Cross-Category Performance Comparison."""

    hypothesis_id: str = "H-03"
    hypothesis_name: str = "Cross-Category Performance Comparison"
    null_hypothesis: str = "Performance is the same across all fault categories."
    alt_hypothesis: str = "At least one fault category has significantly different performance."
    per_category: List[CategoryComparisonDetail] = Field(default_factory=list)
    normality_results: Dict[str, bool] = Field(default_factory=dict)
    test_used: str = "kruskal_wallis"
    omnibus_statistic: float = 0.0
    omnibus_p: float = 1.0
    omnibus_significant: bool = False
    pairwise: List[PairwiseComparison] = Field(default_factory=list)
    correction_method: str = "holm_bonferroni"


class H04Result(HypothesisResult):
    """H-04: Cross-Category Success Rate Uniformity."""

    hypothesis_id: str = "H-04"
    hypothesis_name: str = "Cross-Category Success Rate Uniformity"
    null_hypothesis: str = "Success rates are the same across all fault categories."
    alt_hypothesis: str = "At least one category has a significantly different success rate."
    test_used: str = ""
    statistic: Optional[float] = None
    p_value: float = 1.0
    significant: bool = False
    per_category: List[CategoryRateComparisonDetail] = Field(default_factory=list)
    per_category_rates: Dict[str, float] = Field(default_factory=dict)
    weakest_category: str = ""


class SubFaultConsistencyDetail(BaseModel):
    """Per sub-fault consistency stats for H-05."""

    fault_name: str
    n: int = 0
    mean: float = 0.0
    std: float = 0.0
    cv: float = 0.0
    cv_flag: str = "stable"


class CategoryConsistencyDetail(BaseModel):
    """Per-category detail for H-05.

    CV and Levene's test operate on pooled sub-fault data.
    """

    category: str
    n: int = 0
    pooled_mean: float = 0.0
    pooled_std: float = 0.0
    pooled_cv: float = 0.0
    cv_flag: str = "stable"
    n_sub_faults: int = 0
    within_cv_range: str = ""
    aggregation_method: str = "pooled_per_run"
    sub_faults: List[SubFaultConsistencyDetail] = Field(default_factory=list)


class H05Result(HypothesisResult):
    """H-05: Consistency & Predictability."""

    hypothesis_id: str = "H-05"
    hypothesis_name: str = "Consistency & Predictability"
    null_hypothesis: str = "Variance is equal across all fault categories."
    alt_hypothesis: str = "At least one category has significantly higher variance."
    levene_statistic: float = 0.0
    levene_p: float = 1.0
    variances_equal: bool = True
    per_category: List[CategoryConsistencyDetail] = Field(default_factory=list)
    cv_per_category: Dict[str, float] = Field(default_factory=dict)
    cv_flags: Dict[str, str] = Field(default_factory=dict)
    unstable_categories: List[str] = Field(default_factory=list)


class H06Result(HypothesisResult):
    """H-06: SLA Threshold Compliance."""

    hypothesis_id: str = "H-06"
    hypothesis_name: str = "SLA Threshold Compliance"
    null_hypothesis: str = "The agent's true median performance does NOT meet the SLA."
    alt_hypothesis: str = "The agent's true median performance IS within the SLA."
    sla_threshold: float = 0.0
    per_category: List[CategorySLAResult] = Field(default_factory=list)


class H07Result(HypothesisResult):
    """H-07: SLA Breach Rate Estimation."""

    hypothesis_id: str = "H-07"
    hypothesis_name: str = "SLA Breach Rate Estimation"
    null_hypothesis: str = "The true SLA breach rate is at or above the allowed target."
    alt_hypothesis: str = "The true SLA breach rate is below the allowed target."
    sla_threshold: float = 0.0
    target_rate: float = 0.05
    per_category: List[CategoryBreachResult] = Field(default_factory=list)


class H08Result(HypothesisResult):
    """H-08: Tail Risk Analysis."""

    hypothesis_id: str = "H-08"
    hypothesis_name: str = "Tail Risk Analysis"
    null_hypothesis: str = "Tail outcomes are not disproportionately severe."
    alt_hypothesis: str = "The worst 5% average significantly worse than P95, indicating hidden catastrophic risk."
    quantile_level: float = 0.95
    sla_threshold: Optional[float] = None
    per_category: List[CategoryTailResult] = Field(default_factory=list)


class H09Result(HypothesisResult):
    """H-09: Temporal Stability & Drift Detection."""

    hypothesis_id: str = "H-09"
    hypothesis_name: str = "Temporal Stability & Drift Detection"
    null_hypothesis: str = "Performance is stable over time — no systematic trend."
    alt_hypothesis: str = "Performance is drifting — systematic trend or structural break exists."
    per_category: List[CategoryDriftResult] = Field(default_factory=list)
