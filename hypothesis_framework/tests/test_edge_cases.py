"""Supplementary edge-case tests to cover remaining branches in source."""

import numpy as np

from hypothesis_framework.scripts.statistical_tests.vargha_delaney import vargha_delaney_a12
from hypothesis_framework.scripts.hypothesis_tests.h04_success_rate_uniformity import (
    run_uniformity_test,
)
from hypothesis_framework.scripts.hypothesis_tests.h08_tail_risk_analysis import (
    run_tail_risk_test,
)
from hypothesis_framework.scripts.hypothesis_tests.h09_temporal_stability import (
    run_drift_test,
)
from hypothesis_framework.scripts.utils import build_subfault_timestamps


# ── Vargha-Delaney magnitude bands (small / medium) ───────────────────────

class TestVarghaMagnitudeBands:
    def test_small_magnitude(self):
        # a12 = 0.571 -> diff 0.071 -> small (0.06 <= diff < 0.14)
        r = vargha_delaney_a12([5, 6, 7, 8, 9, 10, 11], [1, 2, 3, 4, 12, 13, 14])
        assert r.magnitude == "small"

    def test_medium_magnitude(self):
        # a12 = 0.66 -> diff 0.16 -> medium (0.14 <= diff < 0.21)
        r = vargha_delaney_a12([3, 4, 5, 6, 7], [4, 4, 3, 8, 2])
        assert r.magnitude == "medium"


# ── H-04 within-category heterogeneity branch ─────────────────────────────

class TestH04Heterogeneity:
    def test_within_category_heterogeneous_flag(self):
        # Two sub-faults with very different rates within category "a"
        counts = {
            "a": {"good": (49, 50), "bad": (5, 50)},
            "b": {"f": (30, 60)},
        }
        r = run_uniformity_test(counts)
        det_a = next(c for c in r.per_category if c.category == "a")
        assert det_a.within_heterogeneous is True
        assert det_a.within_p is not None


# ── H-08 no-data sub-fault -> unknown ─────────────────────────────────────

class TestH08NoData:
    def test_empty_subfault_unknown(self):
        data = {"c": {"f": []}}
        r = run_tail_risk_test(data)
        sf = r.per_category[0].sub_faults[0]
        assert sf.risk_level == "unknown"
        assert r.per_category[0].risk_level == "unknown"


# ── H-09 timestamp count mismatch branch ──────────────────────────────────

class TestH09TimestampMismatch:
    def test_timestamp_count_mismatch_warns(self):
        data = {"c": {"f": [float(x) for x in range(10)]}}
        ts = {"c": {"f": ["2024-01-01T00:00:00"]}}  # wrong length
        r = run_drift_test(data, target=5.0, timestamps_per_category=ts)
        assert any("timestamp count" in w for w in r.warnings)


# ── utils: build_subfault_timestamps filter / non-string skip ─────────────

class TestTimestampBuilderEdges:
    def test_filter_field_skips(self):
        all_runs = {
            "c": [
                {"fault_name": "f", "quantitative": {
                    "time_to_detect": 10, "gate": None,
                    "fault_injection_time": "2024-01-01T00:00:00"}},
            ]
        }
        out = build_subfault_timestamps(all_runs, "time_to_detect", filter_field="gate")
        assert out == {}

    def test_non_string_timestamp_skipped(self):
        all_runs = {
            "c": [
                {"fault_name": "f", "quantitative": {
                    "time_to_detect": 10, "fault_injection_time": 12345}},
            ]
        }
        out = build_subfault_timestamps(all_runs, "time_to_detect")
        assert out == {}
