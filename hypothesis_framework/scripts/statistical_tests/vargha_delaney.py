"""
Method 7 — Vargha-Delaney A12 Effect Size.

Probability that a random draw from group A is larger than from group B.
A12=0.50 means identical distributions; thresholds: 0.56 small, 0.64 medium, 0.71+ large.

Reference:
    Vargha & Delaney (2000) JBES, 25(2).
    Arcuri & Briand (2011) ICSE.
"""

from __future__ import annotations

from typing import List

import numpy as np

from hypothesis_framework.schema.test_results import VarghaDelaneyResult


def vargha_delaney_a12(
    group_a: List[float],
    group_b: List[float],
) -> VarghaDelaneyResult:
    """Compute Vargha-Delaney A12 effect size.

    A12 = P(X_a > X_b) + 0.5 * P(X_a == X_b).

    Args:
        group_a: First group of observations.
        group_b: Second group of observations.

    Returns:
        VarghaDelaneyResult with A12 value and magnitude label.
    """
    warnings: List[str] = []
    a = np.asarray(group_a, dtype=float)
    b = np.asarray(group_b, dtype=float)
    n1, n2 = len(a), len(b)

    if n1 == 0 or n2 == 0:
        warnings.append("Both groups must be non-empty.")
        return VarghaDelaneyResult(
            n1=n1, n2=n2, warnings=warnings,
            interpretation="Insufficient data.",
        )

    # Compute A12 from Mann-Whitney U statistic
    from scipy.stats import mannwhitneyu
    u_stat, _ = mannwhitneyu(a, b, alternative="two-sided")
    a12 = float(u_stat) / (n1 * n2)

    # Classify magnitude
    diff = abs(a12 - 0.5)
    if diff < 0.06:
        magnitude = "negligible"
    elif diff < 0.14:
        magnitude = "small"
    elif diff < 0.21:
        magnitude = "medium"
    else:
        magnitude = "large"

    return VarghaDelaneyResult(
        a12=round(a12, 4),
        magnitude=magnitude,
        n1=n1,
        n2=n2,
        statistic=round(a12, 4),
        interpretation=(
            f"A12={a12:.3f} ({magnitude.upper()}). "
            f"Group A > Group B with probability {a12:.1%}."
        ),
        warnings=warnings,
    )
