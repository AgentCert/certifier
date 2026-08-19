"""Unit tests for main.services.pipeline_service.

Focuses on the deterministic, pure helper functions (config building, severity
normalisation, fault-category grouping, skip-block construction, the hypothesis
gate glue and GroupedDocsQueryService) plus the empty-findings patcher. The
heavy execute_pipeline orchestrations are exercised at the helper level; their
end-to-end paths require the full fault_analyzer/aggregator/cert_builder stack
and many real files, so they are covered via the worker tests with mocked
service objects instead (see test_workers_*).
"""
from pathlib import Path

import pytest

from main.services import pipeline_service as ps
from main.services.pipeline_service import (
    GroupedDocsQueryService,
    _build_fault_config_from_bucket,
    _build_skip_block,
    _doc_fault_category,
    _doc_fault_name,
    _group_docs_by_category,
    _load_fault_categories_config,
    _normalize_severity_labels,
    _patch_empty_findings,
    _run_hypothesis_with_gate,
)


# ── _build_fault_config_from_bucket ────────────────────────────────────────

class TestBuildFaultConfig:
    def test_top_level_ideal_fields_promoted(self):
        bucket = {
            "fault_id": "f1",
            "fault_name": "PodKill",
            "severity": "critical",
            "experiment_id": "e",
            "run_id": "r",
            "injection_timestamp": "2024-01-01T00:00:00Z",
            "target_pod": "svc",
            "namespace": "ns",
            "ideal_course_of_action": "do x",
            "ideal_tool_usage_trajectory": ["kubectl"],
            "agent_id": "a", "agent_name": "n", "agent_version": "v1",
        }
        cfg = _build_fault_config_from_bucket(bucket)
        assert cfg["fault_id"] == "f1"
        assert cfg["fault_category"] == "critical"  # severity → fault_category
        assert cfg["fault_configuration"]["target_service"] == "svc"
        assert cfg["fault_configuration"]["target_namespace"] == "ns"
        assert cfg["ground_truth"]["ideal_course_of_action"] == "do x"
        assert cfg["ground_truth"]["ideal_tool_usage_trajectory"] == ["kubectl"]
        assert cfg["agent"]["agent_id"] == "a"

    def test_defaults_for_missing(self):
        cfg = _build_fault_config_from_bucket({})
        assert cfg["fault_id"] == "unknown"
        assert cfg["fault_name"] == "unknown"
        assert cfg["fault_category"] == "unknown"
        assert cfg["ground_truth"] == {}

    def test_injection_timestamp_falls_back_to_detected_at(self):
        cfg = _build_fault_config_from_bucket({"detected_at": "2024-02-02T00:00:00Z"})
        assert cfg["injection_timestamp"] == "2024-02-02T00:00:00Z"

    def test_nested_ground_truth_preserved(self):
        cfg = _build_fault_config_from_bucket({"ground_truth": {"foo": "bar"}})
        assert cfg["ground_truth"]["foo"] == "bar"


# ── _normalize_severity_labels ─────────────────────────────────────────────

class TestNormalizeSeverity:
    def test_maps_known_label(self):
        sc = {"fault_category_scorecards": [
            {"textual_metrics": {"detection": {"severity_label": "Adequate"}}}
        ]}
        out = _normalize_severity_labels(sc)
        assert out["fault_category_scorecards"][0]["textual_metrics"]["detection"]["severity_label"] == "Moderate"

    def test_valid_label_untouched(self):
        sc = {"fault_category_scorecards": [
            {"textual": {"x": {"severity_label": "Strong"}}}
        ]}
        out = _normalize_severity_labels(sc)
        assert out["fault_category_scorecards"][0]["textual"]["x"]["severity_label"] == "Strong"

    def test_unknown_label_defaults_to_moderate(self):
        sc = {"fault_category_scorecards": [
            {"textual_metrics": {"x": {"severity_label": "Wibble"}}}
        ]}
        out = _normalize_severity_labels(sc)
        assert out["fault_category_scorecards"][0]["textual_metrics"]["x"]["severity_label"] == "Moderate"

    def test_no_scorecards(self):
        assert _normalize_severity_labels({}) == {}


# ── _doc_fault_name / _doc_fault_category ──────────────────────────────────

class TestDocFaultHelpers:
    def test_fault_name_top_level(self):
        assert _doc_fault_name({"fault_name": "X"}) == "X"

    def test_fault_name_nested_quantitative(self):
        assert _doc_fault_name({"quantitative": {"injected_fault_name": "Y"}}) == "Y"

    def test_fault_name_none(self):
        assert _doc_fault_name({"quantitative": {}}) is None

    def test_category_in_config(self):
        mapping = {"PodKill": "resource"}
        assert _doc_fault_category({"fault_name": "PodKill"}, mapping) == "resource"

    def test_category_not_in_config_returns_none(self):
        assert _doc_fault_category({"fault_name": "Unknown"}, {"PodKill": "x"}) is None


# ── _group_docs_by_category ────────────────────────────────────────────────

class TestGroupDocs:
    def test_groups_and_routes_unmapped_to_unclassified(self):
        docs = [
            {"fault_name": "PodKill"},
            {"fault_name": "NetLoss"},
            {"fault_name": "Unmapped"},
        ]
        cats = {"resource": ["PodKill"], "network": ["NetLoss"]}
        grouped = _group_docs_by_category(docs, cats)
        # Unmapped docs (e.g. Phase 0 bucketing that only produced a generic
        # fault_name) are routed to "unclassified" rather than dropped, so
        # they still count toward the certification instead of silently
        # vanishing (and potentially leaving zero categories).
        assert set(grouped.keys()) == {"resource", "network", "unclassified"}
        assert len(grouped["resource"]) == 1
        assert len(grouped["unclassified"]) == 1
        assert grouped["unclassified"][0]["fault_name"] == "Unmapped"

    def test_empty(self):
        assert _group_docs_by_category([], {}) == {}


# ── GroupedDocsQueryService ────────────────────────────────────────────────

class TestGroupedDocsQueryService:
    def test_query_methods(self):
        grouped = {"a": [{"x": 1}], "b": [{"y": 2}, {"y": 3}]}
        svc = GroupedDocsQueryService(grouped)
        assert svc.get_all_fault_categories() == ["a", "b"]  # sorted
        assert svc.query_runs_by_fault_category("b") == [{"y": 2}, {"y": 3}]
        assert svc.query_runs_by_fault_category("missing") == []
        all_docs = svc.query_runs_by_agent("ignored")
        assert len(all_docs) == 3


# ── _load_fault_categories_config ──────────────────────────────────────────

class TestLoadFaultCategoriesConfig:
    def test_missing_returns_empty(self, tmp_path):
        assert _load_fault_categories_config(tmp_path / "nope.json") == {}

    def test_categories_key_schema(self, tmp_path):
        p = tmp_path / "fc.json"
        p.write_text('{"categories": {"resource": ["PodKill", "OOM"]}}')
        out = _load_fault_categories_config(p)
        assert out == {"resource": ["PodKill", "OOM"]}

    def test_flat_schema(self, tmp_path):
        p = tmp_path / "fc.json"
        p.write_text('{"network": ["Loss"], "ignored_scalar": 5}')
        out = _load_fault_categories_config(p)
        assert out == {"network": ["Loss"]}

    def test_invalid_json_raises_orchestrator_error(self, tmp_path):
        p = tmp_path / "fc.json"
        p.write_text("{not json")
        from utils.custom_errors import OrchestratorError
        with pytest.raises(OrchestratorError):
            _load_fault_categories_config(p)


# ── _build_skip_block ──────────────────────────────────────────────────────

class TestBuildSkipBlock:
    def test_basic(self):
        block = _build_skip_block("insufficient_runs", "too few", min_required=30)
        assert block["status"] == "skipped"
        assert block["reason"] == "insufficient_runs"
        assert block["min_required"] == 30
        assert block["observed_per_category"] == {}
        assert block["total_runs"] == 0

    def test_with_validation_dict_counts(self):
        validation = {
            "per_category": {"resource": {"total": 10}, "network": 5},
            "total_runs": 15,
        }
        block = _build_skip_block("x", "m", min_required=30, validation=validation)
        assert block["observed_per_category"] == {"resource": 10, "network": 5}
        assert block["total_runs"] == 15


# ── _patch_empty_findings ──────────────────────────────────────────────────

class TestPatchEmptyFindings:
    def test_header_findings_filled(self):
        report = {"header": {"findings": []}, "sections": []}
        out = _patch_empty_findings(report)
        assert out["header"]["findings"] == [ps._PLACEHOLDER_FINDING]

    def test_section_findings_block_filled(self):
        report = {
            "header": {"findings": [{"x": 1}]},
            "sections": [{"content": [{"type": "findings", "items": []}]}],
        }
        out = _patch_empty_findings(report)
        assert out["sections"][0]["content"][0]["items"] == [ps._PLACEHOLDER_FINDING]

    def test_non_empty_left_alone(self):
        report = {
            "header": {"findings": [{"x": 1}]},
            "sections": [{"content": [{"type": "findings", "items": [{"a": 1}]}]}],
        }
        out = _patch_empty_findings(report)
        assert out["sections"][0]["content"][0]["items"] == [{"a": 1}]


# ── _run_hypothesis_with_gate ──────────────────────────────────────────────

class TestRunHypothesisWithGate:
    def test_gate_fails_returns_skip(self, monkeypatch, tmp_path):
        # Patch the framework's validator to report failure.
        import hypothesis_framework.scripts.utils as hf_utils

        def fake_validate(grouped, min_runs):
            return False, {"message": "not enough", "total_runs": 2,
                           "per_category": {"a": 2}}

        monkeypatch.setattr(hf_utils, "validate_min_total_runs", fake_validate)
        out = _run_hypothesis_with_gate(
            {"a": [{}]}, None, min_runs=30, alpha=0.05,
            n_resamples=10, random_state=1, metrics_dir=tmp_path,
        )
        assert out["status"] == "skipped"
        assert out["reason"] == "insufficient_runs"

    def test_gate_passes_runs_hypothesis(self, monkeypatch, tmp_path):
        import hypothesis_framework.scripts.utils as hf_utils
        import hypothesis_framework.scripts.run_statistical_hypothesis as hf_run

        monkeypatch.setattr(
            hf_utils, "validate_min_total_runs",
            lambda grouped, min_runs: (True, {"total_runs": 60,
                                              "per_category": {"a": 60}}),
        )
        monkeypatch.setattr(
            hf_run, "run_all_hypothesis_tests_from_runs",
            lambda **kwargs: {"H01": "pass"},
        )
        out = _run_hypothesis_with_gate(
            {"a": [{}]}, None, min_runs=30, alpha=0.05,
            n_resamples=10, random_state=1, metrics_dir=tmp_path,
        )
        assert out["status"] == "ok"
        assert out["results"] == {"H01": "pass"}
        assert out["observed_per_category"] == {"a": 60}
        assert out["ground_truth_provided"] is False

    def test_hypothesis_raises_returns_skip(self, monkeypatch, tmp_path):
        import hypothesis_framework.scripts.utils as hf_utils
        import hypothesis_framework.scripts.run_statistical_hypothesis as hf_run

        monkeypatch.setattr(
            hf_utils, "validate_min_total_runs",
            lambda grouped, min_runs: (True, {"total_runs": 60,
                                              "per_category": {"a": 60}}),
        )

        def boom(**kwargs):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(hf_run, "run_all_hypothesis_tests_from_runs", boom)
        out = _run_hypothesis_with_gate(
            {"a": [{}]}, None, min_runs=30, alpha=0.05,
            n_resamples=10, random_state=1, metrics_dir=tmp_path,
        )
        assert out["status"] == "skipped"
        assert out["reason"] == "hypothesis_error"

    def test_hypothesis_error_result_returns_skip(self, monkeypatch, tmp_path):
        import hypothesis_framework.scripts.utils as hf_utils
        import hypothesis_framework.scripts.run_statistical_hypothesis as hf_run

        monkeypatch.setattr(
            hf_utils, "validate_min_total_runs",
            lambda grouped, min_runs: (True, {"total_runs": 60,
                                              "per_category": {"a": 60}}),
        )
        monkeypatch.setattr(
            hf_run, "run_all_hypothesis_tests_from_runs",
            lambda **kwargs: {"error": "bad_data", "message": "oops"},
        )
        out = _run_hypothesis_with_gate(
            {"a": [{}]}, None, min_runs=30, alpha=0.05,
            n_resamples=10, random_state=1, metrics_dir=tmp_path,
        )
        assert out["status"] == "skipped"
        assert out["reason"] == "bad_data"
