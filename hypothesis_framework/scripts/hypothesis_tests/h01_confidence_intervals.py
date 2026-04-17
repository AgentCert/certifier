"""
H-01: Confidence Intervals for Continuous Metrics.

Computes IQM per sub-fault, then equal-weight averages sub-fault IQMs
to produce the category-level estimate. Bootstrap BCa CI is computed on
the equal-weight estimator.

Aggregation levels (per doc):
  - Per-Fault: IQM per sub-fault (container-kill, pod-delete, etc.)
  - Category:  equal-weight avg of sub-fault IQMs + Bootstrap CI

Metrics: time_to_detect, time_to_mitigate, reasoning_score, hallucination_score
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

import numpy as np
from scipy.stats import bootstrap as scipy_bootstrap, trim_mean

from hypothesis_framework.schema.hypothesis_results import (
    CategoryCIResult,
    H01Result,
    SubFaultCIResult,
)
from hypothesis_framework.scripts.statistical_tests.iqm import interquartile_mean


def _equal_weight_iqm(subfault_arrays: List[np.ndarray]) -> float:
    """Compute equal-weight average of per-sub-fault IQMs."""
    iqms = []
    for arr in subfault_arrays:
        if len(arr) >= 4:
            iqms.append(trim_mean(arr, 0.25))
        elif len(arr) > 0:
            iqms.append(float(np.mean(arr)))
    return float(np.mean(iqms)) if iqms else 0.0


def run_confidence_interval_test(
    data_per_category: Dict[str, Dict[str, List[float]]],
    metric_name: str = "time_to_detect",
    alpha: float = 0.05,
    n_resamples: int = 10000,
    random_state: Optional[int] = None,
) -> H01Result:
    """Run H-01: Confidence Intervals for Continuous Metrics.

    For each category, computes IQM per sub-fault, then equal-weight averages
    them for the category estimate. Bootstrap BCa CI is computed on the
    equal-weight estimator to properly quantify uncertainty.

    Args:
        data_per_category: {category: {sub_fault: [values]}}.
            Data should be detected-only values (exclude censored/timed-out).
        metric_name: Name of the metric being analysed.
        alpha: Significance level for CIs.
        n_resamples: Bootstrap resamples.
        random_state: Seed for reproducibility.

    Returns:
        H01Result with per-category CI results including sub-fault breakdown.
    """
    warnings: List[str] = []
    per_cat: List[CategoryCIResult] = []

    for cat, subfaults in data_per_category.items():
        # Build per-sub-fault results
        sub_results: List[SubFaultCIResult] = []
        subfault_arrays: List[np.ndarray] = []
        all_values: List[float] = []

        for fname, values in subfaults.items():
            arr = np.asarray(values, dtype=float)
            subfault_arrays.append(arr)
            all_values.extend(values)

            if len(arr) < 4:
                warnings.append(f"{cat}/{fname}: n={len(arr)} too small for IQM trimming.")

            iqm_val = trim_mean(arr, 0.25) if len(arr) >= 4 else float(np.mean(arr)) if len(arr) > 0 else 0.0

            sub_results.append(SubFaultCIResult(
                fault_name=fname,
                n=len(arr),
                iqm=round(iqm_val, 2),
                median=round(float(np.median(arr)), 2) if len(arr) > 0 else 0.0,
                mean=round(float(np.mean(arr)), 2) if len(arr) > 0 else 0.0,
                p95=round(float(np.percentile(arr, 95)), 2) if len(arr) > 0 else 0.0,
            ))

        all_arr = np.asarray(all_values, dtype=float)
        n_total = len(all_arr)
        n_subfaults = len(subfault_arrays)

        if n_total < 4:
            warnings.append(f"{cat}: total n={n_total} too small for reliable CI.")
            per_cat.append(CategoryCIResult(
                category=cat, n=n_total, n_sub_faults=n_subfaults,
                sub_faults=sub_results,
            ))
            continue

        # Category IQM = equal-weight average of sub-fault IQMs
        cat_iqm = _equal_weight_iqm(subfault_arrays)

        # Bootstrap CI on the equal-weight estimator
        # Resample within each sub-fault, then average their IQMs
        rng = np.random.default_rng(random_state)
        boot_iqms = np.empty(n_resamples)
        for b in range(n_resamples):
            resampled_iqms = []
            for sf_arr in subfault_arrays:
                if len(sf_arr) == 0:
                    continue
                boot_sample = rng.choice(sf_arr, size=len(sf_arr), replace=True)
                if len(boot_sample) >= 4:
                    resampled_iqms.append(trim_mean(boot_sample, 0.25))
                else:
                    resampled_iqms.append(float(np.mean(boot_sample)))
            boot_iqms[b] = np.mean(resampled_iqms)

        ci_lower = float(np.percentile(boot_iqms, 100 * alpha / 2))
        ci_upper = float(np.percentile(boot_iqms, 100 * (1 - alpha / 2)))
        ci_width = ci_upper - ci_lower

        # Identify worst sub-fault (highest IQM for time metrics, lowest for scores)
        worst = max(sub_results, key=lambda s: s.iqm) if sub_results else None

        per_cat.append(CategoryCIResult(
            category=cat,
            n=n_total,
            n_sub_faults=n_subfaults,
            iqm=round(cat_iqm, 2),
            median=round(float(np.median(all_arr)), 2),
            mean=round(float(np.mean(all_arr)), 2),
            p95=round(float(np.percentile(all_arr, 95)), 2),
            ci_lower=round(ci_lower, 4),
            ci_upper=round(ci_upper, 4),
            ci_width=round(ci_width, 4),
            sub_faults=sub_results,
            worst_sub_fault=worst.fault_name if worst else "",
        ))

    widths = [c.ci_width for c in per_cat if c.n >= 4]
    overall = "precise" if widths and max(widths) < 100 else "wide_intervals"

    return H01Result(
        metric_name=metric_name,
        alpha=alpha,
        per_category=per_cat,
        overall_assessment=overall,
        warnings=warnings,
    )
