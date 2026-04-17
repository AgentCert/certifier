"""
Method 4 — Shapiro-Wilk Normality Test.

Pre-test gatekeeper: decides whether data follows a normal distribution.
If normal → Welch's ANOVA is valid.  If not → use Kruskal-Wallis instead.

Reference:
    Shapiro & Wilk (1965) Biometrika, 52(3-4).
"""

from __future__ import annotations

from typing import List

from scipy import stats

from hypothesis_framework.schema.test_results import ShapiroWilkResult


def shapiro_wilk_test(
    data: List[float],
    alpha: float = 0.05,
) -> ShapiroWilkResult:
    """Run Shapiro-Wilk normality test.

    Args:
        data: Observed sample values.
        alpha: Significance level.

    Returns:
        ShapiroWilkResult indicating whether data is normally distributed.
    """
    import numpy as np

    warnings: List[str] = []
    arr = np.asarray(data, dtype=float)
    n = len(arr)

    if n < 3:
        warnings.append(f"n={n} < 3; Shapiro-Wilk requires at least 3 observations.")
        return ShapiroWilkResult(
            alpha=alpha, n=n, is_normal=False,
            warnings=warnings,
            interpretation="Insufficient data for normality test.",
        )

    if n > 5000:
        warnings.append(f"n={n} > 5000; Shapiro-Wilk may be overly sensitive.")

    w_stat, p_val = stats.shapiro(arr)
    is_normal = p_val >= alpha

    return ShapiroWilkResult(
        alpha=alpha,
        statistic=round(float(w_stat), 6),
        p_value=round(float(p_val), 6),
        is_normal=is_normal,
        n=n,
        interpretation=(
            f"W={w_stat:.4f}, p={p_val:.4f} → "
            f"{'NORMAL' if is_normal else 'NOT NORMAL'} at α={alpha}"
        ),
        warnings=warnings,
    )
