"""
H-08: Tail Risk Analysis.

CVaR (Conditional Value-at-Risk) per fault category.
With SLA: expected overshoot and max violation.
Classifies risk: mild (CVaR/IQM < 1.5), moderate (< 2.0), significant (>= 2.0).
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from hypothesis_framework.schema.hypothesis_results import (
    CategoryTailResult,
    H08Result,
)
from hypothesis_framework.scripts.statistical_tests.cvar import cvar_analysis
from hypothesis_framework.scripts.statistical_tests.iqm import interquartile_mean


def run_tail_risk_test(
    data_per_category: Dict[str, List[float]],
    metric_name: str = "time_to_detect",
    quantile: float = 0.95,
    sla_threshold: Optional[float] = None,
) -> H08Result:
    """Run H-08: Tail Risk Analysis.

    CVaR analysis per category, with optional SLA overshoot computation.

    Args:
        data_per_category: {category: [values]} (detected-only).
        metric_name: Name of the metric.
        quantile: Quantile for VaR/CVaR (default 0.95).
        sla_threshold: Optional SLA threshold for overshoot analysis.

    Returns:
        H08Result with per-category tail risk assessments.
    """
    warnings: List[str] = []
    per_cat: List[CategoryTailResult] = []

    for cat, values in data_per_category.items():
        arr = np.asarray(values, dtype=float)
        n = len(arr)
        if n == 0:
            warnings.append(f"{cat}: no data.")
            continue

        if n < 20:
            warnings.append(f"{cat}: n={n}; tail risk estimates uncertain at small n.")

        cvar_r = cvar_analysis(values, quantile=quantile, sla_threshold=sla_threshold)
        iqm_r = interquartile_mean(values)

        cvar_iqm_ratio = None
        if iqm_r.iqm > 0:
            cvar_iqm_ratio = round(cvar_r.cvar / iqm_r.iqm, 2)

        if cvar_iqm_ratio is not None:
            if cvar_iqm_ratio < 1.5:
                risk_level = "mild"
            elif cvar_iqm_ratio < 2.0:
                risk_level = "moderate"
            else:
                risk_level = "significant"
        else:
            risk_level = "unknown"

        per_cat.append(CategoryTailResult(
            category=cat,
            p95=round(float(np.percentile(arr, 95)), 2),
            cvar=cvar_r.cvar,
            n_tail=cvar_r.n_tail,
            cvar_iqm_ratio=cvar_iqm_ratio,
            expected_overshoot=cvar_r.expected_overshoot,
            n_breaches=cvar_r.n_breaches,
            risk_level=risk_level,
        ))

    sig_cats = [c.category for c in per_cat if c.risk_level == "significant"]
    overall = "significant_tail_risk" if sig_cats else "acceptable_tail_risk"

    return H08Result(
        metric_name=metric_name,
        quantile_level=quantile,
        sla_threshold=sla_threshold,
        per_category=per_cat,
        overall_assessment=overall,
        warnings=warnings,
    )
