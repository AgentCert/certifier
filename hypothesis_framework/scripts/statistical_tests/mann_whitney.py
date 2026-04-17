"""
Method 6 — Mann-Whitney U Test.

Pairwise post-hoc rank comparison between two groups.
Used in H-03 after Kruskal-Wallis to identify *which* pair differs.

Reference:
    Mann & Whitney (1947) Annals of Mathematical Statistics, 18(1).
"""

from __future__ import annotations

from typing import List

import numpy as np
from scipy import stats

from hypothesis_framework.schema.test_results import MannWhitneyResult


def mann_whitney_test(
    group_a: List[float],
    group_b: List[float],
    alpha: float = 0.05,
    alternative: str = "two-sided",
) -> MannWhitneyResult:
    """Run Mann-Whitney U test between two independent groups.

    Args:
        group_a: First group of observations.
        group_b: Second group of observations.
        alpha: Significance level.
        alternative: 'two-sided', 'less', or 'greater'.

    Returns:
        MannWhitneyResult with U statistic, p-value, and significance flag.
    """
    warnings: List[str] = []
    a = np.asarray(group_a, dtype=float)
    b = np.asarray(group_b, dtype=float)

    if len(a) < 1 or len(b) < 1:
        warnings.append("Both groups must have at least 1 observation.")
        return MannWhitneyResult(
            alpha=alpha, n1=len(a), n2=len(b),
            warnings=warnings,
            interpretation="Insufficient data.",
        )

    if len(a) < 5 or len(b) < 5:
        warnings.append("Small sample; p-value may be unreliable.")

    u_stat, p_val = stats.mannwhitneyu(a, b, alternative=alternative)
    significant = p_val < alpha

    return MannWhitneyResult(
        alpha=alpha,
        statistic=round(float(u_stat), 4),
        p_value=round(float(p_val), 6),
        u_statistic=round(float(u_stat), 4),
        n1=len(a),
        n2=len(b),
        significant=significant,
        interpretation=(
            f"U={u_stat:.1f}, p={p_val:.4f} → "
            f"{'SIGNIFICANT' if significant else 'not significant'} at α={alpha}"
        ),
        warnings=warnings,
    )
