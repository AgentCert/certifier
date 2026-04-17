"""
H-03: Cross-Category Performance Comparison.

Pipeline:
  1. Pool sub-fault values within each category
  2. Per-category: Shapiro-Wilk normality, descriptive stats, within-category
     heterogeneity check (KW among sub-faults)
  3. Omnibus: Kruskal-Wallis H-test (nonparametric — robust to non-normal SRE data)
  4. Post-hoc: Mann-Whitney U + Vargha-Delaney A12, Holm-Bonferroni corrected

Aggregation: POOLED per-run comparison across categories.
This answers "does a random detected run from category A differ from category B?"
Equal-weight IQM is reported alongside for H-01 consistency.
"""

from __future__ import annotations

from itertools import combinations
from typing import Dict, List, Optional

import numpy as np
from scipy.stats import trim_mean

from hypothesis_framework.schema.hypothesis_results import (
    CategoryComparisonDetail,
    H03Result,
    PairwiseComparison,
    SubFaultComparisonDetail,
)
from hypothesis_framework.scripts.statistical_tests.shapiro_wilk import shapiro_wilk_test
from hypothesis_framework.scripts.statistical_tests.kruskal_wallis import kruskal_wallis_test
from hypothesis_framework.scripts.statistical_tests.mann_whitney import mann_whitney_test
from hypothesis_framework.scripts.statistical_tests.vargha_delaney import vargha_delaney_a12


def _holm_bonferroni(p_values: List[float], alpha: float = 0.05) -> List[float]:
    """Apply Holm-Bonferroni correction to a list of p-values."""
    m = len(p_values)
    if m == 0:
        return []
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    adjusted = [0.0] * m
    cummax = 0.0
    for rank, (orig_idx, p) in enumerate(indexed):
        adj = p * (m - rank)
        adj = min(adj, 1.0)
        cummax = max(cummax, adj)
        adjusted[orig_idx] = cummax
    return adjusted


def _equal_weight_iqm(subfault_arrays: List[np.ndarray]) -> float:
    """Compute equal-weight average of per-sub-fault IQMs."""
    iqms = []
    for arr in subfault_arrays:
        if len(arr) >= 4:
            iqms.append(trim_mean(arr, 0.25))
        elif len(arr) > 0:
            iqms.append(float(np.mean(arr)))
    return float(np.mean(iqms)) if iqms else 0.0


def run_cross_category_test(
    data_per_category: Dict[str, Dict[str, List[float]]],
    metric_name: str = "time_to_detect",
    alpha: float = 0.05,
) -> H03Result:
    """Run H-03: Cross-Category Performance Comparison.

    Step 1: Build per-category pooled arrays and sub-fault breakdown.
    Step 2: Within-category heterogeneity check (KW among sub-faults).
    Step 3: Shapiro-Wilk normality on each pooled category (informational).
    Step 4: Kruskal-Wallis omnibus test across categories.
    Step 5: If significant → pairwise Mann-Whitney U + A12, Holm corrected.

    Args:
        data_per_category: {category: {sub_fault: [values]}}.
            Data should be detected-only values.
        metric_name: Name of the metric.
        alpha: Significance level.

    Returns:
        H03Result with omnibus test, pairwise comparisons, effect sizes,
        and per-category sub-fault breakdowns.
    """
    warnings: List[str] = []
    categories = list(data_per_category.keys())
    cat_details: List[CategoryComparisonDetail] = []
    pooled_groups: Dict[str, np.ndarray] = {}
    normality: Dict[str, bool] = {}

    # Step 1 & 2: Build per-category stats
    for cat in categories:
        subfaults = data_per_category[cat]
        sub_results: List[SubFaultComparisonDetail] = []
        subfault_arrays: List[np.ndarray] = []
        all_values: List[float] = []

        for fname, values in subfaults.items():
            arr = np.asarray(values, dtype=float)
            subfault_arrays.append(arr)
            all_values.extend(values)

            iqm_val = trim_mean(arr, 0.25) if len(arr) >= 4 else (
                float(np.mean(arr)) if len(arr) > 0 else 0.0
            )
            sub_results.append(SubFaultComparisonDetail(
                fault_name=fname,
                n=len(arr),
                iqm=round(iqm_val, 2),
                median=round(float(np.median(arr)), 2) if len(arr) > 0 else 0.0,
                mean=round(float(np.mean(arr)), 2) if len(arr) > 0 else 0.0,
                std=round(float(np.std(arr, ddof=1)), 2) if len(arr) > 1 else 0.0,
            ))

        pooled = np.asarray(all_values, dtype=float)
        pooled_groups[cat] = pooled
        n_total = len(pooled)

        # Pooled stats
        p_iqm = trim_mean(pooled, 0.25) if n_total >= 4 else (
            float(np.mean(pooled)) if n_total > 0 else 0.0
        )
        ew_iqm = _equal_weight_iqm(subfault_arrays)

        # Normality check (informational)
        is_normal = False
        if n_total >= 3:
            sw = shapiro_wilk_test(all_values, alpha=alpha)
            is_normal = sw.is_normal
        normality[cat] = is_normal

        # Within-category heterogeneity check
        within_het = False
        within_kw_p: Optional[float] = None
        non_empty_sfs = [a for a in subfault_arrays if len(a) >= 2]
        if len(non_empty_sfs) >= 2:
            kw_within = kruskal_wallis_test(*[a.tolist() for a in non_empty_sfs], alpha=alpha)
            within_kw_p = kw_within.p_value
            if within_kw_p is not None and within_kw_p < alpha:
                within_het = True
                warnings.append(
                    f"{cat}: sub-faults are heterogeneous (KW p={within_kw_p:.4f}). "
                    f"Pooled cross-category comparison should be interpreted with caution."
                )

        cat_details.append(CategoryComparisonDetail(
            category=cat,
            n=n_total,
            n_sub_faults=len(subfault_arrays),
            pooled_iqm=round(p_iqm, 2),
            pooled_median=round(float(np.median(pooled)), 2) if n_total > 0 else 0.0,
            pooled_mean=round(float(np.mean(pooled)), 2) if n_total > 0 else 0.0,
            pooled_std=round(float(np.std(pooled, ddof=1)), 2) if n_total > 1 else 0.0,
            equal_weight_iqm=round(ew_iqm, 2),
            is_normal=is_normal,
            within_heterogeneous=within_het,
            within_kw_p=round(within_kw_p, 6) if within_kw_p is not None else None,
            sub_faults=sub_results,
        ))

    # Need at least 2 categories
    groups = [pooled_groups[c] for c in categories]
    if len(groups) < 2:
        warnings.append("Need at least 2 categories for comparison.")
        return H03Result(
            metric_name=metric_name, alpha=alpha,
            per_category=cat_details,
            warnings=warnings, overall_assessment="insufficient_groups",
        )

    # Step 3: Omnibus — always Kruskal-Wallis (robust to non-normality)
    omnibus = kruskal_wallis_test(
        *[g.tolist() for g in groups], alpha=alpha
    )
    omnibus_stat = omnibus.statistic if omnibus.statistic is not None else 0.0
    omnibus_p = omnibus.p_value if omnibus.p_value is not None else 1.0
    omnibus_sig = omnibus_p < alpha

    # Step 4: Pairwise post-hoc (if omnibus significant)
    pairwise: List[PairwiseComparison] = []
    if omnibus_sig and len(categories) >= 2:
        pairs = list(combinations(range(len(categories)), 2))
        raw_ps: List[float] = []
        pair_results = []

        for i, j in pairs:
            mw = mann_whitney_test(
                groups[i].tolist(), groups[j].tolist(), alpha=alpha
            )
            a12 = vargha_delaney_a12(
                groups[i].tolist(), groups[j].tolist()
            )
            raw_ps.append(mw.p_value if mw.p_value is not None else 1.0)
            pair_results.append((i, j, mw, a12))

        adj_ps = _holm_bonferroni(raw_ps, alpha)

        for idx, (i, j, mw, a12) in enumerate(pair_results):
            pairwise.append(PairwiseComparison(
                pair=f"{categories[i]} vs {categories[j]}",
                u_statistic=mw.u_statistic,
                p_value_raw=mw.p_value if mw.p_value is not None else 1.0,
                p_value_adjusted=round(adj_ps[idx], 6),
                significant=adj_ps[idx] < alpha,
                a12=a12.a12,
                effect_magnitude=a12.magnitude,
            ))

    # Overall assessment
    if omnibus_sig:
        sig_pairs = [p for p in pairwise if p.significant]
        large_effects = [p for p in sig_pairs if p.effect_magnitude in ("medium", "large")]
        if large_effects:
            assessment = "significant_category_disparity"
        else:
            assessment = "significant_but_small_effect"
    else:
        assessment = "no_significant_difference"

    return H03Result(
        metric_name=metric_name,
        alpha=alpha,
        per_category=cat_details,
        normality_results=normality,
        test_used="kruskal_wallis",
        omnibus_statistic=round(omnibus_stat, 4),
        omnibus_p=round(omnibus_p, 6),
        omnibus_significant=omnibus_sig,
        pairwise=pairwise,
        overall_assessment=assessment,
        warnings=warnings,
    )
