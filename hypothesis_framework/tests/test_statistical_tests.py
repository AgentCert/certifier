"""Fresh unit tests for hypothesis_framework.scripts.statistical_tests.*

Pure-statistics functions. Expected values derived independently with
numpy/scipy/statsmodels; randomized methods (bootstrap) checked via
invariants (seeded reproducibility, bound ordering, CI contains estimate).
"""

import math

import numpy as np
import pytest

from hypothesis_framework.scripts.statistical_tests.bootstrap_bca import bootstrap_bca_ci
from hypothesis_framework.scripts.statistical_tests.chi_square_fisher import chi_square_fisher_test
from hypothesis_framework.scripts.statistical_tests.cusum_ewma import cusum_ewma
from hypothesis_framework.scripts.statistical_tests.cvar import cvar_analysis
from hypothesis_framework.scripts.statistical_tests.exact_binomial import exact_binomial_test
from hypothesis_framework.scripts.statistical_tests.iqm import interquartile_mean
from hypothesis_framework.scripts.statistical_tests.kaplan_meier import kaplan_meier_analysis
from hypothesis_framework.scripts.statistical_tests.kruskal_wallis import kruskal_wallis_test
from hypothesis_framework.scripts.statistical_tests.levene_cv import levene_cv_test
from hypothesis_framework.scripts.statistical_tests.mann_whitney import mann_whitney_test
from hypothesis_framework.scripts.statistical_tests.shapiro_wilk import shapiro_wilk_test
from hypothesis_framework.scripts.statistical_tests.tost import tost_test
from hypothesis_framework.scripts.statistical_tests.vargha_delaney import vargha_delaney_a12
from hypothesis_framework.scripts.statistical_tests.welch_anova import welch_anova
from hypothesis_framework.scripts.statistical_tests.wilcoxon_signed_rank import wilcoxon_signed_rank
from hypothesis_framework.scripts.statistical_tests.wilson_ci import wilson_ci


# ── Method 1: Wilson CI ──────────────────────────────────────────────────

class TestWilsonCI:
    def test_known_value_8_of_10(self):
        r = wilson_ci(8, 10)
        assert r.successes == 8
        assert r.trials == 10
        assert r.proportion == pytest.approx(0.8)
        # statsmodels wilson [0.490162, 0.943318]
        assert r.lower == pytest.approx(0.490162, abs=1e-5)
        assert r.upper == pytest.approx(0.943318, abs=1e-5)
        assert r.confidence_interval == (r.lower, r.upper)
        # n=10 is NOT < 10, so no small-sample warning here
        assert not any("Small sample" in w for w in r.warnings)

    def test_small_sample_warning(self):
        r = wilson_ci(4, 8)
        assert any("Small sample" in w for w in r.warnings)

    def test_larger_sample_no_small_warning(self):
        r = wilson_ci(50, 100)
        assert r.proportion == pytest.approx(0.5)
        assert r.lower == pytest.approx(0.403832, abs=1e-5)
        assert r.upper == pytest.approx(0.596168, abs=1e-5)
        assert not any("Small sample" in w for w in r.warnings)

    def test_zero_trials_degenerate(self):
        r = wilson_ci(0, 0)
        assert "Invalid input" in r.interpretation
        assert any("trials must be > 0" in w for w in r.warnings)

    def test_successes_clamped(self):
        r = wilson_ci(15, 10)
        assert r.successes == 10
        assert any("clamping" in w for w in r.warnings)

    def test_bounds_within_unit_interval(self):
        r = wilson_ci(1, 12)
        assert 0.0 <= r.lower <= r.proportion <= r.upper <= 1.0


# ── Method 2: Bootstrap BCa ──────────────────────────────────────────────

class TestBootstrapBCa:
    def test_too_small_sample(self):
        r = bootstrap_bca_ci([1.0, 2.0])
        assert r.ci_lower == r.ci_upper == r.observed_statistic
        assert r.ci_width == 0.0
        assert any("too small" in w for w in r.warnings)

    def test_empty_sample(self):
        r = bootstrap_bca_ci([])
        assert r.observed_statistic == 0.0
        assert r.ci_width == 0.0

    def test_ci_contains_observed_and_ordered(self):
        data = list(np.linspace(10.0, 20.0, 40))
        r = bootstrap_bca_ci(data, n_resamples=2000, random_state=42)
        assert r.observed_statistic == pytest.approx(np.mean(data), abs=1e-3)
        assert r.ci_lower <= r.observed_statistic <= r.ci_upper
        assert r.ci_width == pytest.approx(r.ci_upper - r.ci_lower, abs=1e-4)

    def test_seed_reproducible(self):
        data = [float(x) for x in range(5, 45)]
        r1 = bootstrap_bca_ci(data, n_resamples=1000, random_state=7)
        r2 = bootstrap_bca_ci(data, n_resamples=1000, random_state=7)
        assert r1.ci_lower == r2.ci_lower
        assert r1.ci_upper == r2.ci_upper

    def test_custom_statistic_fn(self):
        data = [float(x) for x in range(1, 30)]
        r = bootstrap_bca_ci(data, statistic_fn=np.median,
                             n_resamples=1000, random_state=1)
        assert r.observed_statistic == pytest.approx(np.median(data), abs=1e-3)


# ── Method 3: IQM ─────────────────────────────────────────────────────────

class TestIQM:
    def test_known_value(self):
        # 1..8, trim 25% -> mean of [3,4,5,6] = 4.5
        r = interquartile_mean([1, 2, 3, 4, 5, 6, 7, 8])
        assert r.iqm == pytest.approx(4.5)
        assert r.n_total == 8
        assert r.n_trimmed == 2 * int(np.floor(8 * 0.25))  # = 4

    def test_empty(self):
        r = interquartile_mean([])
        assert "No data" in r.interpretation
        assert any("Empty data" in w for w in r.warnings)

    def test_small_sample_falls_back_to_mean(self):
        r = interquartile_mean([2.0, 4.0, 6.0])
        assert r.iqm == pytest.approx(4.0)
        assert r.n_trimmed == 0
        assert any("no trimming" in r.interpretation.lower() for _ in [0])


# ── Method 4: Shapiro-Wilk ────────────────────────────────────────────────

class TestShapiroWilk:
    def test_normal_data(self):
        rng = np.random.default_rng(0)
        data = rng.normal(50, 5, 200).tolist()
        r = shapiro_wilk_test(data)
        assert r.is_normal is True
        assert r.n == 200
        assert r.p_value >= 0.05

    def test_clearly_non_normal(self):
        # Strongly skewed / bimodal-ish data
        data = [1, 1, 1, 1, 1, 1, 100, 100, 100, 100, 100, 100]
        r = shapiro_wilk_test(data)
        assert r.is_normal is False

    def test_too_few(self):
        r = shapiro_wilk_test([1, 2])
        assert r.is_normal is False
        assert any("at least 3" in w for w in r.warnings)


# ── Method 5: Kruskal-Wallis ──────────────────────────────────────────────

class TestKruskalWallis:
    def test_known_value(self):
        r = kruskal_wallis_test([1, 2, 3], [4, 5, 6], [7, 8, 9])
        assert r.statistic == pytest.approx(7.2, abs=1e-2)
        assert r.n_groups == 3
        assert r.significant is True  # p ~ 0.027

    def test_identical_groups_not_significant(self):
        r = kruskal_wallis_test([1, 2, 3, 4], [1, 2, 3, 4])
        assert r.significant is False

    def test_single_group_insufficient(self):
        r = kruskal_wallis_test([1, 2, 3])
        assert "Insufficient groups" in r.interpretation

    def test_empty_groups_filtered(self):
        r = kruskal_wallis_test([], [])
        assert "Insufficient non-empty" in r.interpretation


# ── Method 6: Mann-Whitney ────────────────────────────────────────────────

class TestMannWhitney:
    def test_known_separation(self):
        r = mann_whitney_test([1, 2, 3, 4, 5], [6, 7, 8, 9, 10])
        assert r.u_statistic == pytest.approx(0.0)
        assert r.significant is True

    def test_identical_not_significant(self):
        r = mann_whitney_test([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
        assert r.significant is False

    def test_insufficient(self):
        r = mann_whitney_test([], [1, 2])
        assert "Insufficient data" in r.interpretation

    def test_small_sample_warning(self):
        r = mann_whitney_test([1, 2], [3, 4])
        assert any("Small sample" in w for w in r.warnings)


# ── Method 7: Vargha-Delaney A12 ──────────────────────────────────────────

class TestVarghaDelaney:
    def test_a_less_than_b_gives_zero(self):
        r = vargha_delaney_a12([1, 2, 3, 4, 5], [6, 7, 8, 9, 10])
        assert r.a12 == pytest.approx(0.0)
        assert r.magnitude == "large"

    def test_identical_negligible(self):
        r = vargha_delaney_a12([1, 2, 3, 4], [1, 2, 3, 4])
        assert r.a12 == pytest.approx(0.5)
        assert r.magnitude == "negligible"

    def test_empty(self):
        r = vargha_delaney_a12([], [1])
        assert "Insufficient data" in r.interpretation

    def test_magnitude_thresholds(self):
        # diff just over 0.06 -> small ; construct a12=0.58
        # 7 of 10 a-vs-b wins approx via simple data
        r = vargha_delaney_a12([5, 6, 7], [1, 2, 3])
        # a entirely > b -> a12 = 1.0 large
        assert r.a12 == pytest.approx(1.0)
        assert r.magnitude == "large"


# ── Method 8: Welch ANOVA ─────────────────────────────────────────────────

class TestWelchAnova:
    def test_separated_groups_significant(self):
        r = welch_anova([1, 2, 3, 2, 1], [10, 11, 12, 11, 10], [20, 21, 22, 21, 20])
        assert r.significant is True
        assert r.n_groups == 3
        assert r.f_statistic > 0

    def test_identical_not_significant(self):
        r = welch_anova([1, 2, 3, 4], [1, 2, 3, 4])
        assert r.significant is False

    def test_insufficient_groups(self):
        r = welch_anova([1, 2, 3])
        assert "Insufficient groups" in r.interpretation

    def test_groups_too_small_filtered(self):
        r = welch_anova([1.0], [2.0])
        assert "Insufficient valid groups" in r.interpretation


# ── Method 9: Chi-Square / Fisher ─────────────────────────────────────────

class TestChiSquareFisher:
    def test_2x2_uses_fisher(self):
        r = chi_square_fisher_test([[27, 3], [15, 15]])
        assert r.test_used == "fisher_exact"
        assert r.significant is True  # strong association
        assert 0.0 <= r.p_value <= 1.0

    def test_rxc_uses_chi_square(self):
        # 3x2 with large counts -> chi-square
        r = chi_square_fisher_test([[50, 50], [50, 50], [50, 50]])
        assert r.test_used == "chi_square"
        assert r.significant is False  # perfectly uniform

    def test_invalid_shape(self):
        r = chi_square_fisher_test([[5]])
        assert r.test_used == "none"
        assert any("at least 2" in w for w in r.warnings)

    def test_zero_marginal(self):
        # 3x2 with a zero row -> chi-square undefined
        r = chi_square_fisher_test([[0, 0], [10, 5], [8, 7]])
        assert r.p_value == 1.0
        assert r.significant is False
        assert any("Zero marginal" in w for w in r.warnings)

    def test_low_expected_counts_warning(self):
        r = chi_square_fisher_test([[1, 1], [1, 1], [1, 1]])
        # small expected counts in RxC > 2x2
        assert any("Expected counts < 5" in w for w in r.warnings)


# ── Method 10: Levene + CV ────────────────────────────────────────────────

class TestLeveneCV:
    def test_equal_variances(self):
        r = levene_cv_test([10, 12, 14, 11, 13], [20, 22, 24, 21, 23],
                           labels=["a", "b"])
        assert r.variances_equal is True
        assert len(r.cv_per_group) == 2

    def test_cv_computation(self):
        # group with mean 10, sample std sqrt of variance
        vals = [8.0, 10.0, 12.0]
        cv_expected = float(np.std(vals, ddof=1) / 10.0)
        r = levene_cv_test(vals, [5.0, 6.0, 7.0])
        assert r.cv_per_group[0] == pytest.approx(round(cv_expected, 4))

    def test_insufficient_groups(self):
        r = levene_cv_test([1, 2, 3])
        assert "Insufficient groups" in r.interpretation

    def test_undefined_cv_for_singletons(self):
        r = levene_cv_test([5.0], [6.0])
        assert any("CV undefined" in w for w in r.warnings)
        assert "Insufficient valid groups" in r.interpretation

    def test_zero_mean_cv_inf(self):
        r = levene_cv_test([-1.0, 0.0, 1.0], [2.0, 3.0, 4.0])
        assert math.isinf(r.cv_per_group[0])


# ── Method 11: Wilcoxon Signed-Rank ───────────────────────────────────────

class TestWilcoxonSignedRank:
    def test_below_threshold_passes(self):
        # all values well below 100 -> meets SLA
        data = [10, 12, 14, 16, 18, 20, 22, 24]
        r = wilcoxon_signed_rank(data, threshold=100)
        assert r.meets_threshold is True
        assert r.median == pytest.approx(17.0)

    def test_above_threshold_fails(self):
        data = [110, 120, 130, 140, 150, 160, 170]
        r = wilcoxon_signed_rank(data, threshold=100)
        assert r.meets_threshold is False

    def test_empty(self):
        r = wilcoxon_signed_rank([], threshold=10)
        assert r.n == 0
        assert "No data" in r.interpretation

    def test_all_equal_threshold(self):
        r = wilcoxon_signed_rank([5.0, 5.0, 5.0], threshold=5.0)
        assert "All observations at threshold" in r.interpretation

    def test_small_sample_warning(self):
        r = wilcoxon_signed_rank([1, 2, 3], threshold=10)
        assert any("< 6" in w for w in r.warnings)


# ── Method 12: Exact Binomial ─────────────────────────────────────────────

class TestExactBinomial:
    def test_low_breach_rate_passes(self):
        r = exact_binomial_test(2, 40, target_rate=0.05)
        assert r.observed_rate == pytest.approx(0.05)
        assert r.p_value == pytest.approx(0.600936, abs=1e-5)
        assert r.meets_target is True

    def test_high_breach_rate_fails(self):
        r = exact_binomial_test(20, 40, target_rate=0.05)
        assert r.meets_target is False
        assert r.p_value < 0.05

    def test_zero_trials(self):
        r = exact_binomial_test(0, 0)
        assert "Invalid input" in r.interpretation

    def test_breaches_clamped(self):
        r = exact_binomial_test(50, 40)
        assert r.breaches == 40
        assert any("clamping" in w for w in r.warnings)

    def test_ci_ordering(self):
        r = exact_binomial_test(3, 50)
        assert r.ci_lower <= r.observed_rate <= r.ci_upper


# ── Method 13: TOST ───────────────────────────────────────────────────────

class TestTOST:
    def test_within_bounds_equivalent(self):
        # tight data around 50, bounds [0, 100]
        data = [48, 49, 50, 51, 52, 50, 49, 51]
        r = tost_test(data, low=0, high=100)
        assert r.equivalent is True
        assert r.mean == pytest.approx(50.0, abs=0.5)

    def test_outside_bounds_not_equivalent(self):
        data = [95, 96, 97, 98, 99, 100, 101]
        r = tost_test(data, low=0, high=50)
        assert r.equivalent is False

    def test_invalid_bounds(self):
        r = tost_test([1, 2, 3], low=10, high=5)
        assert "Invalid bounds" in r.interpretation

    def test_insufficient_data(self):
        r = tost_test([5.0], low=0, high=10)
        assert "Insufficient data" in r.interpretation

    def test_zero_variance_within(self):
        r = tost_test([5.0, 5.0, 5.0], low=0, high=10)
        assert r.equivalent is True
        assert any("Zero standard error" in w for w in r.warnings)

    def test_zero_variance_outside(self):
        r = tost_test([50.0, 50.0, 50.0], low=0, high=10)
        assert r.equivalent is False


# ── Method 14: CVaR ───────────────────────────────────────────────────────

class TestCVaR:
    def test_basic_var_cvar(self):
        data = list(range(1, 101))  # 1..100
        r = cvar_analysis(data, quantile=0.95)
        # 95th percentile of 1..100 == 95.05
        assert r.var == pytest.approx(np.percentile(data, 95), abs=1e-4)
        # CVaR = mean of tail above index ceil(100*0.95)=95 -> arr[95:] = 96..100
        assert r.cvar == pytest.approx(np.mean(list(range(96, 101))))
        assert r.n_tail == 5

    def test_empty(self):
        r = cvar_analysis([])
        assert "No data" in r.interpretation

    def test_sla_overshoot(self):
        data = [10, 20, 30, 40, 50, 200]
        r = cvar_analysis(data, sla_threshold=100)
        assert r.n_breaches == 1
        assert r.expected_overshoot == pytest.approx(100.0)  # 200-100

    def test_no_breaches(self):
        r = cvar_analysis([1, 2, 3, 4, 5], sla_threshold=100)
        assert r.n_breaches == 0
        assert r.expected_overshoot == 0.0

    def test_small_sample_warning(self):
        r = cvar_analysis([1, 2, 3])
        assert any("highly uncertain" in w for w in r.warnings)


# ── Method 15: Kaplan-Meier ───────────────────────────────────────────────

class TestKaplanMeier:
    def test_all_events(self):
        times = [10, 20, 30, 40, 50]
        events = [True] * 5
        r = kaplan_meier_analysis(times, events)
        assert r.n_events == 5
        assert r.n_censored == 0
        assert r.survival_table is not None

    def test_with_censoring_and_sla(self):
        times = [10, 20, 30, 40, 50]
        events = [True, True, False, True, False]
        r = kaplan_meier_analysis(times, events, sla_threshold=25)
        assert r.n_events == 3
        assert r.n_censored == 2
        assert r.survival_at_sla is not None
        assert 0.0 <= r.survival_at_sla <= 1.0

    def test_mismatched_lengths(self):
        r = kaplan_meier_analysis([1, 2, 3], [True, False])
        assert "Mismatched" in r.interpretation

    def test_empty(self):
        r = kaplan_meier_analysis([], [])
        assert "No data" in r.interpretation

    def test_no_events_warning(self):
        r = kaplan_meier_analysis([10, 20, 30], [False, False, False])
        assert r.n_events == 0
        assert any("No events observed" in w for w in r.warnings)


# ── Method 16: CUSUM / EWMA ───────────────────────────────────────────────

class TestCusumEwma:
    def test_stable_series_no_alarm(self):
        data = [50.0 + (i % 2) for i in range(40)]  # tight oscillation
        r = cusum_ewma(data)
        assert r.drift_detected is False
        assert len(r.cusum_values) == 40
        assert len(r.ewma_values) == 40

    def test_drift_triggers_alarm(self):
        # Step change upward partway through
        data = [10.0] * 20 + [100.0] * 20
        r = cusum_ewma(data, target=10.0, k=1.0, h=5.0)
        assert r.drift_detected is True
        assert r.cusum_alarm is True

    def test_insufficient_data(self):
        r = cusum_ewma([5.0])
        assert "Insufficient data" in r.interpretation

    def test_small_sample_warning(self):
        r = cusum_ewma([1.0, 2.0, 3.0, 4.0, 5.0])
        assert any("< 30" in w for w in r.warnings)

    def test_constant_series(self):
        # std=0 -> k=0, h=1, sigma=1 ; CUSUM stays 0
        r = cusum_ewma([5.0] * 35)
        assert r.cusum_final == pytest.approx(0.0)
        assert r.drift_detected is False
