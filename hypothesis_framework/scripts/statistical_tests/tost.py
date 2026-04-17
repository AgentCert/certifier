"""
Method 13 — TOST (Two One-Sided Tests) for Equivalence.

Proves that a metric is demonstrably *within* an equivalence interval [low, high].
Used in H-06 for formal SLA equivalence testing.

For one-sided metrics (e.g., TTD ∈ [0, SLA]), the lower-bound test is trivially
satisfied, reducing TOST to a single one-sided t-test against the upper bound.

Reference:
    Schuirmann, D.J. (1987) J. Pharmacokinetics and Biopharmaceutics, 15(6).
    Lakens, D. (2017) Social Psychological and Personality Science, 8(4).
"""

from __future__ import annotations

import math
from typing import List

import numpy as np
from scipy import stats

from hypothesis_framework.schema.test_results import TOSTResult


def tost_test(
    data: List[float],
    low: float,
    high: float,
    alpha: float = 0.05,
) -> TOSTResult:
    """Two One-Sided Tests for equivalence.

    Tests whether the population mean lies within [low, high].
    - Test 1: H₀: μ ≤ low  vs Hₐ: μ > low
    - Test 2: H₀: μ ≥ high vs Hₐ: μ < high
    If both reject, the metric is equivalent (within bounds).

    Args:
        data: Observed metric values.
        low: Lower equivalence bound.
        high: Upper equivalence bound.
        alpha: Significance level.

    Returns:
        TOSTResult with p-values for both tests, equivalence verdict.
    """
    warnings: List[str] = []
    arr = np.asarray(data, dtype=float)
    n = len(arr)

    if low >= high:
        warnings.append(f"low ({low}) >= high ({high}); bounds invalid.")
        return TOSTResult(
            alpha=alpha, lower_bound=low, upper_bound=high,
            warnings=warnings,
            interpretation="Invalid bounds.",
        )

    if n < 2:
        warnings.append(f"n={n} < 2; t-test requires at least 2 observations.")
        return TOSTResult(
            alpha=alpha, lower_bound=low, upper_bound=high,
            warnings=warnings,
            interpretation="Insufficient data.",
        )

    mean_val = float(np.mean(arr))
    std_val = float(np.std(arr, ddof=1))
    se = std_val / math.sqrt(n)

    if se == 0:
        warnings.append("Zero standard error; all values identical.")
        within = low < mean_val < high
        return TOSTResult(
            alpha=alpha, mean=round(mean_val, 4),
            lower_bound=low, upper_bound=high,
            p_lower=0.0, p_upper=0.0,
            equivalent=within,
            interpretation=(
                f"All values = {mean_val:.2f}; "
                f"{'within' if within else 'outside'} [{low}, {high}]."
            ),
            warnings=warnings,
        )

    # Test 1: H₀: μ ≤ low → one-sided t-test (greater)
    _, p_lower_two = stats.ttest_1samp(arr, low)
    t1 = (mean_val - low) / se
    p_lower = float(1 - stats.t.cdf(t1, df=n - 1))

    # Test 2: H₀: μ ≥ high → one-sided t-test (less)
    _, p_upper_two = stats.ttest_1samp(arr, high)
    t2 = (mean_val - high) / se
    p_upper = float(stats.t.cdf(t2, df=n - 1))

    equivalent = p_lower < alpha and p_upper < alpha
    p_tost = max(p_lower, p_upper)

    return TOSTResult(
        alpha=alpha,
        statistic=round(p_tost, 6),
        p_value=round(p_tost, 6),
        mean=round(mean_val, 4),
        lower_bound=low,
        upper_bound=high,
        p_lower=round(float(p_lower), 6),
        p_upper=round(float(p_upper), 6),
        equivalent=equivalent,
        interpretation=(
            f"mean={mean_val:.2f}, bounds=[{low}, {high}]. "
            f"Test1 p={p_lower:.4f}, Test2 p={p_upper:.4f} → "
            f"{'EQUIVALENT (within bounds)' if equivalent else 'NOT EQUIVALENT'}"
        ),
        warnings=warnings,
    )
