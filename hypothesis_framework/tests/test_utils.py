"""Tests for hypothesis_framework.scripts.utils (data loading, builders, filters)."""

import json
import math

import pytest

from hypothesis_framework.scripts.utils import (
    _category_label,
    build_subfault_counts,
    build_subfault_counts_from_status,
    build_subfault_data,
    build_subfault_data_all,
    build_subfault_timestamps,
    filter_categories_by_min_sample_size,
    filter_categories_by_min_sample_size_counts,
    load_runs,
    load_sla_thresholds,
    validate_min_total_runs,
)


# ── _category_label ───────────────────────────────────────────────────────

class TestCategoryLabel:
    def test_known_mappings(self):
        assert _category_label("application_fault") == "Application"
        assert _category_label("network_fault") == "Network"
        assert _category_label("resource_fault") == "Resource"

    def test_fallback_titlecase(self):
        assert _category_label("some_other_fault") == "Some Other Fault"


# ── load_runs ─────────────────────────────────────────────────────────────

class TestLoadRuns:
    def test_loads_category_subdirs(self, tmp_path):
        cat = tmp_path / "network_fault"
        cat.mkdir()
        (cat / "run1.json").write_text(json.dumps({"run_id": "r1"}))
        (cat / "run2.json").write_text(json.dumps({"run_id": "r2"}))
        runs = load_runs(tmp_path)
        assert "network_fault" in runs
        assert len(runs["network_fault"]) == 2
        # _source_file injected
        assert all("_source_file" in r for r in runs["network_fault"])

    def test_skips_underscore_and_dot_dirs(self, tmp_path):
        skip = tmp_path / "_internal"
        skip.mkdir()
        (skip / "x.json").write_text("{}")
        runs = load_runs(tmp_path)
        assert "_internal" not in runs

    def test_bad_json_skipped(self, tmp_path):
        cat = tmp_path / "resource_fault"
        cat.mkdir()
        (cat / "good.json").write_text(json.dumps({"run_id": "g"}))
        (cat / "bad.json").write_text("{not valid json")
        runs = load_runs(tmp_path)
        assert len(runs["resource_fault"]) == 1


# ── validate_min_total_runs ───────────────────────────────────────────────

class TestValidateMinTotalRuns:
    def test_passes_when_enough(self):
        all_runs = {
            "network_fault": [{"run_id": f"r{i}"} for i in range(5)],
        }
        passed, details = validate_min_total_runs(all_runs, min_runs=5)
        assert passed is True
        assert details["per_category"]["network_fault"] == 5
        assert details["total_runs"] == 5

    def test_fails_when_short(self):
        all_runs = {"network_fault": [{"run_id": "r1"}, {"run_id": "r2"}]}
        passed, details = validate_min_total_runs(all_runs, min_runs=5)
        assert passed is False
        assert details["failed_categories"]
        assert "need 5" in details["failed_categories"][0]

    def test_distinct_run_ids_counted_once(self):
        # Two docs share a run_id -> counts as one run
        all_runs = {
            "resource_fault": [
                {"run_id": "shared"},
                {"run_id": "shared"},
                {"run_id": "other"},
            ]
        }
        passed, details = validate_min_total_runs(all_runs, min_runs=1)
        assert details["per_category"]["resource_fault"] == 2

    def test_missing_run_id_falls_back_to_source_file(self):
        all_runs = {
            "network_fault": [
                {"_source_file": "a.json"},
                {"_source_file": "b.json"},
            ]
        }
        passed, details = validate_min_total_runs(all_runs, min_runs=1)
        assert details["per_category"]["network_fault"] == 2


# ── build_subfault_data ───────────────────────────────────────────────────

class TestBuildSubfaultData:
    def test_groups_by_fault_name(self):
        all_runs = {
            "network_fault": [
                {"fault_name": "pod-delete", "quantitative": {"time_to_detect": 10}},
                {"fault_name": "pod-delete", "quantitative": {"time_to_detect": 20}},
                {"fault_name": "pod-kill", "quantitative": {"time_to_detect": 5}},
            ]
        }
        out = build_subfault_data(all_runs, "time_to_detect")
        assert out["network_fault"]["pod-delete"] == [10.0, 20.0]
        assert out["network_fault"]["pod-kill"] == [5.0]

    def test_skips_none_values(self):
        all_runs = {
            "c": [
                {"fault_name": "f", "quantitative": {"time_to_detect": None}},
                {"fault_name": "f", "quantitative": {"time_to_detect": 7}},
            ]
        }
        out = build_subfault_data(all_runs, "time_to_detect")
        assert out["c"]["f"] == [7.0]

    def test_filter_field_gates(self):
        all_runs = {
            "c": [
                {"fault_name": "f", "quantitative": {"time_to_detect": 10,
                                                      "agent_fault_detection_time": None}},
                {"fault_name": "f", "quantitative": {"time_to_detect": 20,
                                                      "agent_fault_detection_time": "t"}},
            ]
        }
        out = build_subfault_data(all_runs, "time_to_detect",
                                  filter_field="agent_fault_detection_time")
        assert out["c"]["f"] == [20.0]

    def test_non_numeric_skipped(self):
        all_runs = {"c": [{"fault_name": "f",
                           "quantitative": {"time_to_detect": "abc"}}]}
        out = build_subfault_data(all_runs, "time_to_detect")
        assert out == {}

    def test_qualitative_section(self):
        all_runs = {"c": [{"fault_name": "f",
                           "qualitative": {"reasoning_quality_score": 4.0}}]}
        out = build_subfault_data(all_runs, "reasoning_quality_score",
                                  section="qualitative")
        assert out["c"]["f"] == [4.0]


# ── build_subfault_data_all ───────────────────────────────────────────────

class TestBuildSubfaultDataAll:
    def test_missing_value_becomes_inf(self):
        all_runs = {
            "c": [
                {"fault_name": "f", "quantitative": {"time_to_detect": 10}},
                {"fault_name": "f", "quantitative": {"time_to_detect": None}},
            ]
        }
        out = build_subfault_data_all(all_runs, "time_to_detect")
        vals = out["c"]["f"]
        assert vals[0] == 10.0
        assert math.isinf(vals[1])

    def test_filter_field_failure_becomes_inf(self):
        all_runs = {
            "c": [
                {"fault_name": "f", "quantitative": {"time_to_detect": 10,
                                                     "gate": None}},
            ]
        }
        out = build_subfault_data_all(all_runs, "time_to_detect",
                                      filter_field="gate")
        assert math.isinf(out["c"]["f"][0])


# ── build_subfault_timestamps ─────────────────────────────────────────────

class TestBuildSubfaultTimestamps:
    def test_extracts_timestamps(self):
        all_runs = {
            "c": [
                {"fault_name": "f", "quantitative": {
                    "time_to_detect": 10, "fault_injection_time": "2024-01-01T00:00:00"}},
            ]
        }
        out = build_subfault_timestamps(all_runs, "time_to_detect")
        assert out["c"]["f"] == ["2024-01-01T00:00:00"]

    def test_skips_missing_timestamp(self):
        all_runs = {
            "c": [
                {"fault_name": "f", "quantitative": {"time_to_detect": 10}},
            ]
        }
        out = build_subfault_timestamps(all_runs, "time_to_detect")
        assert out == {}


# ── build_subfault_counts ─────────────────────────────────────────────────

class TestBuildSubfaultCounts:
    def test_presence_based_success(self):
        all_runs = {
            "c": [
                {"fault_name": "f", "quantitative": {"agent_fault_detection_time": "t"}},
                {"fault_name": "f", "quantitative": {"agent_fault_detection_time": None}},
                {"fault_name": "f", "quantitative": {"agent_fault_detection_time": "t2"}},
            ]
        }
        out = build_subfault_counts(all_runs, "agent_fault_detection_time")
        assert out["c"]["f"] == (2, 3)


# ── build_subfault_counts_from_status ─────────────────────────────────────

class TestBuildSubfaultCountsFromStatus:
    def test_string_success_value(self):
        all_runs = {
            "c": [
                {"fault_name": "f", "qualitative": {"rai_check_status": "Passed"}},
                {"fault_name": "f", "qualitative": {"rai_check_status": "Failed"}},
            ]
        }
        out = build_subfault_counts_from_status(all_runs, "rai_check_status", "Passed")
        assert out["c"]["f"] == (1, 2)

    def test_list_success_values(self):
        all_runs = {
            "c": [
                {"fault_name": "f", "qualitative": {"s": "Compliant"}},
                {"fault_name": "f", "qualitative": {"s": "Partial"}},
                {"fault_name": "f", "qualitative": {"s": "No"}},
            ]
        }
        out = build_subfault_counts_from_status(
            all_runs, "s", ["Compliant", "Partial"])
        assert out["c"]["f"] == (2, 3)


# ── filter_categories_by_min_sample_size ──────────────────────────────────

class TestFilterBySampleSize:
    def test_excludes_small_categories(self):
        data = {
            "big": {"f": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]},
            "small": {"f": [1.0, 2.0]},
        }
        filtered, excluded = filter_categories_by_min_sample_size(data, min_n=5)
        assert "big" in filtered
        assert "small" not in filtered
        assert any("small" in e.lower() or "Small" in e for e in excluded) or excluded

    def test_excludes_zeros_and_nans(self):
        data = {"c": {"f": [0.0, 0.0, float("nan"), 1.0]}}
        filtered, excluded = filter_categories_by_min_sample_size(data, min_n=2)
        # only 1 valid value (1.0) -> excluded
        assert "c" not in filtered
        assert excluded


# ── filter_categories_by_min_sample_size_counts ───────────────────────────

class TestFilterCountsBySampleSize:
    def test_filters_on_successes(self):
        data = {
            "good": {"f": (10, 20)},
            "bad": {"f": (1, 20)},
        }
        filtered, excluded = filter_categories_by_min_sample_size_counts(data, min_n=5)
        assert "good" in filtered
        assert "bad" not in filtered
        assert excluded


# ── load_sla_thresholds ───────────────────────────────────────────────────

class TestLoadSLAThresholds:
    def test_missing_dir_returns_empty(self, tmp_path):
        out = load_sla_thresholds(tmp_path / "nope", "time_to_detect")
        assert out == {}

    def test_yaml_structure(self, tmp_path):
        fault_dir = tmp_path / "pod-delete"
        fault_dir.mkdir()
        (fault_dir / "ground_truth.yaml").write_text(
            "ground_truth:\n  sla:\n    time_to_detect:\n      threshold: 120\n"
        )
        out = load_sla_thresholds(tmp_path, "time_to_detect")
        assert out["pod-delete"] == pytest.approx(120.0)

    def test_json_flat_structure(self, tmp_path):
        (tmp_path / "exp1_pod-kill_ground_truth.json").write_text(json.dumps({
            "fault_name": "pod-kill",
            "ground_truth": {"sla": {"time_to_detect": {"threshold": 90}}},
        }))
        out = load_sla_thresholds(tmp_path, "time_to_detect")
        assert out["pod-kill"] == pytest.approx(90.0)
