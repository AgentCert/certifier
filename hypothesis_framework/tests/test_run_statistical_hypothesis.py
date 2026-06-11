"""Tests for the orchestrator run_statistical_hypothesis.

Drives run_all_hypothesis_tests_from_runs end-to-end with in-memory runs
(no disk/network) and exercises the _safe_run wrapper and error branches.
"""

import json

import pytest

from hypothesis_framework.scripts.run_statistical_hypothesis import (
    _import_hypothesis_tests,
    _safe_run,
    run_all_hypothesis_tests,
    run_all_hypothesis_tests_from_runs,
)


def _make_run(fault_name, ttd, idx, *, detected=True, rai="Passed"):
    """Build a single run dict matching the per-run metrics schema."""
    q = {
        "fault_detected": "Yes" if detected else "No",
        "fault_injection_time": f"2024-01-01T00:00:{idx:02d}",
    }
    if detected:
        q["time_to_detect"] = ttd
        q["agent_fault_detection_time"] = "2024-01-01T00:01:00"
        q["time_to_mitigate"] = ttd + 5
        q["agent_fault_mitigation_time"] = "2024-01-01T00:02:00"
    return {
        "run_id": f"{fault_name}-{idx}",
        "fault_name": fault_name,
        "quantitative": q,
        "qualitative": {
            "reasoning_quality_score": 4.0,
            "hallucination_score": 1.0,
            "rai_check_status": rai,
            "security_compliance_status": "Compliant",
        },
    }


def _make_all_runs(per_category=12):
    """Two categories, each with enough distinct runs to pass min_runs."""
    runs = {}
    for cat, fname in [("network_fault", "pod-delete"), ("resource_fault", "pod-cpu-hog")]:
        runs[cat] = [
            _make_run(fname, 10 + (i % 5), i)
            for i in range(per_category)
        ]
    return runs


class TestSafeRun:
    def test_success_returns_model_dump(self):
        from hypothesis_framework.schema.test_results import WilsonCIResult

        def fn():
            return WilsonCIResult(successes=1, trials=2)

        out = _safe_run(fn, "label")
        assert out["successes"] == 1
        assert out["method_name"] == "wilson_ci"

    def test_exception_returns_error_dict(self):
        def boom():
            raise ValueError("nope")

        out = _safe_run(boom, "label")
        assert out["status"] == "error"
        assert "ValueError" in out["error"]


class TestImportHypothesisTests:
    def test_all_nine_present(self):
        tests = _import_hypothesis_tests()
        assert set(tests.keys()) == {f"h0{i}" for i in range(1, 10)}
        assert all(callable(v) for v in tests.values())


class TestRunAllFromRuns:
    def test_no_runs(self):
        out = run_all_hypothesis_tests_from_runs({}, gt_dir="/nonexistent")
        assert out["error"] == "no_data"

    def test_min_runs_not_met(self, tmp_path):
        runs = _make_all_runs(per_category=3)
        out = run_all_hypothesis_tests_from_runs(runs, gt_dir=tmp_path, min_runs=30)
        assert out["error"] == "minimum_run_criteria_not_qualified"
        assert "validation" in out

    def test_full_run_structure(self, tmp_path):
        # Provide an SLA ground-truth JSON so H06/H07 actually run
        (tmp_path / "exp_pod-delete_ground_truth.json").write_text(json.dumps({
            "fault_name": "pod-delete",
            "ground_truth": {"sla": {"time_to_detect": {"threshold": 100},
                                     "time_to_mitigate": {"threshold": 100}}},
        }))
        runs = _make_all_runs(per_category=12)
        out = run_all_hypothesis_tests_from_runs(
            runs, gt_dir=tmp_path, min_runs=10, n_resamples=300, random_state=1)
        assert "results" in out
        assert "metadata" in out
        assert "validation" in out
        # all 9 hypothesis keys present
        assert set(out["results"].keys()) == {f"h0{i}" for i in range(1, 10)}
        # H01 ran for time_to_detect and produced a real H01 result
        h01_ttd = out["results"]["h01"]["time_to_detect"]
        assert h01_ttd.get("hypothesis_id") == "H-01"
        # H02 success rate produced a result
        assert "fault_detection_success_rate" in out["results"]["h02"]
        # metadata reflects detected runs
        assert out["metadata"]["detected_runs"] == 24

    def test_sla_skipped_when_no_gt(self, tmp_path):
        runs = _make_all_runs(per_category=12)
        out = run_all_hypothesis_tests_from_runs(
            runs, gt_dir=tmp_path, min_runs=10, n_resamples=300, random_state=1)
        # No ground-truth files -> H06/H07 skipped for time_to_detect
        h06 = out["results"]["h06"]["time_to_detect"]
        assert h06.get("status") == "skipped"
        assert h06.get("reason") == "no_sla_thresholds_available"


class TestRunAllFromDisk:
    def test_reads_from_directory(self, tmp_path):
        data_dir = tmp_path / "input"
        gt_dir = tmp_path / "gt"
        gt_dir.mkdir()
        cat_dir = data_dir / "network_fault"
        cat_dir.mkdir(parents=True)
        for i in range(12):
            run = _make_run("pod-delete", 10 + (i % 5), i)
            (cat_dir / f"run{i:02d}.json").write_text(json.dumps(run))
        out = run_all_hypothesis_tests(
            data_dir, gt_dir, min_runs=10, n_resamples=200, random_state=1)
        assert "results" in out
        assert out["metadata"]["total_runs"] == 12

    def test_empty_dir_returns_no_data(self, tmp_path):
        data_dir = tmp_path / "empty"
        data_dir.mkdir()
        out = run_all_hypothesis_tests(data_dir, tmp_path / "gt")
        assert out["error"] == "no_data"
