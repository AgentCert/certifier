"""
Method 5 — Kruskal-Wallis H Test.

Rank-based non-parametric ANOVA alternative.  Tests whether at least one
group has a different distribution.  Used in H-03 as the primary comparison
when Shapiro-Wilk rejects normality.

Reference:
    Kruskal & Wallis (1952) JASA, 47(260).
"""

from __future__ import annotations

from typing import List

import numpy as np
from scipy import stats

from hypothesis_framework.schema.test_results import KruskalWallisResult


def kruskal_wallis_test(
    *groups: List[float],
    alpha: float = 0.05,
) -> KruskalWallisResult:
    """Run Kruskal-Wallis H test across multiple groups.

    Args:
        *groups: Two or more arrays of observed values.
        alpha: Significance level.

    Returns:
        KruskalWallisResult with H statistic, p-value, and significance flag.
    """
    warnings: List[str] = []
    arrays = [np.asarray(g, dtype=float) for g in groups]
    group_sizes = [len(a) for a in arrays]

    if len(arrays) < 2:
        warnings.append("Need at least 2 groups for Kruskal-Wallis.")
        return KruskalWallisResult(
            alpha=alpha, n_groups=len(arrays), group_sizes=group_sizes,
            warnings=warnings,
            interpretation="Insufficient groups.",
        )

    for i, a in enumerate(arrays):
        if len(a) < 1:
            warnings.append(f"Group {i} is empty.")

    # Filter out empty groups
    non_empty = [a for a in arrays if len(a) > 0]
    if len(non_empty) < 2:
        warnings.append("Fewer than 2 non-empty groups after filtering.")
        return KruskalWallisResult(
            alpha=alpha, n_groups=len(non_empty), group_sizes=group_sizes,
            warnings=warnings,
            interpretation="Insufficient non-empty groups.",
        )

    h_stat, p_val = stats.kruskal(*non_empty)
    significant = p_val < alpha

    return KruskalWallisResult(
        alpha=alpha,
        statistic=round(float(h_stat), 4),
        p_value=round(float(p_val), 6),
        n_groups=len(non_empty),
        group_sizes=[len(a) for a in non_empty],
        significant=significant,
        interpretation=(
            f"H={h_stat:.2f}, p={p_val:.4f} → "
            f"{'SIGNIFICANT' if significant else 'not significant'} at α={alpha}. "
            f"{'At least one group differs.' if significant else 'No evidence of difference.'}"
        ),
        warnings=warnings,
    )
