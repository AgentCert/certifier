"""
Method 12 — Exact Binomial Test.

Tests whether the observed SLA breach rate is below a target rate.
Uses exact combinatorial probability with Clopper-Pearson CI.

Reference:
    Clopper, C.J. & Pearson, E.S. (1934) Biometrika, 26(4).
"""

from __future__ import annotations

from typing import List

from scipy import stats

from hypothesis_framework.schema.test_results import ExactBinomialResult


def exact_binomial_test(
    breaches: int,
    trials: int,
    target_rate: float = 0.05,
    alpha: float = 0.05,
) -> ExactBinomialResult:
    """Exact binomial test for SLA breach rate.

    Tests H₀: breach_rate ≥ target_rate vs Hₐ: breach_rate < target_rate.

    Args:
        breaches: Number of SLA breaches observed.
        trials: Total number of trials.
        target_rate: Maximum acceptable breach rate (default 5%).
        alpha: Significance level.

    Returns:
        ExactBinomialResult with observed rate, Clopper-Pearson CI, and verdict.
    """
    warnings: List[str] = []

    if trials <= 0:
        warnings.append("trials must be > 0.")
        return ExactBinomialResult(
            alpha=alpha, target_rate=target_rate,
            warnings=warnings,
            interpretation="Invalid input: zero trials.",
        )

    if breaches < 0 or breaches > trials:
        warnings.append(f"breaches ({breaches}) outside [0, {trials}]; clamping.")
        breaches = max(0, min(breaches, trials))

    observed_rate = breaches / trials

    # Exact binomial test (one-sided: is breach rate < target?)
    result = stats.binomtest(breaches, trials, p=target_rate, alternative="less")
    p_val = result.pvalue

    # Clopper-Pearson exact CI
    ci = result.proportion_ci(confidence_level=1 - alpha, method="exact")
    ci_lower = float(ci.low)
    ci_upper = float(ci.high)

    meets_target = p_val < alpha

    return ExactBinomialResult(
        alpha=alpha,
        p_value=round(float(p_val), 6),
        breaches=breaches,
        trials=trials,
        observed_rate=round(observed_rate, 6),
        target_rate=target_rate,
        ci_lower=round(ci_lower, 6),
        ci_upper=round(ci_upper, 6),
        confidence_interval=(round(ci_lower, 6), round(ci_upper, 6)),
        meets_target=meets_target,
        interpretation=(
            f"{breaches}/{trials} = {observed_rate:.1%} breaches "
            f"(target < {target_rate:.0%}). "
            f"Clopper-Pearson CI: [{ci_lower:.3f}, {ci_upper:.3f}]. "
            f"p={p_val:.3f} → {'PASS' if meets_target else 'FAIL or INCONCLUSIVE'}"
        ),
        warnings=warnings,
    )
