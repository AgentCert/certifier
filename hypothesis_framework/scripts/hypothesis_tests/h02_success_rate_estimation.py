"""
H-02: Success Rate Estimation with Safety Floor.

Wilson CI for binary success/failure rates. Computes per sub-fault, then
equal-weight averages sub-fault rates for the category-level estimate.
The lower bound of the Wilson CI is the "certified floor."

Derived metrics: fault_detection_success_rate, fault_mitigation_success_rate,
    rai_compliance_rate, security_compliance_rate
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from hypothesis_framework.schema.hypothesis_results import (
    CategoryRateResult,
    H02Result,
    SubFaultRateResult,
)
from hypothesis_framework.scripts.statistical_tests.wilson_ci import wilson_ci


def run_success_rate_test(
    counts_per_category: Dict[str, Dict[str, Tuple[int, int]]],
    metric_name: str = "fault_detection_success_rate",
    alpha: float = 0.05,
) -> H02Result:
    """Run H-02: Success Rate Estimation with Safety Floor.

    For each category, computes Wilson CI per sub-fault, then equal-weight
    averages sub-fault rates for the category estimate. The certified floor
    is the Wilson lower bound of the category-level rate.

    Args:
        counts_per_category: {category: {sub_fault: (successes, trials)}}.
        metric_name: Name of the rate metric.
        alpha: Significance level.

    Returns:
        H02Result with per-category Wilson CI, sub-fault breakdown, and certified floor.
    """
    warnings: List[str] = []
    per_cat: List[CategoryRateResult] = []

    for cat, subfaults in counts_per_category.items():
        sub_results: List[SubFaultRateResult] = []
        total_successes = 0
        total_trials = 0

        for fname, (successes, trials) in subfaults.items():
            r = wilson_ci(successes, trials, alpha=alpha)
            warnings.extend(r.warnings)
            total_successes += successes
            total_trials += trials

            sub_results.append(SubFaultRateResult(
                fault_name=fname,
                successes=successes,
                trials=trials,
                rate=r.proportion,
                wilson_lower=r.lower,
                wilson_upper=r.upper,
            ))

        # Category rate = equal-weight average of sub-fault rates
        sf_rates = [sf.rate for sf in sub_results]
        cat_rate = sum(sf_rates) / len(sf_rates) if sf_rates else 0.0

        # Category Wilson CI on pooled counts (conservative)
        cat_wilson = wilson_ci(total_successes, total_trials, alpha=alpha)

        # Certified floor = Wilson lower bound on pooled category rate
        certified_floor = cat_wilson.lower

        # Worst sub-fault = lowest rate
        worst = min(sub_results, key=lambda s: s.rate) if sub_results else None

        per_cat.append(CategoryRateResult(
            category=cat,
            successes=total_successes,
            trials=total_trials,
            rate=round(cat_rate, 6),
            wilson_lower=cat_wilson.lower,
            wilson_upper=cat_wilson.upper,
            certified_floor=certified_floor,
            n_sub_faults=len(sub_results),
            sub_faults=sub_results,
            worst_sub_fault=worst.fault_name if worst else "",
        ))

    floors = [c.certified_floor for c in per_cat]
    worst_floor = min(floors) if floors else 0.0
    overall = f"worst_certified_floor={worst_floor:.1%}"

    return H02Result(
        metric_name=metric_name,
        alpha=alpha,
        per_category=per_cat,
        overall_assessment=overall,
        warnings=warnings,
    )
