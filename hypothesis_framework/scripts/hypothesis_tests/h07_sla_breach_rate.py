"""
H-07: SLA Breach Rate Estimation.

Exact binomial test + Wilson CI on breach rate per fault category.
Three verdicts: PASS, FAIL, INCONCLUSIVE.
"""

from __future__ import annotations

from typing import Dict, List

import numpy as np

from hypothesis_framework.schema.hypothesis_results import (
    CategoryBreachResult,
    H07Result,
)
from hypothesis_framework.scripts.statistical_tests.exact_binomial import exact_binomial_test
from hypothesis_framework.scripts.statistical_tests.wilson_ci import wilson_ci


def run_breach_rate_test(
    data_per_category: Dict[str, List[float]],
    sla_threshold: float,
    target_rate: float = 0.05,
    metric_name: str = "time_to_detect",
    alpha: float = 0.05,
) -> H07Result:
    """Run H-07: SLA Breach Rate Estimation.

    Counts SLA breaches per category and tests whether the true breach rate
    is below the target using exact binomial + Clopper-Pearson CI.

    Verdicts:
        PASS: binomial p < alpha (breach rate provably below target).
        INCONCLUSIVE: cannot reject H0 but observed rate below target.
        FAIL: CI lower bound > target (breach rate clearly above target).

    Args:
        data_per_category: {category: [all_values]} including timeouts.
        sla_threshold: Metric threshold defining a breach.
        target_rate: Max acceptable breach rate (default 5%).
        metric_name: Name of the metric.
        alpha: Significance level.

    Returns:
        H07Result with per-category breach analysis.
    """
    warnings: List[str] = []
    per_cat: List[CategoryBreachResult] = []

    for cat, values in data_per_category.items():
        arr = np.asarray(values, dtype=float)
        n = len(arr)
        if n == 0:
            warnings.append(f"{cat}: no data.")
            continue

        breaches = int(np.sum(arr > sla_threshold))
        observed_rate = breaches / n

        binom = exact_binomial_test(breaches, n, target_rate=target_rate, alpha=alpha)

        # Wilson CI on breach rate
        wil = wilson_ci(breaches, n, alpha=alpha)

        # Verdict logic
        if binom.meets_target:
            verdict = "PASS"
        elif wil.lower > target_rate:
            verdict = "FAIL"
        else:
            verdict = "INCONCLUSIVE"

        per_cat.append(CategoryBreachResult(
            category=cat,
            breaches=breaches,
            trials=n,
            observed_rate=round(observed_rate, 4),
            target_rate=target_rate,
            binomial_p=binom.p_value or 1.0,
            ci_lower=wil.lower,
            ci_upper=wil.upper,
            verdict=verdict,
        ))

    verdicts = [c.verdict for c in per_cat]
    if all(v == "PASS" for v in verdicts):
        overall = "breach_rate_certified"
    elif any(v == "FAIL" for v in verdicts):
        overall = "breach_rate_exceeds_target"
    else:
        overall = "inconclusive"

    return H07Result(
        metric_name=metric_name,
        alpha=alpha,
        sla_threshold=sla_threshold,
        target_rate=target_rate,
        per_category=per_cat,
        overall_assessment=overall,
        warnings=warnings,
    )
