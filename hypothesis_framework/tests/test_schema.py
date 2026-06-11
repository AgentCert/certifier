"""Tests for the Pydantic result schemas (construction / defaults / serialization)."""

from hypothesis_framework.schema.hypothesis_results import (
    CategoryCIResult,
    H01Result,
    H03Result,
    H06Result,
    PairwiseComparison,
    SubFaultCIResult,
)
from hypothesis_framework.schema.test_results import (
    BootstrapBCaResult,
    CVaRResult,
    StatisticalTestResult,
    WilsonCIResult,
)


# ── test_results.py models ────────────────────────────────────────────────

class TestStatisticalTestResultBase:
    def test_defaults(self):
        r = StatisticalTestResult(method_name="x")
        assert r.alpha == 0.05
        assert r.statistic is None
        assert r.p_value is None
        assert r.confidence_interval is None
        assert r.warnings == []

    def test_roundtrip_serialization(self):
        r = StatisticalTestResult(method_name="x", statistic=1.5, p_value=0.04)
        dumped = r.model_dump()
        assert dumped["statistic"] == 1.5
        restored = StatisticalTestResult(**dumped)
        assert restored == r


class TestWilsonCIResult:
    def test_default_method_name(self):
        r = WilsonCIResult()
        assert r.method_name == "wilson_ci"
        assert r.successes == 0
        assert r.proportion == 0.0

    def test_fields_and_dump(self):
        r = WilsonCIResult(successes=8, trials=10, proportion=0.8,
                           lower=0.49, upper=0.94,
                           confidence_interval=(0.49, 0.94))
        d = r.model_dump()
        assert d["successes"] == 8
        assert d["confidence_interval"] == (0.49, 0.94)


class TestBootstrapBCaResult:
    def test_defaults(self):
        r = BootstrapBCaResult()
        assert r.method_name == "bootstrap_bca"
        assert r.n_resamples == 10000
        assert r.random_state is None


class TestCVaRResult:
    def test_optional_overshoot_none(self):
        r = CVaRResult(var=10.0, cvar=12.0, n_tail=5)
        assert r.expected_overshoot is None
        assert r.n_breaches is None
        assert r.quantile_level == 0.95


# ── hypothesis_results.py models ──────────────────────────────────────────

class TestH01Result:
    def test_fixed_identity_fields(self):
        r = H01Result()
        assert r.hypothesis_id == "H-01"
        assert "Confidence Intervals" in r.hypothesis_name
        assert r.per_category == []

    def test_nested_category_and_subfault(self):
        sf = SubFaultCIResult(fault_name="pod-delete", n=10, iqm=12.0,
                              median=11.0, mean=12.5, p95=20.0)
        cat = CategoryCIResult(category="network_fault", n=10, n_sub_faults=1,
                               iqm=12.0, sub_faults=[sf])
        r = H01Result(per_category=[cat])
        d = r.model_dump()
        assert d["per_category"][0]["category"] == "network_fault"
        assert d["per_category"][0]["sub_faults"][0]["fault_name"] == "pod-delete"
        # default aggregation method preserved
        assert cat.aggregation_method == "equal_weight_subfault_iqm"


class TestH03Result:
    def test_pairwise_nesting(self):
        pc = PairwiseComparison(pair="a vs b", u_statistic=5.0,
                                p_value_raw=0.01, p_value_adjusted=0.02,
                                significant=True, a12=0.7,
                                effect_magnitude="large")
        r = H03Result(pairwise=[pc], omnibus_significant=True)
        assert r.test_used == "kruskal_wallis"
        assert r.pairwise[0].significant is True

    def test_defaults(self):
        r = H03Result()
        assert r.correction_method == "holm_bonferroni"
        assert r.omnibus_p == 1.0


class TestH06Result:
    def test_sla_thresholds_dict(self):
        r = H06Result(sla_thresholds={"pod-delete": 120.0})
        assert r.sla_thresholds["pod-delete"] == 120.0
        assert r.hypothesis_id == "H-06"
