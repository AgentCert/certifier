"""
Unit tests for aggregator.scripts.aggregation (Phase-2 orchestrator).

MongoDB and the LLM Council are mocked; the filesystem backend uses
tmp_path. NO network/DB. Covers:
  * config helpers                    — _get_config / _get_collection_name
  * doc field extractors              — agent_id/name/experiment/category/run_id
  * _enrich_fault_categories          — two-pass inference
  * _distinct_run_ids
  * DirectoryQueryService             — file-based query interface (storage-agnostic)
  * MetricsQueryService               — Mongo-backed query interface (mocked db)
  * PipelineTokenTracker              — phase accumulation + report
  * _calculate_phase_0_1_tokens       — dedup-by-run + sum
  * ScorecardAssembler                — category + final assembly
  * ScorecardStorage                  — upsert logic (mocked collection)
  * _validate_metrics_across_categories
  * AggregationOrchestrator           — aggregate_fault_category + aggregate_all (mocked council)
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from aggregator.scripts import aggregation as ag
from aggregator.scripts.aggregation import (
    MetricsQueryService,
    DirectoryQueryService,
    PipelineTokenTracker,
    ScorecardAssembler,
    ScorecardStorage,
    AggregationOrchestrator,
    _calculate_phase_0_1_tokens,
    _distinct_run_ids,
    _enrich_fault_categories,
    _extract_agent_id,
    _extract_agent_name,
    _extract_experiment_id,
    _extract_fault_category,
    _extract_fault_name,
    _extract_run_id,
    _validate_metrics_across_categories,
)
from utils.custom_errors import AggregatorError


# ---------------------------------------------------------------------------
# config helpers
# ---------------------------------------------------------------------------

class TestConfigHelpers:
    def test_get_config_loads_and_caches(self):
        cfg = ag._get_config()
        assert "llm_council" in cfg
        # cached: same object returned
        assert ag._get_config() is cfg

    def test_get_collection_name_default(self):
        assert ag._get_collection_name() == "aggregated_scorecards"


# ---------------------------------------------------------------------------
# field extractors
# ---------------------------------------------------------------------------

class TestExtractors:
    def test_top_level_precedence(self):
        doc = {"agent_id": "top", "quantitative": {"agent_id": "nested"}}
        assert _extract_agent_id(doc) == "top"

    def test_nested_fallback(self):
        doc = {"quantitative": {"agent_id": "nested"}}
        assert _extract_agent_id(doc) == "nested"

    def test_agent_name(self):
        assert _extract_agent_name({"quantitative": {"agent_name": "n"}}) == "n"

    def test_experiment_id(self):
        assert _extract_experiment_id({"experiment_id": "e1"}) == "e1"

    def test_fault_category_nested_key(self):
        doc = {"quantitative": {"injected_fault_category": "network"}}
        assert _extract_fault_category(doc) == "network"

    def test_fault_name_defaults_empty_string(self):
        assert _extract_fault_name({}) == ""
        assert _extract_fault_name({"fault_name": "pod-delete"}) == "pod-delete"

    def test_run_id_nested(self):
        assert _extract_run_id({"quantitative": {"run_id": "r1"}}) == "r1"


class TestDistinctRunIds:
    def test_dedups_and_drops_empty(self):
        docs = [
            {"run_id": "r1"},
            {"run_id": "r1"},
            {"quantitative": {"run_id": "r2"}},
            {"run_id": None},
            {},
        ]
        assert _distinct_run_ids(docs) == {"r1", "r2"}


class TestEnrichFaultCategories:
    def test_infers_missing_category_from_same_fault_name(self):
        docs = [
            {"fault_name": "pod-network-loss", "fault_category": "network"},
            {"fault_name": "pod-network-loss", "fault_category": "network"},
            {"fault_name": "pod-network-loss"},  # missing → inferred "network"
        ]
        out = _enrich_fault_categories(docs)
        assert _extract_fault_category(out[2]) == "network"

    def test_leaves_doc_unchanged_when_no_match(self):
        docs = [{"fault_name": "unknown-fault"}]
        out = _enrich_fault_categories(docs)
        assert _extract_fault_category(out[0]) is None

    def test_does_not_mutate_original_doc(self):
        original = {"fault_name": "f", "other": 1}
        docs = [
            {"fault_name": "f", "fault_category": "c"},
            original,
        ]
        out = _enrich_fault_categories(docs)
        assert "fault_category" not in original  # original untouched
        assert out[1]["fault_category"] == "c"


# ---------------------------------------------------------------------------
# DirectoryQueryService
# ---------------------------------------------------------------------------

def _write_metrics(tmp_path, name, payload):
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


class TestDirectoryQueryService:
    def test_missing_directory_raises(self, tmp_path):
        with pytest.raises(AggregatorError):
            DirectoryQueryService(str(tmp_path / "nope"))

    def test_loads_single_dict_and_list_files(self, tmp_path):
        _write_metrics(tmp_path, "a_metrics.json", {"run_id": "r1", "fault_category": "net", "agent_id": "a1"})
        _write_metrics(tmp_path, "b_metrics.json", [
            {"run_id": "r2", "fault_category": "net", "agent_id": "a1"},
            {"run_id": "r3", "fault_category": "cpu", "agent_id": "a2"},
        ])
        svc = DirectoryQueryService(str(tmp_path))
        all_docs = svc._load_all_docs()
        assert len(all_docs) == 3
        # caching returns same list object
        assert svc._load_all_docs() is all_docs

    def test_skips_malformed_json(self, tmp_path):
        good = tmp_path / "good_metrics.json"
        good.write_text(json.dumps({"run_id": "r1", "fault_category": "net"}), encoding="utf-8")
        bad = tmp_path / "bad_metrics.json"
        bad.write_text("{not valid json", encoding="utf-8")
        svc = DirectoryQueryService(str(tmp_path))
        assert len(svc._load_all_docs()) == 1

    def test_query_by_agent(self, tmp_path):
        _write_metrics(tmp_path, "a_metrics.json", [
            {"run_id": "r1", "fault_category": "net", "agent_id": "a1"},
            {"run_id": "r2", "fault_category": "net", "agent_id": "a2"},
        ])
        svc = DirectoryQueryService(str(tmp_path))
        assert len(svc.query_runs_by_agent("a1")) == 1

    def test_query_by_fault_category_with_agent_filter(self, tmp_path):
        _write_metrics(tmp_path, "a_metrics.json", [
            {"run_id": "r1", "fault_category": "net", "agent_id": "a1"},
            {"run_id": "r2", "fault_category": "net", "agent_id": "a2"},
            {"run_id": "r3", "fault_category": "cpu", "agent_id": "a1"},
        ])
        svc = DirectoryQueryService(str(tmp_path))
        out = svc.query_runs_by_fault_category("net", agent_id="a1")
        assert len(out) == 1
        assert out[0]["run_id"] == "r1"

    def test_get_all_fault_categories_sorted(self, tmp_path):
        _write_metrics(tmp_path, "a_metrics.json", [
            {"run_id": "r1", "fault_category": "net"},
            {"run_id": "r2", "fault_category": "cpu"},
            {"run_id": "r3", "fault_category": "net"},
        ])
        svc = DirectoryQueryService(str(tmp_path))
        assert svc.get_all_fault_categories() == ["cpu", "net"]

    def test_recursive_glob_subdir(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        _write_metrics(sub, "x_metrics.json", {"run_id": "r1", "fault_category": "net"})
        svc = DirectoryQueryService(str(tmp_path))
        assert len(svc._load_all_docs()) == 1


# ---------------------------------------------------------------------------
# MetricsQueryService (mocked Mongo)
# ---------------------------------------------------------------------------

class TestMetricsQueryService:
    def test_query_by_agent_delegates(self):
        db = MagicMock()
        db.find_by_agent_id.return_value = [{"run_id": "r1"}]
        svc = MetricsQueryService(db)
        out = svc.query_runs_by_agent("a1")
        assert out == [{"run_id": "r1"}]
        db.find_by_agent_id.assert_called_once_with("a1")

    def test_query_by_agent_wraps_errors(self):
        db = MagicMock()
        db.find_by_agent_id.side_effect = ValueError("db down")
        svc = MetricsQueryService(db)
        with pytest.raises(AggregatorError):
            svc.query_runs_by_agent("a1")

    def test_query_by_fault_category(self):
        db = MagicMock()
        db.config.metrics_collection = "metrics"
        collection = MagicMock()
        collection.find.return_value = [{"run_id": "r1"}]
        db.sync_db = {"metrics": collection}
        svc = MetricsQueryService(db)
        out = svc.query_runs_by_fault_category("net", agent_id="a1")
        assert out == [{"run_id": "r1"}]
        collection.find.assert_called_once_with({"fault_category": "net", "agent_id": "a1"})

    def test_get_all_fault_categories_filters_none(self):
        db = MagicMock()
        db.config.metrics_collection = "metrics"
        collection = MagicMock()
        collection.distinct.return_value = ["net", None, "cpu"]
        db.sync_db = {"metrics": collection}
        svc = MetricsQueryService(db)
        assert svc.get_all_fault_categories() == ["net", "cpu"]


# ---------------------------------------------------------------------------
# PipelineTokenTracker
# ---------------------------------------------------------------------------

class TestPipelineTokenTracker:
    def test_phase_2_accumulates(self):
        t = PipelineTokenTracker()
        t.add_phase_2_tokens({"input_tokens": 1, "output_tokens": 2, "total_tokens": 3})
        t.add_phase_2_tokens({"input_tokens": 10, "output_tokens": 20, "total_tokens": 30})
        assert t.phase_2_tokens == {"input_tokens": 11, "output_tokens": 22, "total_tokens": 33}

    def test_set_phases_and_build_report_totals(self):
        t = PipelineTokenTracker()
        t.set_phase_0_tokens({"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
        t.set_phase_1_tokens({"input_tokens": 2, "output_tokens": 2, "total_tokens": 4})
        t.add_phase_2_tokens({"input_tokens": 3, "output_tokens": 3, "total_tokens": 6})
        t.set_phase_3_tokens({"input_tokens": 4, "output_tokens": 4, "total_tokens": 8})
        report = t.build_report()
        assert report["totals"]["input_tokens"] == 10
        assert report["totals"]["output_tokens"] == 10
        assert report["totals"]["total_tokens"] == 20
        assert report["phase_2_aggregator"]["total_tokens"] == 6

    def test_empty_tokens_ignored(self):
        t = PipelineTokenTracker()
        t.set_phase_0_tokens({})
        t.add_phase_2_tokens({})
        assert t.phase_0_tokens == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


class TestCalculatePhase01Tokens:
    def test_dedup_phase0_by_run_sum_phase1(self):
        docs = [
            {
                "run_id": "r1",
                "phase_0_tokens": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
                "token_usage": {"input_tokens": 10, "output_tokens": 5},
            },
            {
                # same run → phase_0 NOT double counted
                "run_id": "r1",
                "phase_0_tokens": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
                "token_usage": {"input_tokens": 20, "output_tokens": 10},
            },
        ]
        p0, p1 = _calculate_phase_0_1_tokens(docs)
        assert p0 == {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        # phase 1 summed across both docs
        assert p1 == {"input_tokens": 30, "output_tokens": 15, "total_tokens": 45}

    def test_missing_token_usage_skipped(self):
        docs = [{"run_id": "r1", "token_usage": {}}]
        p0, p1 = _calculate_phase_0_1_tokens(docs)
        assert p1 == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


# ---------------------------------------------------------------------------
# ScorecardAssembler
# ---------------------------------------------------------------------------

class TestScorecardAssembler:
    def test_assemble_category_distinct_runs(self):
        docs = [
            {"run_id": "r1", "fault_name": "pod-delete"},
            {"run_id": "r1", "fault_name": "pod-kill"},  # same run, different fault
            {"run_id": "r2", "fault_name": "pod-delete"},
        ]
        sc = ScorecardAssembler.assemble_category_scorecard(
            fault_category="net",
            docs=docs,
            numeric_aggs={"m": {}},
            derived_rates={"r": 0.5},
            boolean_aggs={"b": True},
            textual_aggs={"t": "x"},
        )
        assert sc["fault_category"] == "net"
        assert sc["faults_tested"] == ["pod-delete", "pod-kill"]
        assert sc["distinct_runs"] == 2
        assert sc["successful_runs"] == 2
        assert sc["total_runs"] == 2
        assert sc["failed_runs"] == 0
        assert sc["fault_evaluations"] == 3
        assert sc["numeric_metrics"] == {"m": {}}

    def test_assemble_category_no_run_ids_falls_back_to_doc_count(self):
        docs = [{"fault_name": "f"}, {"fault_name": "f"}]
        sc = ScorecardAssembler.assemble_category_scorecard(
            "net", docs, {}, {}, {}, {}
        )
        # no run_ids → distinct_runs falls back to fault_evaluations (2)
        assert sc["distinct_runs"] == 2
        assert sc["successful_runs"] == 2

    def test_assemble_final_defaults_sum_per_category(self):
        cats = [
            {"faults_tested": ["a"], "successful_runs": 5},
            {"faults_tested": ["b", "a"], "successful_runs": 3},
        ]
        final = ScorecardAssembler.assemble_final_scorecard(
            cats, agent_id="a1", agent_name="n1", certification_run_id="c1"
        )
        assert final["agent_id"] == "a1"
        assert final["total_successful_runs"] == 8
        assert final["total_runs"] == 8  # input defaults to successful
        assert final["total_failed_runs"] == 0
        assert final["total_faults_tested"] == 2  # union {a, b}
        assert final["total_fault_categories"] == 2
        assert "created_at" in final

    def test_assemble_final_explicit_input_runs_yields_failed(self):
        cats = [{"faults_tested": [], "successful_runs": 7}]
        final = ScorecardAssembler.assemble_final_scorecard(
            cats, total_input_runs=10, total_successful_runs=7
        )
        assert final["total_runs"] == 10
        assert final["total_successful_runs"] == 7
        assert final["total_failed_runs"] == 3


# ---------------------------------------------------------------------------
# ScorecardStorage
# ---------------------------------------------------------------------------

class TestScorecardStorage:
    def test_store_insert_returns_upserted_id(self):
        db = MagicMock()
        collection = MagicMock()
        result = MagicMock()
        result.upserted_id = "objid123"
        collection.replace_one.return_value = result
        db.sync_db = {"aggregated_scorecards": collection}
        storage = ScorecardStorage(db)
        doc_id = storage.store({"certification_run_id": "c1", "agent_id": "a1"})
        assert doc_id == "objid123"
        collection.replace_one.assert_called_once()
        args = collection.replace_one.call_args
        assert args[0][0] == {"certification_run_id": "c1"}
        assert args[1]["upsert"] is True

    def test_store_update_returns_cert_run_id(self):
        db = MagicMock()
        collection = MagicMock()
        result = MagicMock()
        result.upserted_id = None
        collection.replace_one.return_value = result
        db.sync_db = {"aggregated_scorecards": collection}
        storage = ScorecardStorage(db)
        doc_id = storage.store({"certification_run_id": "c1"})
        assert doc_id == "c1"

    def test_store_filters_by_agent_when_no_cert_run_id(self):
        db = MagicMock()
        collection = MagicMock()
        result = MagicMock()
        result.upserted_id = None
        collection.replace_one.return_value = result
        db.sync_db = {"aggregated_scorecards": collection}
        storage = ScorecardStorage(db)
        storage.store({"agent_id": "a1"})
        assert collection.replace_one.call_args[0][0] == {"agent_id": "a1"}

    def test_store_wraps_errors(self):
        db = MagicMock()
        collection = MagicMock()
        collection.replace_one.side_effect = ValueError("write fail")
        db.sync_db = {"aggregated_scorecards": collection}
        storage = ScorecardStorage(db)
        with pytest.raises(AggregatorError):
            storage.store({"certification_run_id": "c1"})


# ---------------------------------------------------------------------------
# _validate_metrics_across_categories
# ---------------------------------------------------------------------------

class _FakeQuery:
    def __init__(self, categories, docs_by_cat):
        self._categories = categories
        self._docs = docs_by_cat

    def get_all_fault_categories(self, agent_id=None):
        return self._categories

    def query_runs_by_fault_category(self, category, agent_id=None):
        return self._docs.get(category, [])


class TestValidateMetrics:
    def test_no_categories_is_failure(self):
        svc = _FakeQuery([], {})
        assert _validate_metrics_across_categories(svc) is True

    def test_all_null_is_failure(self):
        svc = _FakeQuery(
            ["net"],
            {"net": [{"quantitative": {"time_to_detect": None}, "qualitative": {"agent_summary": None}}]},
        )
        assert _validate_metrics_across_categories(svc) is True

    def test_one_quantitative_value_passes(self):
        svc = _FakeQuery(
            ["net"],
            {"net": [{"quantitative": {"time_to_detect": 12.0}, "qualitative": {}}]},
        )
        assert _validate_metrics_across_categories(svc) is False

    def test_one_qualitative_value_passes(self):
        svc = _FakeQuery(
            ["net"],
            {"net": [{"quantitative": {}, "qualitative": {"agent_summary": "ok"}}]},
        )
        assert _validate_metrics_across_categories(svc) is False

    def test_empty_collections_treated_as_null(self):
        svc = _FakeQuery(
            ["net"],
            {"net": [{"quantitative": {"trajectory_steps": []}, "qualitative": {"agent_summary": {}}}]},
        )
        assert _validate_metrics_across_categories(svc) is True


# ---------------------------------------------------------------------------
# AggregationOrchestrator
# ---------------------------------------------------------------------------

def _make_orchestrator(query_service, db_client=None):
    llm_client = MagicMock()
    llm_client.config = {"gpt-4o": {"deployment_name": "dep", "api_version": "v1"}}
    orch = AggregationOrchestrator(
        llm_client=llm_client,
        query_service=query_service,
        db_client=db_client,
    )
    # Replace council methods with AsyncMocks (no LLM/network)
    orch.council.compute_textual_aggregates = AsyncMock(
        return_value=({"agent_summary": {"consensus_summary": "s"}},
                      {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
    )
    orch.council.synthesize_limitations_and_recommendations = AsyncMock(
        return_value=({"known_limitations": {}, "recommendations": {}},
                      {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
    )
    return orch


class TestAggregationOrchestrator:
    async def test_aggregate_fault_category_empty_docs(self):
        svc = _FakeQuery(["net"], {"net": []})
        orch = _make_orchestrator(svc)
        out = await orch.aggregate_fault_category("net")
        assert out["fault_category"] == "net"
        assert out["total_runs"] == 0
        assert out["numeric_metrics"] == {}
        # No LLM call when no docs
        orch.council.compute_textual_aggregates.assert_not_awaited()

    async def test_aggregate_fault_category_full(self):
        docs = [
            {"run_id": "r1", "fault_name": "pod-delete",
             "quantitative": {"time_to_detect": 10.0},
             "qualitative": {"agent_summary": "ok"}},
            {"run_id": "r2", "fault_name": "pod-delete",
             "quantitative": {"time_to_detect": 12.0},
             "qualitative": {"agent_summary": "ok"}},
        ]
        svc = _FakeQuery(["net"], {"net": docs})
        orch = _make_orchestrator(svc)
        out = await orch.aggregate_fault_category("net")
        assert out["fault_category"] == "net"
        assert out["successful_runs"] == 2
        assert out["faults_tested"] == ["pod-delete"]
        # textual + synthesis merged
        assert "agent_summary" in out["textual_metrics"]
        assert "known_limitations" in out["textual_metrics"]
        orch.council.compute_textual_aggregates.assert_awaited_once()

    async def test_aggregate_fault_category_token_tracking(self):
        docs = [{"run_id": "r1", "fault_name": "f",
                 "quantitative": {"time_to_detect": 1.0}, "qualitative": {}}]
        svc = _FakeQuery(["net"], {"net": docs})
        orch = _make_orchestrator(svc)
        tracker = PipelineTokenTracker()
        await orch.aggregate_fault_category("net", token_tracker=tracker)
        # textual(2) + synthesis(2) = 4 total tokens
        assert tracker.phase_2_tokens["total_tokens"] == 4

    async def test_aggregate_all_validation_failed_skips_aggregation(self):
        # All-null docs → validation fails → minimal structures, no council calls
        docs = [{"run_id": "r1", "fault_name": "f",
                 "quantitative": {"time_to_detect": None},
                 "qualitative": {"agent_summary": None}}]
        svc = _FakeQuery(["net"], {"net": docs})
        orch = _make_orchestrator(svc, db_client=None)
        out = await orch.aggregate_all(
            agent_id="a1", agent_name="n1", certification_run_id="c1",
            store_results=False, min_runs_per_category=1,
        )
        assert out["metrics_validation_failed"] is True
        orch.council.compute_textual_aggregates.assert_not_awaited()
        assert out["total_fault_categories"] == 1
        assert out["fault_category_scorecards"][0]["fault_category"] == "net"
        # llm_council metadata attached
        assert "llm_council" in out
        # responsible_ai + pipeline_tokens + run_level_tokens attached
        assert "responsible_ai" in out
        assert "pipeline_tokens" in out
        assert "run_level_tokens" in out

    async def test_aggregate_all_happy_path(self):
        docs = [
            {"run_id": "r1", "fault_name": "pod-delete",
             "quantitative": {"time_to_detect": 10.0, "run_id": "r1"},
             "qualitative": {"agent_summary": "ok"},
             "token_usage": {"input_tokens": 5, "output_tokens": 3}},
            {"run_id": "r2", "fault_name": "pod-delete",
             "quantitative": {"time_to_detect": 12.0, "run_id": "r2"},
             "qualitative": {"agent_summary": "ok"},
             "token_usage": {"input_tokens": 5, "output_tokens": 3}},
            {"run_id": "r3", "fault_name": "pod-delete",
             "quantitative": {"time_to_detect": 11.0, "run_id": "r3"},
             "qualitative": {"agent_summary": "ok"},
             "token_usage": {"input_tokens": 5, "output_tokens": 3}},
        ]
        svc = _FakeQuery(["net"], {"net": docs})
        orch = _make_orchestrator(svc)
        out = await orch.aggregate_all(
            agent_id="a1", agent_name="n1", certification_run_id="c1",
            store_results=False, min_runs_per_category=3,
        )
        assert out["metrics_validation_failed"] is False
        assert out["total_fault_categories"] == 1
        assert out["total_successful_runs"] == 3
        orch.council.compute_textual_aggregates.assert_awaited()
        # run_level_tokens deduped by run_id
        assert set(out["run_level_tokens"]["run_ids"]) == {"r1", "r2", "r3"}

    async def test_aggregate_all_skips_small_categories(self):
        docs_net = [{"run_id": f"r{i}", "fault_name": "f",
                     "quantitative": {"time_to_detect": 1.0}, "qualitative": {}}
                    for i in range(3)]
        docs_cpu = [{"run_id": "rc", "fault_name": "g",
                     "quantitative": {"time_to_detect": 1.0}, "qualitative": {}}]
        svc = _FakeQuery(["net", "cpu"], {"net": docs_net, "cpu": docs_cpu})
        orch = _make_orchestrator(svc)
        out = await orch.aggregate_all(
            agent_id="a1", agent_name="n1", certification_run_id="c1",
            store_results=False, min_runs_per_category=3,
        )
        # cpu has only 1 doc → skipped
        cats = [sc["fault_category"] for sc in out["fault_category_scorecards"]]
        assert cats == ["net"]

    async def test_aggregate_all_stores_when_db_present(self):
        docs = [{"run_id": "r1", "fault_name": "f",
                 "quantitative": {"time_to_detect": 1.0}, "qualitative": {}}]
        svc = _FakeQuery(["net"], {"net": docs})
        db = MagicMock()
        collection = MagicMock()
        result = MagicMock()
        result.upserted_id = "id1"
        collection.replace_one.return_value = result
        db.sync_db = {"aggregated_scorecards": collection}
        orch = _make_orchestrator(svc, db_client=db)
        await orch.aggregate_all(
            agent_id="a1", agent_name="n1", certification_run_id="c1",
            store_results=True, min_runs_per_category=1,
        )
        collection.replace_one.assert_called_once()
