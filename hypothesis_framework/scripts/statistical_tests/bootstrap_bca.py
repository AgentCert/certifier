"""
Method 2 — Bootstrap BCa Confidence Interval.

Bias-corrected and accelerated bootstrap CI for any continuous statistic.
Used in H-01 to bound plausible ranges for metrics like time-to-detect.

Uses scipy.stats.bootstrap for the core computation.

Reference:
    Efron, B. (1987) "Better Bootstrap Confidence Intervals." JASA, 82(397).
    Agarwal et al. (2021) "Deep RL at the Edge of the Statistical Precipice." NeurIPS.
"""

from __future__ import annotations

from typing import Callable, List, Optional

import numpy as np
from scipy.stats import bootstrap as scipy_bootstrap

from hypothesis_framework.schema.test_results import BootstrapBCaResult


def bootstrap_bca_ci(
    data: List[float],
    statistic_fn: Optional[Callable] = None,
    n_resamples: int = 10000,
    alpha: float = 0.05,
    random_state: Optional[int] = None,
) -> BootstrapBCaResult:
    """Compute BCa bootstrap confidence interval.

    Args:
        data: Observed sample values.
        statistic_fn: Function to compute on each resample (default: np.mean).
            Must accept a 1-D array and return a scalar.
        n_resamples: Number of bootstrap resamples.
        alpha: Significance level.
        random_state: Seed for reproducibility.

    Returns:
        BootstrapBCaResult with observed statistic, CI bounds, and width.
    """
    warnings: List[str] = []
    if statistic_fn is None:
        statistic_fn = np.mean

    arr = np.asarray(data, dtype=float)
    n = len(arr)

    if n < 3:
        warnings.append(f"Sample size n={n} too small for reliable bootstrap.")
        obs = float(statistic_fn(arr)) if n > 0 else 0.0
        return BootstrapBCaResult(
            alpha=alpha, observed_statistic=obs,
            ci_lower=obs, ci_upper=obs, ci_width=0.0,
            n_resamples=n_resamples, random_state=random_state,
            warnings=warnings,
            interpretation="Sample too small for bootstrap CI.",
        )

    observed = float(statistic_fn(arr))

    # Wrap statistic_fn for scipy.stats.bootstrap (expects axis argument)
    def _stat_wrapper(x, axis=None):
        if axis is not None:
            return np.apply_along_axis(statistic_fn, axis, x)
        return statistic_fn(x)

    rng = np.random.default_rng(random_state)

    result = scipy_bootstrap(
        (arr,),
        statistic=_stat_wrapper,
        n_resamples=n_resamples,
        confidence_level=1 - alpha,
        method="BCa",
        random_state=rng,
    )

    ci_lower = float(result.confidence_interval.low)
    ci_upper = float(result.confidence_interval.high)
    ci_width = ci_upper - ci_lower

    return BootstrapBCaResult(
        alpha=alpha,
        observed_statistic=round(observed, 4),
        ci_lower=round(ci_lower, 4),
        ci_upper=round(ci_upper, 4),
        ci_width=round(ci_width, 4),
        n_resamples=n_resamples,
        random_state=random_state,
        confidence_interval=(round(ci_lower, 4), round(ci_upper, 4)),
        interpretation=(
            f"Observed={observed:.2f}; "
            f"{(1-alpha)*100:.0f}% BCa CI: [{ci_lower:.2f}, {ci_upper:.2f}] "
            f"(width={ci_width:.2f})"
        ),
        warnings=warnings,
    )
