"""
Method 16 — CUSUM / EWMA Control Charts.

Drift detection for sequential observations.
CUSUM tracks cumulative deviations; EWMA smooths with exponential weighting.
Both signal when performance degrades over time.

Reference:
    Page, E.S. (1954) Biometrika, 41(1-2).
    Roberts, S.W. (1959) Technometrics.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np

from hypothesis_framework.schema.test_results import CusumEwmaResult


def cusum_ewma(
    data: List[float],
    target: Optional[float] = None,
    k: Optional[float] = None,
    h: Optional[float] = None,
    lambda_: float = 0.2,
    L: float = 3.0,
) -> CusumEwmaResult:
    """CUSUM and EWMA drift detection on sequential observations.

    Args:
        data: Time-ordered observations.
        target: Reference value (default: mean of data).
        k: CUSUM allowable slack (default: 0.5 * std of data).
        h: CUSUM alarm threshold (default: 5 * std of data).
        lambda_: EWMA smoothing factor (0 < λ ≤ 1, typical 0.1-0.3).
        L: EWMA control limit multiplier (number of sigmas).

    Returns:
        CusumEwmaResult with CUSUM/EWMA values, limits, and alarm flags.
    """
    warnings: List[str] = []
    arr = np.asarray(data, dtype=float)
    n = len(arr)

    if n < 2:
        warnings.append(f"n={n}; need at least 2 observations for drift detection.")
        return CusumEwmaResult(
            warnings=warnings,
            interpretation="Insufficient data.",
        )

    if n < 30:
        warnings.append(
            f"n={n} < 30; CUSUM/EWMA have limited power. "
            "Interpret drift verdicts as directional indicators."
        )

    mean_val = float(np.mean(arr))
    std_val = float(np.std(arr, ddof=1))

    if target is None:
        target = mean_val
    if k is None:
        k = 0.5 * std_val if std_val > 0 else 0.0
    if h is None:
        h = 5.0 * std_val if std_val > 0 else 1.0

    # ── CUSUM (upper one-sided) ──
    cusum_values: List[float] = []
    s = 0.0
    cusum_alarm = False
    for x in arr:
        s = max(0.0, s + (x - target) - k)
        cusum_values.append(round(s, 4))
        if s > h:
            cusum_alarm = True

    cusum_final = cusum_values[-1]

    # ── EWMA ──
    ewma_values: List[float] = []
    z = target
    sigma = std_val if std_val > 0 else 1.0
    ewma_alarm = False

    for i, x in enumerate(arr):
        z = lambda_ * x + (1 - lambda_) * z
        ewma_values.append(round(z, 4))

    # Steady-state control limits
    ewma_se = sigma * np.sqrt(lambda_ / (2 - lambda_))
    ewma_upper = target + L * ewma_se
    ewma_lower = target - L * ewma_se

    for z_val in ewma_values:
        if z_val > ewma_upper or z_val < ewma_lower:
            ewma_alarm = True
            break

    ewma_final = ewma_values[-1]
    drift_detected = cusum_alarm or ewma_alarm

    return CusumEwmaResult(
        cusum_final=round(cusum_final, 4),
        cusum_threshold=round(h, 4),
        cusum_alarm=cusum_alarm,
        ewma_final=round(ewma_final, 4),
        ewma_upper_limit=round(ewma_upper, 4),
        ewma_lower_limit=round(ewma_lower, 4),
        ewma_alarm=ewma_alarm,
        drift_detected=drift_detected,
        cusum_values=cusum_values,
        ewma_values=ewma_values,
        interpretation=(
            f"CUSUM: S_final={cusum_final:.1f} (h={h:.1f}) → "
            f"{'ALARM' if cusum_alarm else 'stable'}. "
            f"EWMA(λ={lambda_}): Z_final={ewma_final:.1f} "
            f"[{ewma_lower:.1f}, {ewma_upper:.1f}] → "
            f"{'ALARM' if ewma_alarm else 'stable'}. "
            f"Overall: {'DRIFT DETECTED' if drift_detected else 'STABLE'}."
        ),
        warnings=warnings,
    )
