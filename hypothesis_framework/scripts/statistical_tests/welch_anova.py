"""
Method 8 — Welch's ANOVA (One-Way).

Parametric one-way ANOVA that does NOT assume equal variances across groups.
Only used when all groups pass Shapiro-Wilk normality test.
More powerful than Kruskal-Wallis when normality holds.

Reference:
    Welch, B.L. (1951) Biometrika, 38.
"""

from __future__ import annotations

from typing import List

import numpy as np
from statsmodels.stats.oneway import anova_oneway

from hypothesis_framework.schema.test_results import WelchAnovaResult


def welch_anova(
    *groups: List[float],
    alpha: float = 0.05,
) -> WelchAnovaResult:
    """Run Welch's ANOVA (one-way, unequal variances).

    Uses statsmodels.stats.oneway.anova_oneway with use_var='unequal', which
    correctly implements Welch's F-test without assuming equal group variances.
    Only used when all groups pass Shapiro-Wilk normality test.

    Args:
        *groups: Two or more arrays of observed values.
        alpha: Significance level.

    Returns:
        WelchAnovaResult with F statistic, p-value, and significance flag.
    """
    warnings: List[str] = []
    arrays = [np.asarray(g, dtype=float) for g in groups]
    group_sizes = [len(a) for a in arrays]

    if len(arrays) < 2:
        warnings.append("Need at least 2 groups.")
        return WelchAnovaResult(
            alpha=alpha, n_groups=len(arrays), group_sizes=group_sizes,
            warnings=warnings,
            interpretation="Insufficient groups.",
        )

    for i, a in enumerate(arrays):
        if len(a) < 2:
            warnings.append(f"Group {i} has n={len(a)} < 2; variance undefined.")

    non_empty = [a for a in arrays if len(a) >= 2]
    if len(non_empty) < 2:
        warnings.append("Fewer than 2 groups with n >= 2.")
        return WelchAnovaResult(
            alpha=alpha, n_groups=len(non_empty), group_sizes=group_sizes,
            warnings=warnings,
            interpretation="Insufficient valid groups.",
        )

    # Welch's ANOVA via statsmodels with use_var='unequal' — does not assume equal variances
    all_data = np.concatenate(non_empty)
    group_labels = np.concatenate(
        [np.full(len(g), i) for i, g in enumerate(non_empty)]
    )
    ow = anova_oneway(all_data, groups=group_labels, use_var="unequal")
    f_stat_val = float(ow.statistic)
    p_val = float(ow.pvalue)
    f_stat = f_stat_val
    significant = p_val < alpha

    k = len(non_empty)
    ns = [len(g) for g in non_empty]

    return WelchAnovaResult(
        alpha=alpha,
        statistic=round(float(f_stat), 4),
        p_value=round(float(p_val), 6),
        f_statistic=round(float(f_stat), 4),
        n_groups=k,
        group_sizes=[int(n) for n in ns],
        significant=significant,
        interpretation=(
            f"Welch's F={f_stat:.2f}, p={p_val:.4f} → "
            f"{'SIGNIFICANT' if significant else 'not significant'} at α={alpha}"
        ),
        warnings=warnings,
    )
