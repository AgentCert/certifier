"""Tests for the higher-level H01-H09 hypothesis tests.

These build on the pure statistical methods. We feed deterministic
{category: {sub_fault: [values]}} structures and assert on result identity,
verdict logic, and key invariants.
"""

import numpy as np
import pytest

from hypothesis_framework.scripts.hypothesis_tests.h01_confidence_intervals import (
    run_confidence_interval_test,
)
from hypothesis_framework.scripts.hypothesis_tests.h02_success_rate_estimation import (
    run_success_rate_test,
)
from hypothesis_framework.scripts.hypothesis_tests.h03_cross_category_comparison import (
    _holm_bonferroni,
    run_cross_category_test,
)
from hypothesis_framework.scripts.hypothesis_tests.h04_success_rate_uniformity import (
    run_uniformity_test,
)
from hypothesis_framework.scripts.hypothesis_tests.h05_consistency_predictability import (
    _classify_cv,
    run_consistency_test,
)
from hypothesis_framework.scripts.hypothesis_tests.h06_sla_threshold_compliance import (
    run_sla_compliance_test,
)
from hypothesis_framework.scripts.hypothesis_tests.h07_sla_breach_rate import (
    run_breach_rate_test,
)
from hypothesis_framework.scripts.hypothesis_tests.h08_tail_risk_analysis import (
    run_tail_risk_test,
)
from hypothesis_framework.scripts.hypothesis_tests.h09_temporal_stability import (
    run_drift_test,
)


def _tight(center, n=12, spread=1.0, seed=0):
    rng = np.random.default_rng(seed)
    return list(center + rng.uniform(-spread, spread, n))


# ── H-01: Confidence Intervals ────────────────────────────────────────────

class TestH01:
    def test_basic_ci(self):
        data = {
            "network_fault": {"pod-delete": [10.0, 12.0, 11.0, 13.0, 10.5, 12.5]},
        }
        r = run_confidence_interval_test(data, n_resamples=500, random_state=1)
        assert r.hypothesis_id == "H-01"
        assert len(r.per_category) == 1
        cat = r.per_category[0]
        # CI brackets the point estimate
        assert cat.ci_lower <= cat.iqm <= cat.ci_upper
        assert cat.ci_width == pytest.approx(cat.ci_upper - cat.ci_lower, abs=1e-3)

    def test_small_category_excluded(self):
        data = {"c": {"f": [1.0, 2.0]}}  # n=2 < 5
        r = run_confidence_interval_test(data, n_resamples=200, random_state=1)
        assert r.per_category == []
        assert any("excluded" in w.lower() for w in r.warnings)

    def test_seed_reproducible(self):
        data = {"c": {"f": [float(x) for x in range(10, 30)]}}
        r1 = run_confidence_interval_test(data, n_resamples=500, random_state=99)
        r2 = run_confidence_interval_test(data, n_resamples=500, random_state=99)
        assert r1.per_category[0].ci_lower == r2.per_category[0].ci_lower


# ── H-02: Success Rate Estimation ─────────────────────────────────────────

class TestH02:
    def test_certified_floor(self):
        counts = {"network_fault": {"pod-delete": (18, 20), "pod-kill": (19, 20)}}
        r = run_success_rate_test(counts)
        assert r.hypothesis_id == "H-02"
        cat = r.per_category[0]
        assert cat.successes == 37
        assert cat.trials == 40
        # certified floor (Wilson lower) below the point rate
        assert 0.0 <= cat.certified_floor <= cat.rate

    def test_worst_subfault_lowest_rate(self):
        counts = {"c": {"good": (20, 20), "bad": (5, 20)}}
        r = run_success_rate_test(counts)
        assert r.per_category[0].worst_sub_fault == "bad"


# ── H-03: Cross-Category Comparison ───────────────────────────────────────

class TestH03:
    def test_holm_bonferroni_helper(self):
        adj = _holm_bonferroni([0.01, 0.04, 0.03], alpha=0.05)
        # monotone non-decreasing after sorting back, bounded to 1.0
        assert all(0.0 <= a <= 1.0 for a in adj)
        # smallest raw p (0.01) * 3 = 0.03
        assert adj[0] == pytest.approx(0.03, abs=1e-9)

    def test_holm_empty(self):
        assert _holm_bonferroni([]) == []

    def test_significant_disparity(self):
        data = {
            "fast": {"f": [10.0, 11.0, 12.0, 10.5, 11.5, 9.5, 10.2, 11.8]},
            "slow": {"g": [100.0, 110.0, 105.0, 108.0, 102.0, 109.0, 101.0, 107.0]},
        }
        r = run_cross_category_test(data)
        assert r.categories_tested == 2
        assert r.omnibus_significant is True
        assert r.overall_assessment in (
            "significant_category_disparity", "significant_but_small_effect")
        assert len(r.pairwise) == 1

    def test_insufficient_categories(self):
        data = {"only": {"f": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]}}
        r = run_cross_category_test(data)
        assert r.overall_assessment == "insufficient_groups"
        assert r.categories_tested == 0

    def test_no_difference(self):
        data = {
            "a": {"f": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]},
            "b": {"g": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]},
        }
        r = run_cross_category_test(data)
        assert r.omnibus_significant is False
        assert r.overall_assessment == "no_significant_difference"


# ── H-04: Success Rate Uniformity ─────────────────────────────────────────

class TestH04:
    def test_uniform_rates(self):
        counts = {
            "a": {"f": (50, 100)},
            "b": {"g": (50, 100)},
        }
        r = run_uniformity_test(counts)
        assert r.significant is False
        assert r.overall_assessment == "uniform_rates"

    def test_non_uniform_rates(self):
        counts = {
            "a": {"f": (95, 100)},
            "b": {"g": (10, 100)},
        }
        r = run_uniformity_test(counts)
        assert r.significant is True
        assert r.overall_assessment == "non_uniform_rates"
        assert r.weakest_category == "b"

    def test_insufficient_categories(self):
        counts = {"a": {"f": (10, 20)}}
        r = run_uniformity_test(counts)
        assert r.overall_assessment == "insufficient_groups"


# ── H-05: Consistency & Predictability ────────────────────────────────────

class TestH05:
    def test_classify_cv_thresholds(self):
        assert _classify_cv(0.10) == "stable"
        assert _classify_cv(0.20) == "moderate"
        assert _classify_cv(0.50) == "unstable"

    def test_consistent_categories(self):
        data = {
            "a": {"f": [100.0, 101.0, 99.0, 100.5, 99.5, 100.2]},
            "b": {"g": [200.0, 201.0, 199.0, 200.5, 199.5, 200.2]},
        }
        r = run_consistency_test(data)
        assert r.categories_tested == 2
        assert r.cv_flags["a"] == "stable"
        assert r.overall_assessment in ("consistent", "unequal_variance")

    def test_unstable_category_flagged(self):
        data = {
            "a": {"f": [1.0, 100.0, 2.0, 200.0, 3.0, 150.0]},  # huge CV
            "b": {"g": [50.0, 51.0, 49.0, 50.5, 49.5, 50.2]},
        }
        r = run_consistency_test(data)
        assert "a" in r.unstable_categories
        assert r.overall_assessment == "variance_instability_detected"

    def test_insufficient_categories(self):
        data = {"a": {"f": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]}}
        r = run_consistency_test(data)
        assert r.overall_assessment == "insufficient_groups"


# ── H-06: SLA Threshold Compliance ────────────────────────────────────────

class TestH06:
    def test_pass_when_below_sla(self):
        data = {"network_fault": {"pod-delete": [10, 12, 11, 13, 10, 12, 11, 9]}}
        sla = {"pod-delete": 100.0}
        r = run_sla_compliance_test(data, sla, n_resamples=500, random_state=1)
        cat = r.per_category[0]
        sf = cat.sub_faults[0]
        assert sf.verdict == "PASS"
        assert cat.verdict == "PASS"
        assert r.overall_assessment == "sla_compliant"

    def test_fail_when_above_sla(self):
        data = {"c": {"f": [110, 120, 130, 140, 150, 160, 170, 180]}}
        sla = {"f": 100.0}
        r = run_sla_compliance_test(data, sla, n_resamples=500, random_state=1)
        assert r.per_category[0].sub_faults[0].verdict == "FAIL"
        assert r.overall_assessment == "sla_non_compliant"

    def test_no_sla_defined(self):
        data = {"c": {"f": [10, 12, 11, 13, 10, 12, 11, 9]}}
        r = run_sla_compliance_test(data, {}, n_resamples=200, random_state=1)
        assert r.per_category[0].sub_faults[0].verdict == "NO_SLA_DEFINED"

    def test_insufficient_data(self):
        data = {"c": {"f": [10, 12, 11]}}  # n=3 < 6
        r = run_sla_compliance_test(data, {"f": 100.0}, n_resamples=200, random_state=1)
        assert r.per_category[0].sub_faults[0].verdict == "INSUFFICIENT_DATA"

    def test_no_data(self):
        data = {"c": {"f": []}}
        r = run_sla_compliance_test(data, {"f": 100.0}, n_resamples=200, random_state=1)
        assert r.per_category[0].sub_faults[0].verdict == "NO_DATA"


# ── H-07: SLA Breach Rate ─────────────────────────────────────────────────

class TestH07:
    def test_pass_low_breach(self):
        # all detected well below SLA -> 0 breaches
        data = {"c": {"f": [10.0] * 30}}
        sla = {"f": 100.0}
        r = run_breach_rate_test(data, sla, target_rate=0.05)
        sf = r.per_category[0].sub_faults[0]
        assert sf.breaches == 0
        assert sf.verdict == "PASS"

    def test_fail_high_breach(self):
        # mostly breaches (inf values count as breach)
        data = {"c": {"f": [float("inf")] * 28 + [10.0, 10.0]}}
        sla = {"f": 100.0}
        r = run_breach_rate_test(data, sla, target_rate=0.05)
        sf = r.per_category[0].sub_faults[0]
        assert sf.breaches == 28
        assert sf.verdict == "FAIL"

    def test_no_sla(self):
        data = {"c": {"f": [10.0] * 10}}
        r = run_breach_rate_test(data, {}, target_rate=0.05)
        assert r.per_category[0].sub_faults[0].verdict == "NO_SLA_DEFINED"

    def test_insufficient_data(self):
        data = {"c": {"f": [10.0, 20.0]}}  # n=2 < 5
        r = run_breach_rate_test(data, {"f": 100.0})
        assert r.per_category[0].sub_faults[0].verdict == "INSUFFICIENT_DATA"

    def test_no_data(self):
        data = {"c": {"f": []}}
        r = run_breach_rate_test(data, {"f": 100.0})
        assert r.per_category[0].sub_faults[0].verdict == "NO_DATA"


# ── H-08: Tail Risk ───────────────────────────────────────────────────────

class TestH08:
    def test_mild_risk_uniform(self):
        data = {"c": {"f": [10.0 + (i % 3) for i in range(30)]}}
        r = run_tail_risk_test(data)
        sf = r.per_category[0].sub_faults[0]
        assert sf.risk_level == "mild"
        assert r.overall_assessment == "acceptable_tail_risk"

    def test_significant_tail(self):
        # heavy tail: most small, a few enormous
        data = {"c": {"f": [10.0] * 25 + [1000.0] * 5}}
        r = run_tail_risk_test(data)
        sf = r.per_category[0].sub_faults[0]
        assert sf.risk_level == "significant"
        assert r.overall_assessment == "significant_tail_risk"

    def test_insufficient_data(self):
        data = {"c": {"f": [10.0, 11.0, 12.0]}}  # n=3 < 20
        r = run_tail_risk_test(data)
        sf = r.per_category[0].sub_faults[0]
        assert sf.risk_level == "INSUFFICIENT_DATA"
        assert r.overall_assessment == "insufficient_data"

    def test_overshoot_with_sla(self):
        data = {"c": {"f": [10.0] * 25 + [200.0] * 5}}
        r = run_tail_risk_test(data, sla_thresholds={"f": 100.0})
        sf = r.per_category[0].sub_faults[0]
        assert sf.n_breaches == 5
        assert sf.expected_overshoot == pytest.approx(100.0)


# ── H-09: Temporal Stability ──────────────────────────────────────────────

class TestH09:
    def test_stable_series(self):
        data = {"c": {"f": [50.0 + (i % 2) for i in range(30)]}}
        r = run_drift_test(data)
        sf = r.per_category[0].sub_faults[0]
        assert sf.drift_verdict == "STABLE"
        assert r.overall_assessment == "no_drift_detected"

    def test_drift_detected(self):
        data = {"c": {"f": [10.0] * 15 + [500.0] * 15}}
        r = run_drift_test(data, target=10.0)
        sf = r.per_category[0].sub_faults[0]
        assert sf.drift_verdict == "DRIFT_DETECTED"
        assert r.overall_assessment == "drift_detected"

    def test_low_power(self):
        data = {"c": {"f": [10.0, 11.0, 12.0]}}  # n=3 < 8
        r = run_drift_test(data)
        sf = r.per_category[0].sub_faults[0]
        assert sf.drift_verdict == "LOW_POWER"
        assert r.overall_assessment == "low_power"

    def test_timestamp_sorting(self):
        # reverse-chronological values, timestamps will reorder them
        data = {"c": {"f": [float(x) for x in range(10, 0, -1)] * 1}}
        ts = {"c": {"f": [f"2024-01-{i:02d}T00:00:00" for i in range(10, 0, -1)]}}
        r = run_drift_test(data, target=5.0, timestamps_per_category=ts)
        assert any("sorted chronologically" in w for w in r.warnings)
