"""
H-07: SLA Breach Rate Estimation.

Per-sub-fault breach rate testing with category-level rollup.

Each sub-fault is tested against its own SLA threshold from the ground truth.
Exact binomial test + Wilson CI on breach rate per sub-fault.

Sub-fault verdicts:
  - PASS: binomial p < alpha (breach rate provably below target)
  - INCONCLUSIVE: cannot reject H0 but observed rate below target
  - FAIL: CI lower bound > target (breach rate clearly above target)
  - NO_SLA_DEFINED: no SLA threshold for this sub-fault
  - NO_DATA: no data available

Category verdicts (rollup):
  - PASS: all assessed sub-faults PASS
  - FAIL: any sub-fault FAIL
  - INCOMPLETE: any NO_SLA_DEFINED and none FAIL
  - INCONCLUSIVE: any INCONCLUSIVE and none FAIL/INCOMPLETE
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from hypothesis_framework.schema.hypothesis_results import (
    CategoryBreachResult,
    H07Result,
    SubFaultBreachResult,
)
from hypothesis_framework.scripts.statistical_tests.exact_binomial import exact_binomial_test
from hypothesis_framework.scripts.statistical_tests.wilson_ci import wilson_ci


def run_breach_rate_test(
    data_per_category: Dict[str, Dict[str, List[float]]],
    sla_thresholds: Dict[str, float],
    target_rate: float = 0.05,
    metric_name: str = "time_to_detect",
    alpha: float = 0.05,
) -> H07Result:
    """Run H-07: SLA Breach Rate Estimation.

    Counts SLA breaches per sub-fault and tests whether the true breach
    rate is below the target using exact binomial + Clopper-Pearson CI.

    Non-detected runs should be included as float('inf') so they count
    as breaches against any finite SLA threshold.

    Args:
        data_per_category: {category: {sub_fault: [all_values]}}.
        sla_thresholds: {sub_fault_name: threshold} — per sub-fault SLA.
        target_rate: Max acceptable breach rate (default 5%).
        metric_name: Name of the metric.
        alpha: Significance level.

    Returns:
        H07Result with per-sub-fault breach analysis rolled up to categories.
    """
    warnings: List[str] = []
    per_cat: List[CategoryBreachResult] = []

    for cat, subfaults in data_per_category.items():
        sub_results: List[SubFaultBreachResult] = []
        cat_n = 0

        for fname, values in sorted(subfaults.items()):
            arr = np.asarray(values, dtype=float)
            n = len(arr)
            cat_n += n

            if n == 0:
                warnings.append(f"{cat}/{fname}: no data.")
                sub_results.append(SubFaultBreachResult(
                    fault_name=fname, verdict="NO_DATA",
                ))
                continue

            sla = sla_thresholds.get(fname)
            if sla is None:
                warnings.append(
                    f"{cat}/{fname}: no SLA threshold defined — skipping breach test."
                )
                sub_results.append(SubFaultBreachResult(
                    fault_name=fname, trials=n,
                    verdict="NO_SLA_DEFINED",
                ))
                continue

            breaches = int(np.sum(arr > sla))
            observed_rate = breaches / n

            binom = exact_binomial_test(breaches, n, target_rate=target_rate, alpha=alpha)
            wil = wilson_ci(breaches, n, alpha=alpha)

            if binom.meets_target:
                verdict = "PASS"
            elif wil.lower > target_rate:
                verdict = "FAIL"
            else:
                verdict = "INCONCLUSIVE"

            sub_results.append(SubFaultBreachResult(
                fault_name=fname,
                breaches=breaches,
                trials=n,
                observed_rate=round(observed_rate, 4),
                target_rate=target_rate,
                sla_threshold=sla,
                binomial_p=binom.p_value,
                ci_lower=wil.lower,
                ci_upper=wil.upper,
                verdict=verdict,
            ))

        # Category rollup
        verdicts = [s.verdict for s in sub_results]
        n_passed = sum(v == "PASS" for v in verdicts)
        n_failed = sum(v == "FAIL" for v in verdicts)
        n_inc = sum(v == "INCONCLUSIVE" for v in verdicts)
        n_no_sla = sum(v == "NO_SLA_DEFINED" for v in verdicts)

        if n_failed > 0:
            cat_verdict = "FAIL"
        elif n_no_sla > 0:
            cat_verdict = "INCOMPLETE"
        elif n_inc > 0:
            cat_verdict = "INCONCLUSIVE"
        elif n_passed > 0:
            cat_verdict = "PASS"
        else:
            cat_verdict = "NO_DATA"

        # Worst sub-fault: highest observed breach rate
        assessed = [
            s for s in sub_results
            if s.verdict not in ("NO_DATA", "NO_SLA_DEFINED")
        ]
        worst = ""
        if assessed:
            worst = max(assessed, key=lambda s: s.observed_rate).fault_name

        per_cat.append(CategoryBreachResult(
            category=cat,
            n=cat_n,
            n_sub_faults=len(sub_results),
            n_passed=n_passed,
            n_failed=n_failed,
            n_inconclusive=n_inc,
            n_no_sla=n_no_sla,
            verdict=cat_verdict,
            sub_faults=sub_results,
            worst_sub_fault=worst,
        ))

    cat_verdicts = [c.verdict for c in per_cat]
    if all(v == "PASS" for v in cat_verdicts):
        overall = "breach_rate_certified"
    elif any(v == "FAIL" for v in cat_verdicts):
        overall = "breach_rate_exceeds_target"
    elif any(v == "INCOMPLETE" for v in cat_verdicts):
        overall = "incomplete_coverage"
    else:
        overall = "inconclusive"

    return H07Result(
        metric_name=metric_name,
        alpha=alpha,
        sla_thresholds=sla_thresholds,
        target_rate=target_rate,
        per_category=per_cat,
        overall_assessment=overall,
        warnings=warnings,
    )
