"""
Method 9 — Chi-Square / Fisher's Exact Test.

Tests whether success rates are uniform across fault categories.
Automatically selects Fisher's exact test (2×2 tables or when expected counts < 5)
or chi-square test (larger tables).

Reference:
    Fisher, R.A. (1922) JRSS.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np
from scipy import stats

from hypothesis_framework.schema.test_results import ContingencyTestResult


def chi_square_fisher_test(
    contingency_table: List[List[int]],
    alpha: float = 0.05,
) -> ContingencyTestResult:
    """Run chi-square or Fisher's exact test on a contingency table.

    Automatically chooses Fisher's exact for 2×2 tables or when any
    expected cell count < 5.  Falls back to chi-square otherwise.

    Args:
        contingency_table: 2D list of counts, e.g. [[27,3],[15,15],[21,9]].
        alpha: Significance level.

    Returns:
        ContingencyTestResult with test name, statistic, p-value, significance.
    """
    warnings: List[str] = []
    table = np.asarray(contingency_table, dtype=int)

    if table.ndim != 2 or table.shape[0] < 2 or table.shape[1] < 2:
        warnings.append("Table must be at least 2×2.")
        return ContingencyTestResult(
            alpha=alpha, test_used="none",
            table=contingency_table,
            warnings=warnings,
            interpretation="Invalid contingency table shape.",
        )

    if np.any(table < 0):
        warnings.append("Negative counts detected; results may be invalid.")

    rows, cols = table.shape
    use_fisher = False
    test_used = "chi_square"

    # Check if Fisher's exact is appropriate
    if rows == 2 and cols == 2:
        use_fisher = True
    else:
        # Check expected counts for chi-square validity
        row_totals = table.sum(axis=1)
        col_totals = table.sum(axis=0)
        grand_total = table.sum()
        if grand_total > 0:
            expected = np.outer(row_totals, col_totals) / grand_total
            if np.any(expected < 5):
                warnings.append("Expected counts < 5 in some cells.")
                # For R×C with small expected, try Fisher-Freeman-Halton
                if rows == 2 and cols == 2:
                    use_fisher = True
                else:
                    warnings.append(
                        "Using chi-square despite low expected counts "
                        "(Fisher-Freeman-Halton not available for R×C > 2×2)."
                    )

    stat_val: Optional[float] = None
    p_val: float = 1.0

    if use_fisher:
        test_used = "fisher_exact"
        _, p_val = stats.fisher_exact(table)
        stat_val = None
    else:
        test_used = "chi_square"
        # Check for zero rows/columns that make chi-square undefined
        row_totals = table.sum(axis=1)
        col_totals = table.sum(axis=0)
        if np.any(row_totals == 0) or np.any(col_totals == 0):
            warnings.append(
                "Zero marginal total detected — all counts in one row/column are zero. "
                "Chi-square is undefined; treating as not significant."
            )
            return ContingencyTestResult(
                alpha=alpha, test_used="chi_square",
                statistic=None, p_value=1.0, significant=False,
                table=contingency_table,
                interpretation="Chi-square undefined due to zero marginal totals.",
                warnings=warnings,
            )
        chi2, p_val, dof, expected = stats.chi2_contingency(table)
        stat_val = round(float(chi2), 4)

    significant = p_val < alpha

    return ContingencyTestResult(
        alpha=alpha,
        test_used=test_used,
        statistic=stat_val,
        p_value=round(float(p_val), 6),
        significant=significant,
        table=contingency_table,
        interpretation=(
            f"{test_used}: p={p_val:.4f} → "
            f"{'SIGNIFICANT' if significant else 'not significant'} at α={alpha}. "
            f"{'Rates are NOT uniform.' if significant else 'No evidence rates differ.'}"
        ),
        warnings=warnings,
    )
