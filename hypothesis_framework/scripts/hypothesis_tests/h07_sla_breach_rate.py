"""
H-07: SLA Breach Rate Estimation.

Hybrid approach:
  - Per-sub-fault: test each fault against its own SLA threshold using exact binomial + Wilson CI
  - Per-category: pool breach counts across sub-faults and re-test at category level

Hypotheses:
  - H₀: The true breach rate IS below the target (meets SLA)
  - Hₐ: The true breach rate is AT/ABOVE the target (doesn't meet SLA)

Binomial interpretation (alternative="greater"):
  - p ≥ α: Fail to reject H₀ → breach rate likely < target (meets SLA) → meets_target = True
  - p < α: Reject H₀ → breach rate likely ≥ target (doesn't meet) → meets_target = False

Sub-fault verdicts (consensus-based):
  - PASS: binomial p ≥ α AND (CI clearly below target OR observed rate below target)
  - FAIL: CI lower bound > target AND binomial doesn't pass
  - INCONCLUSIVE: binomial passes but CI not clearly below target
  - NO_SLA_DEFINED: no SLA threshold for this sub-fault
  - NO_DATA: no data available

Category verdicts (aggregated from pooled breach counts):
  - Same verdict logic applied to category-level pooled data
  - Per-category metrics: total breaches, trials, observed rate, CI, binomial p
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
    min_sample_size: int = 5,
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
        min_sample_size: Minimum sample size for valid testing (default 5).

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

            if n < min_sample_size:
                warnings.append(f"{cat}/{fname}: insufficient sample size (n={n}, min={min_sample_size}).")
                sub_results.append(SubFaultBreachResult(
                    fault_name=fname, trials=n,
                    verdict="INSUFFICIENT_DATA",
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

            # Consensus-based verdict: collect all evidence first
            binomial_pass = binom.meets_target  # p >= alpha?
            ci_clearly_below = wil.upper <= target_rate  # Entire CI below target?
            ci_clearly_above = wil.lower > target_rate   # Entire CI above target?
            observed_below = observed_rate < target_rate # Raw rate below target?

            # Now decide consensus
            if binomial_pass and (ci_clearly_below or observed_below):
                verdict = "PASS"
            elif ci_clearly_above and not binomial_pass:
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

        # Category aggregation: pool breach counts across all sub-faults with assessed verdicts
        assessed_sub_faults = [
            s for s in sub_results
            if s.verdict not in ("NO_DATA", "NO_SLA_DEFINED", "INSUFFICIENT_DATA")
        ]
        
        # Get sub-fault verdict counts for reporting
        verdicts = [s.verdict for s in sub_results]
        n_passed = sum(v == "PASS" for v in verdicts)
        n_failed = sum(v == "FAIL" for v in verdicts)
        n_inc = sum(v == "INCONCLUSIVE" for v in verdicts)
        n_no_sla = sum(v == "NO_SLA_DEFINED" for v in verdicts)
        n_insufficient = sum(v == "INSUFFICIENT_DATA" for v in verdicts)

        # Worst sub-fault: highest observed breach rate
        worst = ""
        if assessed_sub_faults:
            worst = max(assessed_sub_faults, key=lambda s: s.observed_rate).fault_name
        
        if len(assessed_sub_faults) == 0:
            # No assessed data at category level
            cat_verdict = "NO_DATA"
            cat_result = CategoryBreachResult(
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
                n_insufficient_data=n_insufficient,
            )
        else:
            # Pool breach counts from all assessed sub-faults
            cat_breaches = sum(s.breaches for s in assessed_sub_faults)
            cat_trials = sum(s.trials for s in assessed_sub_faults)
            cat_observed_rate = cat_breaches / cat_trials if cat_trials > 0 else 0.0

            # Gate-keeper check: category-level sample size
            if cat_trials < min_sample_size:
                warnings.append(f"{cat}: insufficient pooled sample size (n={cat_trials}, min={min_sample_size}).")
                cat_verdict = "INSUFFICIENT_DATA"
                cat_binom = None
                cat_wil = None
                cat_binomial_p = None
                cat_ci_lower = None
                cat_ci_upper = None
            else:
                # Run aggregation test on pooled counts
                cat_binom = exact_binomial_test(cat_breaches, cat_trials, target_rate=target_rate, alpha=alpha)
                cat_wil = wilson_ci(cat_breaches, cat_trials, alpha=alpha)
                cat_binomial_p = cat_binom.p_value
                cat_ci_lower = cat_wil.lower
                cat_ci_upper = cat_wil.upper

                # Apply consensus verdict logic to aggregated data
                cat_binomial_pass = cat_binom.meets_target
                cat_ci_clearly_below = cat_wil.upper <= target_rate
                cat_ci_clearly_above = cat_wil.lower > target_rate
                cat_observed_below = cat_observed_rate < target_rate

                if cat_binomial_pass and (cat_ci_clearly_below or cat_observed_below):
                    cat_verdict = "PASS"
                elif cat_ci_clearly_above and not cat_binomial_pass:
                    cat_verdict = "FAIL"
                else:
                    cat_verdict = "INCONCLUSIVE"

            cat_result = CategoryBreachResult(
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
                n_insufficient_data=n_insufficient,
                # Aggregated category-level metrics
                category_breaches=cat_breaches,
                category_trials=cat_trials,
                category_observed_rate=round(cat_observed_rate, 4),
                category_binomial_p=cat_binomial_p,
                category_ci_lower=cat_ci_lower,
                category_ci_upper=cat_ci_upper,
            )

        per_cat.append(cat_result)

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
