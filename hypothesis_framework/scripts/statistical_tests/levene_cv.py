"""
Method 10 — Levene's Test + Coefficient of Variation (CV).

Tests equality of variances across groups (Levene's) and computes per-group CV.
CV thresholds: <0.15 stable, 0.15-0.30 moderate, >0.30 unreliable.
Used in H-05 to assess consistency/predictability.

Reference:
    Levene, H. (1960) Contributions to Probability and Statistics.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np
from scipy import stats

from hypothesis_framework.schema.test_results import LeveneCVResult


def levene_cv_test(
    *groups: List[float],
    labels: Optional[List[str]] = None,
    alpha: float = 0.05,
) -> LeveneCVResult:
    """Run Levene's test for equality of variances and compute per-group CV.

    Args:
        *groups: Two or more arrays of observed values.
        labels: Optional names for each group.
        alpha: Significance level.

    Returns:
        LeveneCVResult with Levene's statistic, per-group CV values.
    """
    warnings: List[str] = []
    arrays = [np.asarray(g, dtype=float) for g in groups]

    if labels is None:
        labels = [f"group_{i}" for i in range(len(arrays))]

    if len(arrays) < 2:
        warnings.append("Need at least 2 groups.")
        return LeveneCVResult(
            alpha=alpha, warnings=warnings,
            interpretation="Insufficient groups.",
        )

    # Compute per-group CV
    cv_values: List[float] = []
    for i, a in enumerate(arrays):
        if len(a) < 2:
            warnings.append(f"Group '{labels[i]}' has n={len(a)} < 2; CV undefined.")
            cv_values.append(float("nan"))
        else:
            mean_val = np.mean(a)
            if mean_val == 0:
                warnings.append(f"Group '{labels[i]}' has mean=0; CV undefined.")
                cv_values.append(float("inf"))
            else:
                cv_values.append(round(float(np.std(a, ddof=1) / abs(mean_val)), 4))

    # Filter valid groups for Levene's
    valid = [a for a in arrays if len(a) >= 2]
    if len(valid) < 2:
        warnings.append("Fewer than 2 valid groups for Levene's test.")
        return LeveneCVResult(
            alpha=alpha, cv_per_group=cv_values, cv_labels=labels,
            warnings=warnings,
            interpretation="Insufficient valid groups for Levene's.",
        )

    lev_stat, lev_p = stats.levene(*valid, center="median")
    variances_equal = lev_p >= alpha

    return LeveneCVResult(
        alpha=alpha,
        statistic=round(float(lev_stat), 4),
        p_value=round(float(lev_p), 6),
        levene_statistic=round(float(lev_stat), 4),
        levene_p=round(float(lev_p), 6),
        variances_equal=variances_equal,
        cv_per_group=cv_values,
        cv_labels=labels,
        interpretation=(
            f"Levene's F={lev_stat:.2f}, p={lev_p:.4f} → "
            f"variances {'EQUAL' if variances_equal else 'NOT EQUAL'} at α={alpha}. "
            f"CVs: {', '.join(f'{l}={v:.2f}' for l, v in zip(labels, cv_values))}."
        ),
        warnings=warnings,
    )
