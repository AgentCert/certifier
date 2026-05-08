"""
H-04: Cross-Category Success Rate Uniformity.

Chi-square test (or Fisher's exact for 2×2) on a contingency table of
success/failure counts across fault categories.

Aggregation: POOLED counts per category for the contingency table.
Equal-weight rate reported alongside for H-02 consistency.
Within-category heterogeneity check flags categories where sub-fault
rates differ significantly.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from hypothesis_framework.schema.hypothesis_results import (
    CategoryRateComparisonDetail,
    H04Result,
    SubFaultRateResult,
)
from hypothesis_framework.scripts.statistical_tests.chi_square_fisher import chi_square_fisher_test
from hypothesis_framework.scripts.statistical_tests.wilson_ci import wilson_ci


def run_uniformity_test(
    counts_per_category: Dict[str, Dict[str, Tuple[int, int]]],
    metric_name: str = "fault_detection_success_rate",
    alpha: float = 0.05,
) -> H04Result:
    """Run H-04: Cross-Category Success Rate Uniformity.

    Builds a contingency table [success, failure] per category (pooled from
    sub-faults) and runs Chi-square or Fisher's exact test.

    Args:
        counts_per_category: {category: {sub_fault: (successes, trials)}}.
        metric_name: Name of the rate metric.
        alpha: Significance level.

    Returns:
        H04Result with test used, p-value, sub-fault breakdown, and weakest category.
    """
    warnings: List[str] = []
    cat_details: List[CategoryRateComparisonDetail] = []
    table: List[List[int]] = []
    rates: Dict[str, float] = {}

    for cat, subfaults in counts_per_category.items():
        sub_results: List[SubFaultRateResult] = []
        total_s, total_n = 0, 0

        for fname, (s, n) in subfaults.items():
            total_s += s
            total_n += n
            r = wilson_ci(s, n, alpha=alpha)
            sub_results.append(SubFaultRateResult(
                fault_name=fname,
                successes=s,
                trials=n,
                rate=round(s / n, 4) if n > 0 else 0.0,
                wilson_lower=r.lower,
                wilson_upper=r.upper,
            ))

        pooled_rate = round(total_s / total_n, 4) if total_n > 0 else 0.0
        sf_rates = [sf.rate for sf in sub_results]
        ew_rate = round(sum(sf_rates) / len(sf_rates), 4) if sf_rates else 0.0

        # Within-category heterogeneity (chi-square among sub-faults)
        within_het = False
        within_p: Optional[float] = None
        if len(sub_results) >= 2:
            within_table = [[sf.successes, sf.trials - sf.successes] for sf in sub_results]
            within_r = chi_square_fisher_test(within_table, alpha=alpha)
            within_p = within_r.p_value
            if within_p is not None and within_p < alpha:
                within_het = True
                warnings.append(
                    f"{cat}: sub-fault rates are heterogeneous (p={within_p:.4f}). "
                    f"Pooled cross-category comparison may mask sub-fault differences."
                )

        table.append([total_s, total_n - total_s])
        rates[cat] = pooled_rate

        cat_details.append(CategoryRateComparisonDetail(
            category=cat,
            successes=total_s,
            trials=total_n,
            rate=pooled_rate,
            n_sub_faults=len(sub_results),
            equal_weight_rate=ew_rate,
            within_heterogeneous=within_het,
            within_p=round(within_p, 6) if within_p is not None else None,
            sub_faults=sub_results,
        ))

    # Omnibus: Chi-square / Fisher on category-level contingency table
    r = chi_square_fisher_test(table, alpha=alpha)
    warnings.extend(r.warnings)

    p_val = r.p_value if r.p_value is not None else 1.0
    weakest = min(rates, key=rates.get) if rates else ""

    return H04Result(
        metric_name=metric_name,
        alpha=alpha,
        test_used=r.test_used,
        statistic=r.statistic,
        p_value=p_val,
        significant=r.significant,
        per_category=cat_details,
        per_category_rates=rates,
        weakest_category=weakest,
        overall_assessment="non_uniform_rates" if r.significant else "uniform_rates",
        warnings=warnings,
    )
