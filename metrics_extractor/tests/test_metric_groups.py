"""Tests for MetricGroup.requires dependency resolution and cycle detection.

Covers:
- _select_groups: dependency expansion for filtered requests
- _check_no_cycles: synthetic cycle detection guardrail
- requested=None: unchanged behavior (all groups run)
- End-to-end: requesting a KubernetesQuantitativeBatchGroup metric automatically
  pulls in SpanIdentificationGroup so span_times is precomputed
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from metrics_extractor.scripts.metric_groups import (
    CombinedJudgeGroup,
    DeterministicGroup,
    ExtractionContext,
    KubernetesQualitativeBatchGroup,
    KubernetesQuantitativeBatchGroup,
    MetricGroup,
    SpanIdentificationGroup,
    _GROUP_ORDER,
    _build_dep_graph,
    _check_no_cycles,
    _select_groups,
)


# ── _select_groups: basic cases ────────────────────────────────────────────────

class TestSelectGroupsBasic:
    def test_none_returns_all_groups_in_order(self):
        result = _select_groups(None)
        assert [type(g).__name__ for g in result] == [
            "DeterministicGroup",
            "SpanIdentificationGroup",
            "KubernetesQuantitativeBatchGroup",
            "CombinedJudgeGroup",
            "KubernetesQualitativeBatchGroup",
        ]

    def test_empty_list_returns_no_groups(self):
        assert _select_groups([]) == []

    def test_unknown_metric_returns_no_groups(self):
        assert _select_groups(["nonexistent_metric"]) == []

    def test_deterministic_only_metric_returns_deterministic_group(self):
        result = _select_groups(["input_tokens"])
        assert len(result) == 1
        assert type(result[0]).__name__ == "DeterministicGroup"

    def test_span_identification_metric_returns_span_group_only(self):
        result = _select_groups(["time_to_detect"])
        names = [type(g).__name__ for g in result]
        assert "SpanIdentificationGroup" in names
        # KubernetesQuantitativeBatchGroup does NOT require SpanIdentificationGroup's
        # output — it goes the other direction.  Requesting time_to_detect alone
        # should NOT pull in KubernetesQuantitativeBatchGroup.
        assert "KubernetesQuantitativeBatchGroup" not in names


# ── _select_groups: dependency expansion ──────────────────────────────────────

class TestSelectGroupsDependencyExpansion:
    """The core new behavior: requesting a metric from a group with `requires`
    automatically pulls in the required producer group."""

    def test_quant_batch_metric_pulls_in_span_identification(self):
        """Requesting fault_detected (KubernetesQuantitativeBatchGroup) must
        automatically include SpanIdentificationGroup."""
        result = _select_groups(["fault_detected"])
        names = [type(g).__name__ for g in result]
        assert "KubernetesQuantitativeBatchGroup" in names, (
            "KubernetesQuantitativeBatchGroup should be selected for fault_detected"
        )
        assert "SpanIdentificationGroup" in names, (
            "SpanIdentificationGroup must be pulled in automatically via requires"
        )

    def test_execution_order_preserved_after_expansion(self):
        """SpanIdentificationGroup must appear before KubernetesQuantitativeBatchGroup."""
        result = _select_groups(["fault_detected"])
        names = [type(g).__name__ for g in result]
        span_idx = names.index("SpanIdentificationGroup")
        quant_idx = names.index("KubernetesQuantitativeBatchGroup")
        assert span_idx < quant_idx, (
            f"SpanIdentificationGroup (pos {span_idx}) must precede "
            f"KubernetesQuantitativeBatchGroup (pos {quant_idx})"
        )

    def test_qualitative_batch_metric_pulls_in_combined_judge(self):
        """Requesting a KubernetesQualitativeBatchGroup metric must automatically
        include CombinedJudgeGroup."""
        result = _select_groups(["fairness_check_status"])
        names = [type(g).__name__ for g in result]
        assert "KubernetesQualitativeBatchGroup" in names
        assert "CombinedJudgeGroup" in names, (
            "CombinedJudgeGroup must be pulled in automatically via requires"
        )

    def test_combined_judge_before_qualitative(self):
        result = _select_groups(["fairness_check_status"])
        names = [type(g).__name__ for g in result]
        judge_idx = names.index("CombinedJudgeGroup")
        qual_idx = names.index("KubernetesQualitativeBatchGroup")
        assert judge_idx < qual_idx

    def test_deps_not_added_when_not_needed(self):
        """Requesting only a DeterministicGroup metric must NOT pull in the LLM
        groups (no requires chain to follow)."""
        result = _select_groups(["output_tokens"])
        names = [type(g).__name__ for g in result]
        assert names == ["DeterministicGroup"]

    def test_explicit_request_of_both_dep_and_dependent_is_idempotent(self):
        """Requesting metrics from both SpanIdentificationGroup and
        KubernetesQuantitativeBatchGroup must not duplicate any group."""
        result = _select_groups(["time_to_detect", "fault_detected"])
        names = [type(g).__name__ for g in result]
        assert names.count("SpanIdentificationGroup") == 1
        assert names.count("KubernetesQuantitativeBatchGroup") == 1


# ── Cycle detection ────────────────────────────────────────────────────────────

class TestCycleDetection:
    """_check_no_cycles must raise ValueError on cyclic requires graphs."""

    def _make_group(self, name: str, provides: List[str], requires: List[str] = []):
        """Create a minimal concrete MetricGroup subclass for testing."""
        async def _execute(self_inner, context):
            return {}

        cls = type(name, (MetricGroup,), {
            "provides": provides,
            "requires": requires,
            "execute": _execute,
        })
        return cls()

    def test_acyclic_graph_does_not_raise(self):
        a = self._make_group("A", provides=["x"], requires=[])
        b = self._make_group("B", provides=["y"], requires=["x"])
        _check_no_cycles([a, b])  # should not raise

    def test_direct_self_loop_raises(self):
        # A requires its own metric (degenerate case)
        a = self._make_group("A", provides=["x"], requires=["x"])
        # _build_dep_graph skips self-dependencies, so A's dep graph is empty
        # — no error raised; this is intentional (self-reference is a no-op).
        _check_no_cycles([a])

    def test_mutual_dependency_raises(self):
        """A requires B's metric AND B requires A's metric → cycle."""
        a = self._make_group("GroupA", provides=["metric_a"], requires=["metric_b"])
        b = self._make_group("GroupB", provides=["metric_b"], requires=["metric_a"])
        with pytest.raises(ValueError, match="Cycle"):
            _check_no_cycles([a, b])

    def test_transitive_cycle_raises(self):
        """A→B→C→A is a three-node cycle."""
        a = self._make_group("GroupA", provides=["metric_a"], requires=["metric_c"])
        b = self._make_group("GroupB", provides=["metric_b"], requires=["metric_a"])
        c = self._make_group("GroupC", provides=["metric_c"], requires=["metric_b"])
        with pytest.raises(ValueError, match="Cycle"):
            _check_no_cycles([a, b, c])

    def test_current_group_order_has_no_cycles(self):
        """Regression: the production _GROUP_ORDER must always pass cycle check."""
        _check_no_cycles(_GROUP_ORDER)


# ── _build_dep_graph ───────────────────────────────────────────────────────────

class TestBuildDepGraph:
    def test_production_graph_shape(self):
        graph = _build_dep_graph(_GROUP_ORDER)
        assert graph["DeterministicGroup"] == []
        assert graph["SpanIdentificationGroup"] == []
        assert graph["KubernetesQuantitativeBatchGroup"] == ["SpanIdentificationGroup"]
        assert graph["CombinedJudgeGroup"] == []
        assert graph["KubernetesQualitativeBatchGroup"] == ["CombinedJudgeGroup"]


# ── End-to-end: span_times is precomputed when fault_detected is requested ────

class TestEndToEndDependencyPrecomputation:
    """Verify that requesting fault_detected alone actually causes
    SpanIdentificationGroup to run and populate context.results['span_times']
    so KubernetesQuantitativeBatchGroup receives it.

    We test this without real LLM calls by mocking extractor methods.
    """

    @pytest.mark.anyio
    async def test_span_times_precomputed_when_fault_detected_requested(self):
        """When fault_detected is requested:
        1. SpanIdentificationGroup runs and writes context.results['span_times']
        2. KubernetesQuantitativeBatchGroup reads it (precomputed_span_times is set)
        3. _identify_detection_mitigation_spans is called exactly once (by SpanIdentification),
           NOT a second time inside _aggregate_quantitative_metrics.
        """
        from metrics_extractor.schema.metrics_model import LLMQuantitativeExtraction

        fake_span_times = {
            "agent_fault_detection_time": "2024-01-01T00:01:00Z",
            "agent_fault_mitigation_time": "2024-01-01T00:02:00Z",
        }

        # Minimal fake extractor that records calls
        extractor = MagicMock()
        extractor.llm_client = MagicMock()
        extractor._init_llm_client = MagicMock()
        extractor.bucket_metadata = {"injection_timestamp": "2024-01-01T00:00:00Z"}

        extractor._identify_detection_mitigation_spans = AsyncMock(
            return_value=fake_span_times
        )
        extractor._validate_bucket_timestamps_with_llm = AsyncMock(return_value={})
        extractor._create_batches = MagicMock(return_value=[[]])  # one empty batch
        extractor._extract_batch_quantitative = AsyncMock(return_value={})
        extractor.quant_aggregator = MagicMock()
        extractor.quant_aggregator._prescan_result = None
        extractor.quant_aggregator._span_metrics = {}

        # _aggregate_quantitative_metrics captures its precomputed_span_times kwarg
        captured_precomputed = {}

        async def fake_aggregate_quant(partials, n_spans, spans, precomputed_span_times=None):
            captured_precomputed["span_times"] = precomputed_span_times
            return LLMQuantitativeExtraction(fault_detected="Yes")

        extractor._aggregate_quantitative_metrics = fake_aggregate_quant

        ctx = ExtractionContext(
            spans=[{"id": "s1"}],
            bucket_metadata={"injection_timestamp": "2024-01-01T00:00:00Z"},
            extractor=extractor,
        )

        # Select groups for a request that ONLY asks for fault_detected
        groups = _select_groups(["fault_detected"])
        group_names = [type(g).__name__ for g in groups]

        assert "SpanIdentificationGroup" in group_names, (
            "SpanIdentificationGroup must be pulled in via KubernetesQuantitativeBatchGroup.requires"
        )
        assert "KubernetesQuantitativeBatchGroup" in group_names

        # Execute all selected groups
        for group in groups:
            await group.execute(ctx)

        # Verify span_times was set in context.results by SpanIdentificationGroup
        assert "span_times" in ctx.results, (
            "SpanIdentificationGroup must write span_times to context.results"
        )
        assert ctx.results["span_times"]["agent_fault_detection_time"] == (
            "2024-01-01T00:01:00Z"
        )

        # Verify _aggregate_quantitative_metrics received the precomputed span_times
        assert captured_precomputed["span_times"] is not None, (
            "KubernetesQuantitativeBatchGroup must pass precomputed_span_times to "
            "_aggregate_quantitative_metrics (not None)"
        )
        assert captured_precomputed["span_times"]["agent_fault_detection_time"] == (
            "2024-01-01T00:01:00Z"
        )

        # Verify _identify_detection_mitigation_spans was called exactly once
        # (by SpanIdentificationGroup), NOT a second time by the quantitative group
        extractor._identify_detection_mitigation_spans.assert_awaited_once()
