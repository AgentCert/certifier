"""
Method 3 — Interquartile Mean (IQM).

25% trimmed mean — drops bottom 25% and top 25%, averages the middle 50%.
Used in H-01 as an outlier-robust measure of central tendency.

Uses scipy.stats.trim_mean for the core computation.

Reference:
    Agarwal et al. (2021) "Deep RL at the Edge of the Statistical Precipice."
    arXiv:2108.13264.
"""

from __future__ import annotations

from typing import List

import numpy as np
from scipy.stats import trim_mean

from hypothesis_framework.schema.test_results import IQMResult


def interquartile_mean(data: List[float]) -> IQMResult:
    """Compute the interquartile mean (25% trimmed mean).

    Args:
        data: Observed sample values.

    Returns:
        IQMResult with IQM value, total count, and trimmed count.
    """
    warnings: List[str] = []
    arr = np.asarray(data, dtype=float)
    n = len(arr)

    if n == 0:
        warnings.append("Empty data; IQM undefined.")
        return IQMResult(
            warnings=warnings,
            interpretation="No data provided.",
        )

    if n < 4:
        warnings.append(f"n={n} < 4; trimming degenerates to plain mean.")
        iqm_val = float(np.mean(arr))
        return IQMResult(
            iqm=round(iqm_val, 4),
            n_total=n,
            n_trimmed=0,
            interpretation=f"IQM={iqm_val:.2f} (n too small, no trimming applied).",
            warnings=warnings,
        )

    iqm_val = float(trim_mean(arr, proportiontocut=0.25))
    n_trimmed = 2 * int(np.floor(n * 0.25))

    return IQMResult(
        iqm=round(iqm_val, 4),
        n_total=n,
        n_trimmed=n_trimmed,
        interpretation=f"IQM={iqm_val:.2f} (trimmed {n_trimmed} of {n} observations).",
        warnings=warnings,
    )
