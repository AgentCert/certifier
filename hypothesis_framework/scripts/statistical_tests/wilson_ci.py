"""
Method 1 — Wilson Confidence Interval.

Computes the Wilson score interval for a binomial proportion (success/failure).
Used in H-01 and H-02 to bound detection rates and success rates.

Uses statsmodels.stats.proportion.proportion_confint for the core computation.

Reference:
    Wilson, E.B. (1927) "Probable inference." JASA, 22(158), 209-212.
"""

from __future__ import annotations

from typing import List

from statsmodels.stats.proportion import proportion_confint

from hypothesis_framework.schema.test_results import WilsonCIResult


def wilson_ci(
    successes: int,
    trials: int,
    alpha: float = 0.05,
) -> WilsonCIResult:
    """Compute Wilson score confidence interval for a binomial proportion.

    Args:
        successes: Number of successes (e.g., faults detected).
        trials: Total number of trials.
        alpha: Significance level (default 0.05 for 95% CI).

    Returns:
        WilsonCIResult with proportion, lower bound, upper bound, and CI.
    """
    warnings: List[str] = []

    if trials <= 0:
        warnings.append("trials must be > 0; returning degenerate interval.")
        return WilsonCIResult(
            alpha=alpha, warnings=warnings,
            interpretation="Invalid input: zero trials.",
        )

    if successes < 0 or successes > trials:
        warnings.append(f"successes ({successes}) outside [0, {trials}]; clamping.")
        successes = max(0, min(successes, trials))

    if trials < 10:
        warnings.append(f"Small sample (n={trials}); CI may be unreliable.")

    p_hat = successes / trials

    lower, upper = proportion_confint(
        successes, trials, alpha=alpha, method="wilson"
    )
    lower = max(0.0, float(lower))
    upper = min(1.0, float(upper))

    return WilsonCIResult(
        alpha=alpha,
        successes=successes,
        trials=trials,
        proportion=round(p_hat, 6),
        lower=round(lower, 6),
        upper=round(upper, 6),
        confidence_interval=(round(lower, 6), round(upper, 6)),
        interpretation=(
            f"{successes}/{trials} = {p_hat:.1%}; "
            f"{(1-alpha)*100:.0f}% Wilson CI: [{lower:.3f}, {upper:.3f}]"
        ),
        warnings=warnings,
    )
