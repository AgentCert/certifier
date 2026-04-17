"""
Method 11 — Wilcoxon Signed-Rank Test (One-Sample).

Non-parametric one-sample test: does the median lie below an SLA threshold?
Used in H-06 for SLA compliance testing.

Note: This is a ONE-SAMPLE signed-rank test against a fixed threshold,
NOT the two-sample paired variant.

Reference:
    Wilcoxon, F. (1945) "Individual comparisons by ranking methods."
    Biometrics Bulletin, 1(6), 80-83.
"""

from __future__ import annotations

from typing import List

import numpy as np
from scipy import stats

from hypothesis_framework.schema.test_results import WilcoxonSignedRankResult


def wilcoxon_signed_rank(
    data: List[float],
    threshold: float,
    alpha: float = 0.05,
) -> WilcoxonSignedRankResult:
    """One-sample Wilcoxon signed-rank test against an SLA threshold.

    Tests H₀: median(data) ≥ threshold vs Hₐ: median(data) < threshold.
    Rejects when observations are systematically below the threshold.

    Args:
        data: Observed metric values (e.g., time-to-detect).
        threshold: SLA threshold to test against.
        alpha: Significance level.

    Returns:
        WilcoxonSignedRankResult with test statistic, p-value, and verdict.
    """
    warnings: List[str] = []
    arr = np.asarray(data, dtype=float)
    n = len(arr)

    if n < 6:
        warnings.append(f"n={n} < 6; Wilcoxon signed-rank unreliable for very small samples.")

    if n == 0:
        return WilcoxonSignedRankResult(
            alpha=alpha, threshold=threshold, n=0,
            warnings=["Empty data."],
            interpretation="No data provided.",
        )

    differences = arr - threshold

    # Remove zero differences (ties with threshold)
    non_zero = differences[differences != 0]
    n_zeros = n - len(non_zero)
    if n_zeros > 0:
        warnings.append(f"{n_zeros} observation(s) exactly equal threshold; dropped from test.")

    if len(non_zero) < 1:
        warnings.append("All observations equal threshold; test undefined.")
        return WilcoxonSignedRankResult(
            alpha=alpha, threshold=threshold,
            median=float(np.median(arr)), n=n,
            warnings=warnings,
            interpretation="All observations at threshold; cannot determine compliance.",
        )

    w_stat, p_val = stats.wilcoxon(non_zero, alternative="less")
    meets = p_val < alpha
    median_val = float(np.median(arr))

    return WilcoxonSignedRankResult(
        alpha=alpha,
        statistic=round(float(w_stat), 4),
        p_value=round(float(p_val), 6),
        threshold=threshold,
        median=round(median_val, 4),
        n=n,
        meets_threshold=meets,
        interpretation=(
            f"W={w_stat:.1f}, p={p_val:.4f}, median={median_val:.1f} vs threshold={threshold}. "
            f"→ {'PASS (below SLA)' if meets else 'FAIL or INCONCLUSIVE'}"
        ),
        warnings=warnings,
    )
