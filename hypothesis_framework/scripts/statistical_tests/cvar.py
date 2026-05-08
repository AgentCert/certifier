"""
Method 14 — CVaR (Conditional Value-at-Risk).

Quantifies tail-risk severity: "how bad are the worst cases?"
CVaR₉₅ = mean of all values above the 95th percentile.
Optionally computes expected SLA overshoot.

Reference:
    Rockafellar & Uryasev (2000) Journal of Risk, 2(3).
    Artzner et al. (1999) Mathematical Finance, 9(3).
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np

from hypothesis_framework.schema.test_results import CVaRResult


def cvar_analysis(
    data: List[float],
    quantile: float = 0.95,
    sla_threshold: Optional[float] = None,
) -> CVaRResult:
    """Compute CVaR (Conditional Value-at-Risk) tail-risk analysis.

    Args:
        data: Observed metric values.
        quantile: Quantile level for VaR (default 0.95 = worst 5%).
        sla_threshold: Optional SLA threshold for overshoot calculation.

    Returns:
        CVaRResult with VaR, CVaR, and optional overshoot metrics.
    """
    warnings: List[str] = []
    arr = np.sort(np.asarray(data, dtype=float))
    n = len(arr)

    if n == 0:
        warnings.append("Empty data.")
        return CVaRResult(
            quantile_level=quantile, sla_threshold=sla_threshold,
            warnings=warnings,
            interpretation="No data provided.",
        )

    if n < 20:
        warnings.append(
            f"n={n}; tail estimates from < 20 observations are highly uncertain."
        )

    # VaR = quantile value
    var_val = float(np.percentile(arr, 100 * quantile))

    # CVaR = mean of values above VaR
    tail_idx = int(np.ceil(n * quantile))
    tail = arr[tail_idx:]
    n_tail = len(tail)

    if n_tail == 0:
        # Include at least the last observation
        tail = arr[-1:]
        n_tail = 1
        warnings.append("Tail index at boundary; using last observation.")

    cvar_val = float(np.mean(tail))

    # SLA overshoot analysis
    expected_overshoot: Optional[float] = None
    n_breaches: Optional[int] = None
    if sla_threshold is not None:
        breaches = arr[arr > sla_threshold]
        n_breaches = len(breaches)
        if n_breaches > 0:
            expected_overshoot = float(np.mean(breaches - sla_threshold))
        else:
            expected_overshoot = 0.0

    return CVaRResult(
        quantile_level=quantile,
        var=round(var_val, 4),
        cvar=round(cvar_val, 4),
        n_tail=n_tail,
        sla_threshold=sla_threshold,
        expected_overshoot=round(expected_overshoot, 4) if expected_overshoot is not None else None,
        n_breaches=n_breaches,
        statistic=round(cvar_val, 4),
        interpretation=(
            f"VaR{quantile*100:.0f}={var_val:.1f}, "
            f"CVaR{quantile*100:.0f}={cvar_val:.1f} "
            f"(mean of worst {(1-quantile)*100:.0f}%, n_tail={n_tail})"
            + (f". SLA overshoot: {expected_overshoot:.1f} across {n_breaches} breaches"
               if expected_overshoot is not None else "")
        ),
        warnings=warnings,
    )
