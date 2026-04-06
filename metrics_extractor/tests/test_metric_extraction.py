"""
Unit tests for the Metric Extraction from Trace module.

Tests cover:
  - schema/data_models.py: TokenUsage, ExtractionResult dataclasses
  - scripts/span_aggregator.py: QuantitativeAggregator, QualitativeAggregator
  - scripts/metrics_extractor_from_trace.py: batching, trace loading, config/prompt loading
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from metrics_extractor.schema.data_models import (
    ExtractionResult,
    TokenUsage,
)
from metrics_extractor.scripts.span_aggregator import (
    QualitativeAggregator,
    QuantitativeAggregator,
)
from metrics_extractor.scripts.metrics_extractor_from_trace import (
    TraceMetricsExtractor,
    _load_module_config,
    _load_prompts,
)


# ============================================================================
# schema/data_models.py — TokenUsage
# ============================================================================

class TestTokenUsage:
    """Tests for TokenUsage dataclass."""

    def test_default_values(self):
        usage = TokenUsage()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.total_tokens == 0

    def test_add_tokens(self):
        usage = TokenUsage()
        usage.add({"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.total_tokens == 150

    def test_add_tokens_multiple(self):
        usage = TokenUsage()
        usage.add({"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
        usage.add({"input_tokens": 200, "output_tokens": 100, "total_tokens": 300})
        assert usage.input_tokens == 300
        assert usage.output_tokens == 150
        assert usage.total_tokens == 450

    def test_add_partial_keys(self):
        usage = TokenUsage()
        usage.add({"input_tokens": 100})
        assert usage.input_tokens == 100
        assert usage.output_tokens == 0
        assert usage.total_tokens == 0

    def test_add_empty_dict(self):
        usage = TokenUsage()
        usage.add({})
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.total_tokens == 0

    def test_to_dict(self):
        usage = TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150)
        result = usage.to_dict()
        assert result == {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
        }


# ============================================================================
# schema/data_models.py — ExtractionResult
# ============================================================================

class TestExtractionResult:
    """Tests for ExtractionResult dataclass."""

    def _make_mock_quant(self):
        mock = MagicMock()
        mock.model_dump.return_value = {"fault_detected": "pod-delete"}
        mock.to_dict = None
        del mock.to_dict
        return mock

    def _make_mock_qual(self):
        mock = MagicMock()
        mock.model_dump.return_value = {"rai_check_status": "Passed"}
        mock.to_dict = None
        del mock.to_dict
        return mock

    def test_creation_defaults(self):
        quant = self._make_mock_quant()
        qual = self._make_mock_qual()
        result = ExtractionResult(quantitative=quant, qualitative=qual)
        assert result.quantitative is quant
        assert result.qualitative is qual
        assert result.token_usage.input_tokens == 0
        assert result.mongodb_document_id is None

    def test_creation_with_all_fields(self):
        quant = self._make_mock_quant()
        qual = self._make_mock_qual()
        usage = TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150)
        result = ExtractionResult(
            quantitative=quant,
            qualitative=qual,
            token_usage=usage,
            mongodb_document_id="abc123",
        )
        assert result.token_usage.input_tokens == 100
        assert result.mongodb_document_id == "abc123"

    def test_to_dict(self):
        quant = self._make_mock_quant()
        qual = self._make_mock_qual()
        result = ExtractionResult(
            quantitative=quant,
            qualitative=qual,
            mongodb_document_id="doc-1",
        )
        d = result.to_dict()
        assert "quantitative" in d
        assert "qualitative" in d
        assert "token_usage" in d
        assert d["mongodb_document_id"] == "doc-1"

    def test_to_dict_without_mongodb_id(self):
        quant = self._make_mock_quant()
        qual = self._make_mock_qual()
        result = ExtractionResult(quantitative=quant, qualitative=qual)
        d = result.to_dict()
        assert "mongodb_document_id" not in d


# ============================================================================
# scripts/span_aggregator.py — QuantitativeAggregator
# ============================================================================

class TestQuantitativeAggregator:
    """Tests for QuantitativeAggregator."""

    def test_parse_timestamp_iso(self):
        ts = "2025-01-15T10:30:00+00:00"
        result = QuantitativeAggregator._parse_timestamp(ts)
        assert result is not None
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 10
        assert result.minute == 30

    def test_parse_timestamp_z_suffix(self):
        ts = "2025-01-15T10:30:00Z"
        result = QuantitativeAggregator._parse_timestamp(ts)
        assert result is not None
        assert result.tzinfo is None  # should be naive UTC

    def test_parse_timestamp_empty(self):
        assert QuantitativeAggregator._parse_timestamp("") is None

    def test_parse_timestamp_none(self):
        assert QuantitativeAggregator._parse_timestamp(None) is None

    def test_parse_timestamp_invalid(self):
        assert QuantitativeAggregator._parse_timestamp("not-a-date") is None

    def test_extract_from_fault_config_empty(self):
        result = QuantitativeAggregator.extract_from_fault_config(None)
        assert result == {}

    def test_extract_from_fault_config_full(self):
        config = {
            "agent": {"agent_name": "TestAgent", "agent_id": "agent-1"},
            "injection_timestamp": "2025-01-15T10:00:00Z",
            "fault_name": "pod-delete",
            "fault_category": "compute",
            "fault_configuration": {
                "target_service": "my-pod",
                "target_namespace": "default",
            },
        }
        result = QuantitativeAggregator.extract_from_fault_config(config)
        assert result["agent_name"] == "TestAgent"
        assert result["agent_id"] == "agent-1"
        assert result["fault_injection_time"] == "2025-01-15T10:00:00Z"
        assert result["injected_fault_name"] == "pod-delete"
        assert result["detected_fault_type"] == "pod-delete"
        assert result["injected_fault_category"] == "compute"
        assert result["fault_target_service"] == "my-pod"
        assert result["fault_namespace"] == "default"

    def test_extract_from_fault_config_partial(self):
        config = {"fault_name": "disk-fill"}
        result = QuantitativeAggregator.extract_from_fault_config(config)
        assert result["injected_fault_name"] == "disk-fill"
        assert "agent_name" not in result

    def test_aggregate_basic(self):
        agg = QuantitativeAggregator()
        partial = [
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "fault_detected": "pod-delete fault",
                "tool_calls": [{"tool_name": "kubectl"}],
            },
            {
                "input_tokens": 200,
                "output_tokens": 75,
                "fault_detected": "Unknown",
                "tool_calls": [{"tool_name": "get_logs"}],
            },
        ]
        result = agg.aggregate(partial, total_spans=10, span_times=None)
        assert result["input_tokens"] == 300
        assert result["output_tokens"] == 125
        assert result["trajectory_steps"] == 10
        assert result["fault_detected"] == "pod-delete fault"
        assert len(result["tool_calls"]) == 2

    def test_aggregate_with_span_times(self):
        agg = QuantitativeAggregator()
        span_times = {
            "agent_fault_detection_time": "2025-01-15T10:30:00Z",
            "agent_fault_mitigation_time": "2025-01-15T10:35:00Z",
        }
        result = agg.aggregate(
            partial_metrics=[{}],
            total_spans=5,
            span_times=span_times,
            fault_config={"injection_timestamp": "2025-01-15T10:25:00Z"},
        )
        assert result["agent_fault_detection_time"] == "2025-01-15T10:30:00Z"
        assert result["time_to_detect"] == 300.0  # 5 minutes
        assert result["time_to_mitigate"] == 600.0  # 10 minutes

    def test_aggregate_pii_detection(self):
        agg = QuantitativeAggregator()
        partial = [
            {"pii_detection": False},
            {"pii_detection": True},
        ]
        result = agg.aggregate(partial, total_spans=2, span_times=None)
        assert result["pii_detection"] is True

    def test_aggregate_pii_detection_all_false(self):
        agg = QuantitativeAggregator()
        partial = [
            {"pii_detection": False},
            {"pii_detection": False},
        ]
        result = agg.aggregate(partial, total_spans=2, span_times=None)
        assert result["pii_detection"] is False

    def test_aggregate_tool_selection_accuracy(self):
        agg = QuantitativeAggregator()
        partial = [
            {"correct_tool_selections": 3, "total_tool_selections": 5},
            {"correct_tool_selections": 4, "total_tool_selections": 5},
        ]
        result = agg.aggregate(partial, total_spans=10, span_times=None)
        assert result["tool_selection_accuracy"] == 0.7  # 7/10


# ============================================================================
# scripts/span_aggregator.py — QualitativeAggregator
# ============================================================================

class TestQualitativeAggregator:
    """Tests for QualitativeAggregator."""

    def test_aggregate_reasoning_score_average(self):
        agg = QualitativeAggregator()
        partial = [
            {"reasoning_quality_score": 8.0},
            {"reasoning_quality_score": 6.0},
        ]
        result = agg.aggregate(partial)
        assert result["reasoning_quality_score"] == 7.0

    def test_aggregate_reasoning_score_single(self):
        agg = QualitativeAggregator()
        partial = [{"reasoning_quality_score": 9.5}]
        result = agg.aggregate(partial)
        assert result["reasoning_quality_score"] == 9.5

    def test_aggregate_hallucination_score(self):
        agg = QualitativeAggregator()
        partial = [
            {"hallucination_count": 1, "total_response_count": 5},
            {"hallucination_count": 0, "total_response_count": 3},
        ]
        result = agg.aggregate(partial)
        assert result["hallucination_score"] == 0.12  # 1/8 = 0.125 → rounded to 0.12

    def test_aggregate_hallucination_zero_responses(self):
        agg = QualitativeAggregator()
        partial = [
            {"hallucination_count": 0, "total_response_count": 0},
        ]
        result = agg.aggregate(partial)
        assert "hallucination_score" not in result

    def test_aggregate_empty(self):
        agg = QualitativeAggregator()
        result = agg.aggregate([])
        assert result == {}

    def test_aggregate_no_numeric_fields(self):
        agg = QualitativeAggregator()
        partial = [{"rai_check_status": "Passed"}]
        result = agg.aggregate(partial)
        assert result == {}


# ============================================================================
# scripts/metrics_extractor_from_trace.py — Config & Prompt Loading
# ============================================================================

class TestModuleConfig:
    """Tests for module-level config and prompt loading."""

    def test_load_module_config(self):
        config = _load_module_config()
        assert "extractor" in config
        assert "mongodb" in config
        assert config["extractor"]["batch_size"] == 15

    def test_load_prompts(self):
        prompts = _load_prompts()
        assert "quantitative_batch_extraction" in prompts
        assert "qualitative_batch_extraction" in prompts
        assert "quantitative_aggregation" in prompts
        assert "qualitative_aggregation" in prompts
        assert "span_identification" in prompts
        assert "ground_truth_with_config" in prompts
        assert "ground_truth_without_config" in prompts
        assert "behavioural_with_config" in prompts
        assert "behavioural_without_config" in prompts


# ============================================================================
# scripts/metrics_extractor_from_trace.py — TraceMetricsExtractor
# ============================================================================

class TestTraceMetricsExtractor:
    """Tests for TraceMetricsExtractor helper methods."""

    def test_create_batches_small(self):
        extractor = TraceMetricsExtractor.__new__(TraceMetricsExtractor)
        extractor.BATCH_SIZE = 15
        spans = [{"startTime": f"2025-01-15T10:{i:02d}:00Z"} for i in range(5)]
        batches = extractor._create_batches(spans)
        assert len(batches) == 1
        assert len(batches[0]) == 5

    def test_create_batches_exact(self):
        extractor = TraceMetricsExtractor.__new__(TraceMetricsExtractor)
        extractor.BATCH_SIZE = 5
        spans = [{"startTime": f"2025-01-15T10:{i:02d}:00Z"} for i in range(10)]
        batches = extractor._create_batches(spans)
        assert len(batches) == 2
        assert len(batches[0]) == 5
        assert len(batches[1]) == 5

    def test_create_batches_remainder(self):
        extractor = TraceMetricsExtractor.__new__(TraceMetricsExtractor)
        extractor.BATCH_SIZE = 3
        spans = [{"startTime": f"2025-01-15T10:{i:02d}:00Z"} for i in range(7)]
        batches = extractor._create_batches(spans)
        assert len(batches) == 3
        assert len(batches[0]) == 3
        assert len(batches[1]) == 3
        assert len(batches[2]) == 1

    def test_create_batches_sorts_by_start_time(self):
        extractor = TraceMetricsExtractor.__new__(TraceMetricsExtractor)
        extractor.BATCH_SIZE = 15
        spans = [
            {"startTime": "2025-01-15T10:02:00Z"},
            {"startTime": "2025-01-15T10:00:00Z"},
            {"startTime": "2025-01-15T10:01:00Z"},
        ]
        batches = extractor._create_batches(spans)
        assert batches[0][0]["startTime"] == "2025-01-15T10:00:00Z"
        assert batches[0][1]["startTime"] == "2025-01-15T10:01:00Z"
        assert batches[0][2]["startTime"] == "2025-01-15T10:02:00Z"

    def test_prepare_span_for_llm(self):
        span = {
            "id": "span-1",
            "type": "SPAN",
            "name": "fault_detected",
            "startTime": "2025-01-15T10:00:00Z",
            "endTime": "2025-01-15T10:01:00Z",
            "input": '{"pod": "my-pod"}',
            "output": "Fault detected",
            "metadata": '{"action": "fault_detected"}',
            "extra_field": "ignored",
        }
        result = TraceMetricsExtractor._prepare_span_for_llm(span)
        assert result["id"] == "span-1"
        assert result["type"] == "SPAN"
        assert "extra_field" not in result

    def test_load_trace_file(self):
        extractor = TraceMetricsExtractor.__new__(TraceMetricsExtractor)
        spans = [{"id": "s1", "startTime": "2025-01-15T10:00:00Z"}]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(spans, f)
            f.flush()
            loaded = extractor.load_trace_file(f.name)
        assert len(loaded) == 1
        assert loaded[0]["id"] == "s1"

    def test_load_trace_file_not_found(self):
        extractor = TraceMetricsExtractor.__new__(TraceMetricsExtractor)
        with pytest.raises(FileNotFoundError):
            extractor.load_trace_file("/nonexistent/path/trace.json")

    def test_load_fault_config_not_found(self):
        result = TraceMetricsExtractor._load_fault_config("/nonexistent/path.json")
        assert result is None

    def test_load_fault_config_valid(self):
        config = {
            "fault_id": "f1",
            "fault_name": "pod-delete",
            "injection_timestamp": "2025-01-15T10:00:00Z",
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(config, f)
            f.flush()
            result = TraceMetricsExtractor._load_fault_config(f.name)
        assert result is not None
        assert result["fault_id"] == "f1"
        assert result["fault_name"] == "pod-delete"

    def test_create_default_quantitative(self):
        result = TraceMetricsExtractor._create_default_quantitative(10)
        assert result.trajectory_steps == 10
        assert result.fault_detected == "Unknown - extraction failed"
        assert result.input_tokens == 0

    def test_create_default_qualitative(self):
        result = TraceMetricsExtractor._create_default_qualitative()
        assert result.rai_check_status == "Not Evaluated"
        assert result.security_compliance_status == "Not Evaluated"

    def test_batch_size_from_config(self):
        assert TraceMetricsExtractor.BATCH_SIZE == 15
