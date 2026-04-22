"""
H-09: Temporal Stability & Drift Detection.

Per-sub-fault CUSUM + EWMA control charts with category-level rollup.

Always active — does not require SLA thresholds.
Data must be in run-order (time-sequential).
Target defaults to IQM of each sub-fault's data.

Sub-fault verdicts:
  - STABLE: no drift detected
  - DRIFT_DETECTED: CUSUM or EWMA alarm triggered
  - LOW_POWER: too few observations for reliable drift detection (n < 8)

Category drift: DRIFT_DETECTED if any sub-fault has drift.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from scipy.stats import trim_mean

from hypothesis_framework.schema.hypothesis_results import (
    CategoryDriftResult,
    H09Result,
    SubFaultDriftResult,
)
from hypothesis_framework.scripts.statistical_tests.cusum_ewma import cusum_ewma


def run_drift_test(
    data_per_category: Dict[str, Dict[str, List[float]]],
    metric_name: str = "time_to_detect",
    target: Optional[float] = None,
    lambda_: float = 0.2,
) -> H09Result:
    """Run H-09: Temporal Stability & Drift Detection.

    CUSUM and EWMA analysis per sub-fault on time-ordered observations.

    Args:
        data_per_category: {category: {sub_fault: [values_in_run_order]}}.
        metric_name: Name of the metric.
        target: Reference value for drift detection (default: IQM per sub-fault).
        lambda_: EWMA smoothing factor.

    Returns:
        H09Result with per-sub-fault drift verdicts rolled up to categories.
    """
    warnings: List[str] = []
    per_cat: List[CategoryDriftResult] = []

    for cat, subfaults in data_per_category.items():
        sub_results: List[SubFaultDriftResult] = []
        cat_n = 0

        for fname, values in sorted(subfaults.items()):
            n = len(values)
            cat_n += n

            if n < 2:
                warnings.append(f"{cat}/{fname}: need at least 2 observations.")
                sub_results.append(SubFaultDriftResult(
                    fault_name=fname, n=n, drift_verdict="LOW_POWER",
                ))
                continue

            if n < 8:
                warnings.append(
                    f"{cat}/{fname}: n={n} < 8; drift detection has very low power."
                )
                sub_results.append(SubFaultDriftResult(
                    fault_name=fname, n=n, drift_verdict="LOW_POWER",
                ))
                continue

            sf_target = target
            if sf_target is None:
                sf_target = trim_mean(values, 0.25)

            r = cusum_ewma(values, target=sf_target, lambda_=lambda_)
            warnings.extend(r.warnings)

            drift_verdict = "DRIFT_DETECTED" if r.drift_detected else "STABLE"

            sub_results.append(SubFaultDriftResult(
                fault_name=fname,
                n=n,
                cusum_final=r.cusum_final,
                cusum_alarm=r.cusum_alarm,
                ewma_final=r.ewma_final,
                ewma_alarm=r.ewma_alarm,
                drift_verdict=drift_verdict,
            ))

        # Category rollup
        any_drift = any(
            s.drift_verdict == "DRIFT_DETECTED" for s in sub_results
        )
        all_low = all(
            s.drift_verdict == "LOW_POWER" for s in sub_results
        )

        if any_drift:
            cat_verdict = "DRIFT_DETECTED"
        elif all_low:
            cat_verdict = "LOW_POWER"
        else:
            cat_verdict = "STABLE"

        per_cat.append(CategoryDriftResult(
            category=cat,
            n=cat_n,
            n_sub_faults=len(sub_results),
            drift_verdict=cat_verdict,
            sub_faults=sub_results,
        ))

    any_drift = any(c.drift_verdict == "DRIFT_DETECTED" for c in per_cat)
    overall = "drift_detected" if any_drift else "no_drift_detected"

    return H09Result(
        metric_name=metric_name,
        per_category=per_cat,
        overall_assessment=overall,
        warnings=warnings,
    )
