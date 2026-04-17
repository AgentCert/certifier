"""
H-06: SLA Threshold Compliance.

Primary: Wilcoxon signed-rank per category.
Supplementary: Bootstrap CI vs SLA, TOST equivalence, Kaplan-Meier (if censored).

Verdict logic:
  - PASS: Wilcoxon p < alpha AND CI upper ≤ SLA
  - CONDITIONAL: CI contains SLA threshold
  - FAIL: CI lower > SLA or Wilcoxon not significant and median > SLA
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

import numpy as np
from scipy.stats import trim_mean

from hypothesis_framework.schema.hypothesis_results import (
    CategorySLAResult,
    H06Result,
)
from hypothesis_framework.scripts.statistical_tests.wilcoxon_signed_rank import wilcoxon_signed_rank
from hypothesis_framework.scripts.statistical_tests.bootstrap_bca import bootstrap_bca_ci
from hypothesis_framework.scripts.statistical_tests.tost import tost_test
from hypothesis_framework.scripts.statistical_tests.kaplan_meier import kaplan_meier_analysis


def run_sla_compliance_test(
    data_per_category: Dict[str, List[float]],
    sla_threshold: float,
    metric_name: str = "time_to_detect",
    alpha: float = 0.05,
    n_resamples: int = 10000,
    random_state: Optional[int] = None,
    censored_per_category: Optional[Dict[str, List[float]]] = None,
) -> H06Result:
    """Run H-06: SLA Threshold Compliance.

    Args:
        data_per_category: {category: [detected_values]}.
        sla_threshold: SLA upper bound (e.g., 300s for TTD).
        metric_name: Name of the metric.
        alpha: Significance level.
        n_resamples: Bootstrap resamples.
        random_state: Seed for reproducibility.
        censored_per_category: Optional {category: [censored_times]} for KM.

    Returns:
        H06Result with per-category verdicts.
    """
    warnings: List[str] = []
    per_cat: List[CategorySLAResult] = []

    iqm_fn: Callable = lambda x: trim_mean(x, 0.25)

    for cat, values in data_per_category.items():
        arr = np.asarray(values, dtype=float)
        n = len(arr)
        if n == 0:
            warnings.append(f"{cat}: no data.")
            per_cat.append(CategorySLAResult(category=cat, verdict="NO_DATA"))
            continue

        median_val = float(np.median(arr))

        # Primary: Wilcoxon signed-rank
        wil = wilcoxon_signed_rank(values, threshold=sla_threshold, alpha=alpha)

        # Supplementary: Bootstrap CI on IQM
        boot = bootstrap_bca_ci(
            values, statistic_fn=iqm_fn,
            n_resamples=n_resamples, alpha=alpha,
            random_state=random_state,
        )

        # Supplementary: TOST equivalence
        tost_r = tost_test(values, low=0, high=sla_threshold, alpha=alpha)

        # Supplementary: Kaplan-Meier (only if censored data available)
        km_survival: Optional[float] = None
        if censored_per_category and cat in censored_per_category:
            cens = censored_per_category[cat]
            all_times = list(values) + list(cens)
            events = [True] * len(values) + [False] * len(cens)
            km = kaplan_meier_analysis(all_times, events, sla_threshold=sla_threshold)
            km_survival = km.survival_at_sla

        # Verdict logic
        ci_upper = boot.ci_upper
        if wil.meets_threshold and ci_upper <= sla_threshold:
            verdict = "PASS"
        elif median_val > sla_threshold and not wil.meets_threshold:
            verdict = "FAIL"
        else:
            verdict = "CONDITIONAL"

        per_cat.append(CategorySLAResult(
            category=cat,
            n=n,
            median=round(median_val, 2),
            wilcoxon_p=wil.p_value,
            ci_upper=boot.ci_upper,
            tost_equivalent=tost_r.equivalent,
            tost_p=tost_r.p_value,
            km_survival_at_sla=km_survival,
            verdict=verdict,
        ))

    verdicts = [c.verdict for c in per_cat]
    if all(v == "PASS" for v in verdicts):
        overall = "sla_compliant"
    elif any(v == "FAIL" for v in verdicts):
        overall = "sla_non_compliant"
    else:
        overall = "conditional_compliance"

    return H06Result(
        metric_name=metric_name,
        alpha=alpha,
        sla_threshold=sla_threshold,
        per_category=per_cat,
        overall_assessment=overall,
        warnings=warnings,
    )
