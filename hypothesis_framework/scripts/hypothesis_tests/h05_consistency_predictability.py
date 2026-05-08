"""
H-05: Consistency & Predictability (Variance Stability).

Levene's test for equality of variances across categories,
plus per-category CV with threshold classification:
  CV < 0.15 → stable
  0.15 ≤ CV < 0.30 → moderate
  CV ≥ 0.30 → unstable (red flag)

Aggregation: POOLED per-run values within each category.
Sub-fault CV breakdown reported for transparency.

Configuration Note: Metric selection is configurable per use case.
Optional normalization may be applied to align scales across metrics.
"""

from __future__ import annotations

from typing import Dict, List

import numpy as np

from hypothesis_framework.schema.hypothesis_results import (
    CategoryConsistencyDetail,
    H05Result,
    SubFaultConsistencyDetail,
)
from hypothesis_framework.scripts.statistical_tests.levene_cv import levene_cv_test


def _classify_cv(cv: float) -> str:
    if cv < 0.15:
        return "stable"
    elif cv < 0.30:
        return "moderate"
    else:
        return "unstable"


def run_consistency_test(
    data_per_category: Dict[str, Dict[str, List[float]]],
    metric_name: str = "time_to_detect",
    alpha: float = 0.05,
) -> H05Result:
    """Run H-05: Consistency & Predictability.

    Levene's test across pooled category groups + per-category and
    per-sub-fault CV analysis.

    Args:
        data_per_category: {category: {sub_fault: [values]}}.
            Data should be detected-only values.
        metric_name: Name of the metric.
        alpha: Significance level.

    Returns:
        H05Result with Levene's test, CVs, stability flags, and sub-fault breakdown.
    """
    warnings: List[str] = []
    categories = list(data_per_category.keys())
    cat_details: List[CategoryConsistencyDetail] = []
    pooled_groups: List[List[float]] = []
    cv_map: Dict[str, float] = {}
    cv_flags: Dict[str, str] = {}
    unstable: List[str] = []

    for cat in categories:
        subfaults = data_per_category[cat]
        sub_results: List[SubFaultConsistencyDetail] = []
        all_values: List[float] = []

        for fname, values in subfaults.items():
            arr = np.asarray(values, dtype=float)
            all_values.extend(values)

            sf_mean = float(np.mean(arr)) if len(arr) > 0 else 0.0
            sf_std = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
            sf_cv = round(sf_std / abs(sf_mean), 4) if sf_mean != 0 else 0.0

            sub_results.append(SubFaultConsistencyDetail(
                fault_name=fname,
                n=len(arr),
                mean=round(sf_mean, 2),
                std=round(sf_std, 2),
                cv=sf_cv,
                cv_flag=_classify_cv(sf_cv),
            ))

        pooled = np.asarray(all_values, dtype=float)
        pooled_groups.append(all_values)
        n_total = len(pooled)

        p_mean = float(np.mean(pooled)) if n_total > 0 else 0.0
        p_std = float(np.std(pooled, ddof=1)) if n_total > 1 else 0.0
        p_cv = round(p_std / abs(p_mean), 4) if p_mean != 0 else 0.0

        flag = _classify_cv(p_cv)
        cv_map[cat] = p_cv
        cv_flags[cat] = flag
        if flag == "unstable":
            unstable.append(cat)

        # Sub-fault CV range
        sf_cvs = [sf.cv for sf in sub_results]
        cv_range = f"{min(sf_cvs):.2f}–{max(sf_cvs):.2f}" if sf_cvs else ""

        cat_details.append(CategoryConsistencyDetail(
            category=cat,
            n=n_total,
            pooled_mean=round(p_mean, 2),
            pooled_std=round(p_std, 2),
            pooled_cv=p_cv,
            cv_flag=flag,
            n_sub_faults=len(sub_results),
            within_cv_range=cv_range,
            sub_faults=sub_results,
        ))

    # Levene's test across pooled category groups
    r = levene_cv_test(*pooled_groups, labels=categories, alpha=alpha)
    warnings.extend(r.warnings)

    assessment = "variance_instability_detected" if unstable else (
        "unequal_variance" if not r.variances_equal else "consistent"
    )

    return H05Result(
        metric_name=metric_name,
        alpha=alpha,
        levene_statistic=r.levene_statistic,
        levene_p=r.levene_p,
        variances_equal=r.variances_equal,
        per_category=cat_details,
        cv_per_category=cv_map,
        cv_flags=cv_flags,
        unstable_categories=unstable,
        overall_assessment=assessment,
        warnings=warnings,
    )
