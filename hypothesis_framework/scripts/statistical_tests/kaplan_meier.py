"""
Method 15 — Kaplan-Meier Survival Estimator.

Models S(t) = P(metric > t) to estimate SLA compliance probability.
Properly handles right-censored observations (runs that timed out).

Uses statsmodels.duration.SurvfuncRight for the core computation.

Reference:
    Kaplan, E.L. & Meier, P. (1958) JASA, 53(282).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
from statsmodels.duration.survfunc import SurvfuncRight

from hypothesis_framework.schema.test_results import KaplanMeierResult


def kaplan_meier_analysis(
    times: List[float],
    event_observed: List[bool],
    sla_threshold: Optional[float] = None,
) -> KaplanMeierResult:
    """Kaplan-Meier survival analysis with optional SLA evaluation.

    Args:
        times: Observed times (detection time or timeout duration).
        event_observed: True if the event occurred (e.g., fault was detected);
            False if right-censored (e.g., timed out without detection).
        sla_threshold: Optional time threshold to evaluate S(threshold).

    Returns:
        KaplanMeierResult with survival table, median survival, and S(SLA).
    """
    warnings: List[str] = []
    t = np.asarray(times, dtype=float)
    e = np.asarray(event_observed, dtype=bool)

    if len(t) != len(e):
        warnings.append("times and event_observed must have the same length.")
        return KaplanMeierResult(
            sla_threshold=sla_threshold,
            warnings=warnings,
            interpretation="Mismatched input lengths.",
        )

    n = len(t)
    if n == 0:
        warnings.append("Empty data.")
        return KaplanMeierResult(
            sla_threshold=sla_threshold,
            warnings=warnings,
            interpretation="No data provided.",
        )

    n_events = int(np.sum(e))
    n_censored = n - n_events

    if n_events == 0:
        warnings.append("No events observed; survival function is trivially 1.0 everywhere.")

    # Use statsmodels SurvfuncRight for KM estimation
    # SurvfuncRight expects: time, status (1=event, 0=censored)
    status = e.astype(int)
    kmf = SurvfuncRight(t, status)

    # Build survival table from statsmodels results
    surv_times = kmf.surv_times
    surv_prob = kmf.surv_prob
    n_risk = kmf.n_risk
    n_events_at = kmf.n_events

    table: List[Dict[str, Any]] = []
    for i in range(len(surv_times)):
        table.append({
            "time": float(surv_times[i]),
            "events": int(n_events_at[i]),
            "at_risk": int(n_risk[i]),
            "survival": round(float(surv_prob[i]), 6),
        })

    # Find median survival (time when S(t) first drops below 0.5)
    median_survival: Optional[float] = None
    for row in table:
        if row["survival"] <= 0.5:
            median_survival = row["time"]
            break

    # Evaluate S(SLA)
    survival_at_sla: Optional[float] = None
    if sla_threshold is not None:
        survival_at_sla = 1.0
        for row in table:
            if row["time"] <= sla_threshold:
                survival_at_sla = row["survival"]
            else:
                break

    return KaplanMeierResult(
        sla_threshold=sla_threshold,
        survival_at_sla=round(survival_at_sla, 4) if survival_at_sla is not None else None,
        median_survival=round(median_survival, 4) if median_survival is not None else None,
        n_events=n_events,
        n_censored=n_censored,
        survival_table=table,
        interpretation=(
            f"KM: {n_events} events, {n_censored} censored."
            + (f" S({sla_threshold})={survival_at_sla:.3f}"
               f" ({(1 - survival_at_sla)*100:.0f}% detected within SLA)."
               if survival_at_sla is not None else "")
            + (f" Median survival={median_survival:.1f}s."
               if median_survival is not None else " Median not reached.")
        ),
        warnings=warnings,
    )
