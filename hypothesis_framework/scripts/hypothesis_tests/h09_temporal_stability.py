"""
H-09: Temporal Stability & Drift Detection.

CUSUM + EWMA control charts per fault category.
Data must be in run-order (time-sequential).
Target defaults to IQM of the data; with SLA, uses SLA threshold.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from scipy.stats import trim_mean

from hypothesis_framework.schema.hypothesis_results import (
    CategoryDriftResult,
    H09Result,
)
from hypothesis_framework.scripts.statistical_tests.cusum_ewma import cusum_ewma


def run_drift_test(
    data_per_category: Dict[str, List[float]],
    metric_name: str = "time_to_detect",
    target: Optional[float] = None,
    lambda_: float = 0.2,
) -> H09Result:
    """Run H-09: Temporal Stability & Drift Detection.

    CUSUM and EWMA analysis per category on time-ordered observations.

    Args:
        data_per_category: {category: [values_in_run_order]}.
        metric_name: Name of the metric.
        target: Reference value for drift detection (default: IQM of data).
        lambda_: EWMA smoothing factor.

    Returns:
        H09Result with per-category drift verdicts.
    """
    warnings: List[str] = []
    per_cat: List[CategoryDriftResult] = []

    for cat, values in data_per_category.items():
        if len(values) < 2:
            warnings.append(f"{cat}: need at least 2 observations.")
            per_cat.append(CategoryDriftResult(category=cat))
            continue

        cat_target = target
        if cat_target is None:
            cat_target = trim_mean(values, 0.25)

        r = cusum_ewma(values, target=cat_target, lambda_=lambda_)
        warnings.extend(r.warnings)

        drift_verdict = "DRIFT_DETECTED" if r.drift_detected else "STABLE"

        per_cat.append(CategoryDriftResult(
            category=cat,
            cusum_final=r.cusum_final,
            cusum_alarm=r.cusum_alarm,
            ewma_final=r.ewma_final,
            ewma_alarm=r.ewma_alarm,
            drift_verdict=drift_verdict,
        ))

    any_drift = any(c.drift_verdict == "DRIFT_DETECTED" for c in per_cat)
    overall = "drift_detected" if any_drift else "no_drift_detected"

    return H09Result(
        metric_name=metric_name,
        per_category=per_cat,
        overall_assessment=overall,
        warnings=warnings,
    )
