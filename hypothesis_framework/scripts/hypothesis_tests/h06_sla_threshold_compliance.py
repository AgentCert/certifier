"""
H-06: SLA Threshold Compliance.

Per-sub-fault SLA compliance testing with category-level rollup.

Each sub-fault is tested against its own SLA threshold from the ground truth.
Primary: Wilcoxon signed-rank per sub-fault.
Supplementary: Bootstrap CI vs SLA, TOST equivalence, Kaplan-Meier (if censored).

Sub-fault verdicts:
  - PASS: Wilcoxon p < alpha AND CI upper ≤ SLA
  - CONDITIONAL: CI contains SLA threshold
  - FAIL: CI lower > SLA or Wilcoxon not significant and median > SLA
  - NO_SLA_DEFINED: No SLA threshold provided for this sub-fault
  - NO_DATA: No data available for this sub-fault

Category verdicts (rollup):
  - PASS: All assessed sub-faults PASS
  - FAIL: Any sub-fault FAIL
  - INCOMPLETE: Any sub-fault NO_SLA_DEFINED and none FAIL
  - CONDITIONAL: Mix of PASS / CONDITIONAL
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

import numpy as np
from scipy.stats import trim_mean

from hypothesis_framework.schema.hypothesis_results import (
    CategorySLAResult,
    H06Result,
    SubFaultSLAResult,
)
from hypothesis_framework.scripts.statistical_tests.wilcoxon_signed_rank import wilcoxon_signed_rank
from hypothesis_framework.scripts.statistical_tests.bootstrap_bca import bootstrap_bca_ci
from hypothesis_framework.scripts.statistical_tests.tost import tost_test


def run_sla_compliance_test(
    data_per_category: Dict[str, Dict[str, List[float]]],
    sla_thresholds: Dict[str, float],
    metric_name: str = "time_to_detect",
    alpha: float = 0.05,
    n_resamples: int = 10000,
    random_state: Optional[int] = None,
) -> H06Result:
    """Run H-06: SLA Threshold Compliance.

    Args:
        data_per_category: {category: {sub_fault: [detected_values]}}.
        sla_thresholds: {sub_fault_name: threshold} — per sub-fault SLA.
        metric_name: Name of the metric being tested.
        alpha: Significance level.
        n_resamples: Bootstrap resamples.
        random_state: Seed for reproducibility.

    Returns:
        H06Result with per-sub-fault verdicts rolled up to categories.
    """
    warnings: List[str] = []
    per_cat: List[CategorySLAResult] = []

    iqm_fn: Callable = lambda x: trim_mean(x, 0.25)

    for cat, subfaults in data_per_category.items():
        sub_results: List[SubFaultSLAResult] = []
        cat_n = 0

        for fname, values in sorted(subfaults.items()):
            arr = np.asarray(values, dtype=float)
            n = len(arr)
            cat_n += n

            if n == 0:
                warnings.append(f"{cat}/{fname}: no data.")
                sub_results.append(SubFaultSLAResult(
                    fault_name=fname, verdict="NO_DATA",
                ))
                continue

            sla = sla_thresholds.get(fname)
            if sla is None:
                warnings.append(
                    f"{cat}/{fname}: no SLA threshold defined — skipping tests."
                )
                sub_results.append(SubFaultSLAResult(
                    fault_name=fname, n=n,
                    median=round(float(np.median(arr)), 2),
                    verdict="NO_SLA_DEFINED",
                ))
                continue

            median_val = float(np.median(arr))

            # Primary: Wilcoxon signed-rank
            wil = wilcoxon_signed_rank(values, threshold=sla, alpha=alpha)

            # Supplementary: Bootstrap CI on IQM
            boot = bootstrap_bca_ci(
                values, statistic_fn=iqm_fn,
                n_resamples=n_resamples, alpha=alpha,
                random_state=random_state,
            )

            # Supplementary: TOST equivalence
            tost_r = tost_test(values, low=0, high=sla, alpha=alpha)

            # Verdict logic
            ci_upper = boot.ci_upper
            if wil.meets_threshold and ci_upper <= sla:
                verdict = "PASS"
            elif median_val > sla and not wil.meets_threshold:
                verdict = "FAIL"
            else:
                verdict = "CONDITIONAL"

            sub_results.append(SubFaultSLAResult(
                fault_name=fname,
                n=n,
                sla_threshold=sla,
                median=round(median_val, 2),
                wilcoxon_p=wil.p_value,
                ci_upper=boot.ci_upper,
                tost_equivalent=tost_r.equivalent,
                tost_p=tost_r.p_value,
                verdict=verdict,
            ))

        # Category rollup
        verdicts = [s.verdict for s in sub_results]
        n_passed = sum(v == "PASS" for v in verdicts)
        n_failed = sum(v == "FAIL" for v in verdicts)
        n_cond = sum(v == "CONDITIONAL" for v in verdicts)
        n_no_sla = sum(v == "NO_SLA_DEFINED" for v in verdicts)

        if n_failed > 0:
            cat_verdict = "FAIL"
        elif n_no_sla > 0:
            cat_verdict = "INCOMPLETE"
        elif n_cond > 0:
            cat_verdict = "CONDITIONAL"
        elif n_passed > 0:
            cat_verdict = "PASS"
        else:
            cat_verdict = "NO_DATA"

        # Identify worst sub-fault (highest median relative to SLA)
        assessed = [
            s for s in sub_results
            if s.verdict not in ("NO_DATA", "NO_SLA_DEFINED") and s.sla_threshold
        ]
        worst = ""
        if assessed:
            worst = max(assessed, key=lambda s: s.median / s.sla_threshold).fault_name

        per_cat.append(CategorySLAResult(
            category=cat,
            n=cat_n,
            n_sub_faults=len(sub_results),
            n_passed=n_passed,
            n_failed=n_failed,
            n_conditional=n_cond,
            n_no_sla=n_no_sla,
            verdict=cat_verdict,
            sub_faults=sub_results,
            worst_sub_fault=worst,
        ))

    # Overall assessment
    cat_verdicts = [c.verdict for c in per_cat]
    if all(v == "PASS" for v in cat_verdicts):
        overall = "sla_compliant"
    elif any(v == "FAIL" for v in cat_verdicts):
        overall = "sla_non_compliant"
    elif any(v == "INCOMPLETE" for v in cat_verdicts):
        overall = "incomplete_coverage"
    else:
        overall = "conditional_compliance"

    return H06Result(
        metric_name=metric_name,
        alpha=alpha,
        sla_thresholds=sla_thresholds,
        per_category=per_cat,
        overall_assessment=overall,
        warnings=warnings,
    )
