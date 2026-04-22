"""
H-08: Tail Risk Analysis.

Per-sub-fault CVaR (Conditional Value-at-Risk) analysis with category rollup.

Always active — does not require SLA thresholds. When SLA thresholds are
provided, computes expected overshoot per sub-fault.

Risk levels per sub-fault (CVaR/IQM ratio):
  - mild: ratio < 1.5
  - moderate: 1.5 ≤ ratio < 2.0
  - significant: ratio ≥ 2.0

Category risk is the worst sub-fault risk level.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from hypothesis_framework.schema.hypothesis_results import (
    CategoryTailResult,
    H08Result,
    SubFaultTailResult,
)
from hypothesis_framework.scripts.statistical_tests.cvar import cvar_analysis
from hypothesis_framework.scripts.statistical_tests.iqm import interquartile_mean

_RISK_ORDER = {"mild": 0, "moderate": 1, "significant": 2, "unknown": -1}


def run_tail_risk_test(
    data_per_category: Dict[str, Dict[str, List[float]]],
    metric_name: str = "time_to_detect",
    quantile: float = 0.95,
    sla_thresholds: Optional[Dict[str, float]] = None,
) -> H08Result:
    """Run H-08: Tail Risk Analysis.

    CVaR analysis per sub-fault, with optional per-sub-fault SLA overshoot.

    Args:
        data_per_category: {category: {sub_fault: [values]}} (detected-only).
        metric_name: Name of the metric.
        quantile: Quantile for VaR/CVaR (default 0.95).
        sla_thresholds: Optional {sub_fault: threshold} for overshoot analysis.

    Returns:
        H08Result with per-sub-fault tail risk rolled up to categories.
    """
    warnings: List[str] = []
    per_cat: List[CategoryTailResult] = []
    sla_map = sla_thresholds or {}

    for cat, subfaults in data_per_category.items():
        sub_results: List[SubFaultTailResult] = []
        cat_n = 0

        for fname, values in sorted(subfaults.items()):
            arr = np.asarray(values, dtype=float)
            n = len(arr)
            cat_n += n

            if n == 0:
                warnings.append(f"{cat}/{fname}: no data.")
                sub_results.append(SubFaultTailResult(
                    fault_name=fname, risk_level="unknown",
                ))
                continue

            if n < 20:
                warnings.append(
                    f"{cat}/{fname}: n={n}; tail risk estimates uncertain at small n."
                )

            sf_sla = sla_map.get(fname)
            cvar_r = cvar_analysis(values, quantile=quantile, sla_threshold=sf_sla)
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

            sub_results.append(SubFaultTailResult(
                fault_name=fname,
                n=n,
                p95=round(float(np.percentile(arr, 95)), 2),
                cvar=cvar_r.cvar,
                n_tail=cvar_r.n_tail,
                cvar_iqm_ratio=cvar_iqm_ratio,
                sla_threshold=sf_sla,
                expected_overshoot=cvar_r.expected_overshoot,
                n_breaches=cvar_r.n_breaches,
                risk_level=risk_level,
            ))

        # Category rollup: worst risk level
        risk_levels = [s.risk_level for s in sub_results if s.risk_level != "unknown"]
        if risk_levels:
            cat_risk = max(risk_levels, key=lambda r: _RISK_ORDER.get(r, -1))
        else:
            cat_risk = "unknown"

        worst = ""
        assessed = [s for s in sub_results if s.risk_level != "unknown"]
        if assessed:
            worst = max(
                assessed,
                key=lambda s: s.cvar_iqm_ratio if s.cvar_iqm_ratio is not None else 0.0,
            ).fault_name

        per_cat.append(CategoryTailResult(
            category=cat,
            n=cat_n,
            n_sub_faults=len(sub_results),
            risk_level=cat_risk,
            worst_sub_fault=worst,
            sub_faults=sub_results,
        ))

    sig_cats = [c.category for c in per_cat if c.risk_level == "significant"]
    overall = "significant_tail_risk" if sig_cats else "acceptable_tail_risk"

    return H08Result(
        metric_name=metric_name,
        quantile_level=quantile,
        sla_thresholds=sla_map,
        per_category=per_cat,
        overall_assessment=overall,
        warnings=warnings,
    )
