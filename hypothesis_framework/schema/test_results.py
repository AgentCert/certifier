"""
Pydantic result models for all 16 statistical methods.

Each method-specific model inherits from StatisticalTestResult which provides
common fields (method_name, alpha, statistic, p_value, interpretation, warnings).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field


class StatisticalTestResult(BaseModel):
    """Base result shared by all statistical tests."""

    method_name: str
    alpha: Optional[float] = 0.05
    statistic: Optional[float] = None
    p_value: Optional[float] = None
    confidence_interval: Optional[Tuple[float, float]] = None
    interpretation: str = ""
    warnings: List[str] = Field(default_factory=list)


# ── Method 1: Wilson Confidence Interval ──────────────────────────────────

class WilsonCIResult(StatisticalTestResult):
    """Result of Wilson score confidence interval for a binomial proportion."""

    method_name: str = "wilson_ci"
    successes: int = 0
    trials: int = 0
    proportion: float = 0.0
    lower: float = 0.0
    upper: float = 0.0


# ── Method 2: Bootstrap BCa Confidence Interval ──────────────────────────

class BootstrapBCaResult(StatisticalTestResult):
    """Result of bias-corrected and accelerated bootstrap CI."""

    method_name: str = "bootstrap_bca"
    observed_statistic: float = 0.0
    ci_lower: float = 0.0
    ci_upper: float = 0.0
    ci_width: float = 0.0
    n_resamples: int = 10000
    random_state: Optional[int] = None


# ── Method 3: Interquartile Mean ─────────────────────────────────────────

class IQMResult(StatisticalTestResult):
    """Result of interquartile mean computation."""

    method_name: str = "interquartile_mean"
    iqm: float = 0.0
    n_total: int = 0
    n_trimmed: int = 0
    trim_fraction: float = 0.25


# ── Method 4: Shapiro-Wilk Normality Test ────────────────────────────────

class ShapiroWilkResult(StatisticalTestResult):
    """Result of Shapiro-Wilk normality test."""

    method_name: str = "shapiro_wilk"
    is_normal: bool = False
    n: int = 0


# ── Method 5: Kruskal-Wallis H Test ─────────────────────────────────────

class KruskalWallisResult(StatisticalTestResult):
    """Result of Kruskal-Wallis H test across multiple groups."""

    method_name: str = "kruskal_wallis"
    n_groups: int = 0
    group_sizes: List[int] = Field(default_factory=list)
    significant: bool = False


# ── Method 6: Mann-Whitney U Test ───────────────────────────────────────

class MannWhitneyResult(StatisticalTestResult):
    """Result of Mann-Whitney U test between two groups."""

    method_name: str = "mann_whitney"
    u_statistic: float = 0.0
    significant: bool = False
    n1: int = 0
    n2: int = 0


# ── Method 7: Vargha-Delaney A12 Effect Size ────────────────────────────

class VarghaDelaneyResult(StatisticalTestResult):
    """Result of Vargha-Delaney A12 effect size computation."""

    method_name: str = "vargha_delaney_a12"
    a12: float = 0.5
    magnitude: str = "negligible"
    n1: int = 0
    n2: int = 0


# ── Method 8: Welch's ANOVA ─────────────────────────────────────────────

class WelchAnovaResult(StatisticalTestResult):
    """Result of Welch's ANOVA (one-way, unequal variances)."""

    method_name: str = "welch_anova"
    f_statistic: float = 0.0
    n_groups: int = 0
    group_sizes: List[int] = Field(default_factory=list)
    significant: bool = False


# ── Method 9: Chi-Square / Fisher's Exact Test ──────────────────────────

class ContingencyTestResult(StatisticalTestResult):
    """Result of chi-square or Fisher's exact test on a contingency table."""

    method_name: str = "contingency_test"
    test_used: str = ""
    significant: bool = False
    table: Optional[List[List[int]]] = None


# ── Method 10: Levene's Test + Coefficient of Variation ─────────────────

class LeveneCVResult(StatisticalTestResult):
    """Result of Levene's test for equality of variances plus per-group CV."""

    method_name: str = "levene_cv"
    levene_statistic: float = 0.0
    levene_p: float = 0.0
    variances_equal: bool = True
    cv_per_group: List[float] = Field(default_factory=list)
    cv_labels: List[str] = Field(default_factory=list)


# ── Method 11: Wilcoxon Signed-Rank (One-Sample) ───────────────────────

class WilcoxonSignedRankResult(StatisticalTestResult):
    """Result of one-sample Wilcoxon signed-rank test against a threshold."""

    method_name: str = "wilcoxon_signed_rank"
    threshold: float = 0.0
    median: float = 0.0
    n: int = 0
    meets_threshold: bool = False


# ── Method 12: Exact Binomial Test ──────────────────────────────────────

class ExactBinomialResult(StatisticalTestResult):
    """Result of exact binomial test for breach rate."""

    method_name: str = "exact_binomial"
    breaches: int = 0
    trials: int = 0
    observed_rate: float = 0.0
    target_rate: float = 0.05
    ci_lower: float = 0.0
    ci_upper: float = 0.0
    meets_target: bool = False


# ── Method 13: TOST (Two One-Sided Tests) ───────────────────────────────

class TOSTResult(StatisticalTestResult):
    """Result of TOST equivalence test."""

    method_name: str = "tost"
    mean: float = 0.0
    lower_bound: float = 0.0
    upper_bound: float = 0.0
    p_lower: float = 0.0
    p_upper: float = 0.0
    equivalent: bool = False


# ── Method 14: CVaR (Conditional Value-at-Risk) ────────────────────────

class CVaRResult(StatisticalTestResult):
    """Result of CVaR tail-risk analysis."""

    method_name: str = "cvar"
    quantile_level: float = 0.95
    var: float = 0.0
    cvar: float = 0.0
    n_tail: int = 0
    sla_threshold: Optional[float] = None
    expected_overshoot: Optional[float] = None
    n_breaches: Optional[int] = None


# ── Method 15: Kaplan-Meier Survival Estimator ─────────────────────────

class KaplanMeierResult(StatisticalTestResult):
    """Result of Kaplan-Meier survival analysis."""

    method_name: str = "kaplan_meier"
    survival_at_sla: Optional[float] = None
    sla_threshold: Optional[float] = None
    median_survival: Optional[float] = None
    n_events: int = 0
    n_censored: int = 0
    survival_table: Optional[List[Dict[str, Any]]] = None


# ── Method 16: CUSUM / EWMA Control Charts ─────────────────────────────

class CusumEwmaResult(StatisticalTestResult):
    """Result of CUSUM and EWMA drift detection."""

    method_name: str = "cusum_ewma"
    cusum_final: float = 0.0
    cusum_threshold: float = 0.0
    cusum_alarm: bool = False
    ewma_final: float = 0.0
    ewma_upper_limit: float = 0.0
    ewma_lower_limit: float = 0.0
    ewma_alarm: bool = False
    drift_detected: bool = False
    cusum_values: Optional[List[float]] = None
    ewma_values: Optional[List[float]] = None
