"""Unit tests for deterministic helpers of
metrics_extractor.scripts.metrics_extractor_from_trace.TraceMetricsExtractor.

Only the non-LLM, pure logic is exercised here: trace-file loading, batching,
span preparation, ground-truth merging, fault-context building, and the
default-extraction factories. All LLM/MongoDB I/O is left untouched (those
clients are created lazily and are not invoked by the tested methods).
"""

import json

import pytest

from utils.custom_errors import MetricsExtractorError
from metrics_extractor.schema.metrics_model import (
    LLMQualitativeExtraction,
    LLMQuantitativeExtraction,
)
from metrics_extractor.scripts.metrics_extractor_from_trace import (
    TraceMetricsExtractor,
)


@pytest.fixture
def extractor():
    # Passing an explicit config dict avoids ConfigLoader (no env/secrets).
    return TraceMetricsExtractor(config={"dummy": True})


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

class TestInit:
    def test_uses_explicit_config(self, extractor):
        assert extractor.config == {"dummy": True}
        assert extractor.llm_client is None
        assert extractor.mongodb_client is None
        assert extractor.token_usage.total_tokens == 0

    def test_batch_size_from_config(self):
        assert TraceMetricsExtractor.BATCH_SIZE >= 1


# ---------------------------------------------------------------------------
# load_trace_file
# ---------------------------------------------------------------------------

class TestLoadTraceFile:
    def test_missing_file_raises(self, extractor, tmp_path):
        with pytest.raises(MetricsExtractorError):
            extractor.load_trace_file(str(tmp_path / "nope.json"))

    def test_plain_list(self, extractor, tmp_path):
        f = tmp_path / "trace.json"
        f.write_text(json.dumps([{"id": "1"}, {"id": "2"}]))
        events = extractor.load_trace_file(str(f))
        assert events == [{"id": "1"}, {"id": "2"}]
        # bucket_metadata stays None for plain lists
        assert extractor.bucket_metadata is None

    def test_bucket_format_extracts_metadata(self, extractor, tmp_path):
        f = tmp_path / "bucket.json"
        f.write_text(json.dumps({
            "fault_id": "pod-delete",
            "fault_name": "pod-delete",
            "injection_timestamp": "2024-01-01T00:00:00Z",
            "events": [{"id": "e1"}],
        }))
        events = extractor.load_trace_file(str(f))
        assert events == [{"id": "e1"}]
        assert extractor.bucket_metadata["fault_id"] == "pod-delete"
        assert "events" not in extractor.bucket_metadata

    def test_invalid_json_raises(self, extractor, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{not valid")
        with pytest.raises(MetricsExtractorError):
            extractor.load_trace_file(str(f))

    def test_events_not_list_raises(self, extractor, tmp_path):
        f = tmp_path / "x.json"
        f.write_text(json.dumps({"events": "notalist"}))
        with pytest.raises(MetricsExtractorError):
            extractor.load_trace_file(str(f))

    def test_unsupported_top_level_type_raises(self, extractor, tmp_path):
        f = tmp_path / "x.json"
        f.write_text(json.dumps(42))
        with pytest.raises(MetricsExtractorError):
            extractor.load_trace_file(str(f))


# ---------------------------------------------------------------------------
# _create_batches
# ---------------------------------------------------------------------------

class TestCreateBatches:
    def test_sorts_by_start_time(self, extractor):
        spans = [
            {"id": "c", "startTime": "2024-01-01T00:00:03Z"},
            {"id": "a", "startTime": "2024-01-01T00:00:01Z"},
            {"id": "b", "startTime": "2024-01-01T00:00:02Z"},
        ]
        batches = extractor._create_batches(spans)
        flat = [s["id"] for b in batches for s in b]
        assert flat == ["a", "b", "c"]

    def test_respects_batch_size(self, extractor):
        n = TraceMetricsExtractor.BATCH_SIZE * 2 + 1
        spans = [{"id": str(i), "startTime": f"{i:05d}"} for i in range(n)]
        batches = extractor._create_batches(spans)
        assert len(batches) == 3
        assert sum(len(b) for b in batches) == n


# ---------------------------------------------------------------------------
# _prepare_span_for_llm
# ---------------------------------------------------------------------------

class TestPrepareSpan:
    def test_selects_and_defaults_fields(self):
        out = TraceMetricsExtractor._prepare_span_for_llm({"id": "1", "name": "n"})
        assert out["id"] == "1"
        assert out["name"] == "n"
        assert out["input"] == ""
        assert out["endTime"] is None
        assert set(out.keys()) == {
            "id", "type", "name", "startTime", "endTime",
            "input", "output", "metadata", "usage",
        }

    def test_drops_unknown_keys(self):
        out = TraceMetricsExtractor._prepare_span_for_llm({"id": "1", "extra": "x"})
        assert "extra" not in out


# ---------------------------------------------------------------------------
# _get_ground_truth
# ---------------------------------------------------------------------------

class TestGetGroundTruth:
    def test_none_without_metadata(self, extractor):
        assert extractor._get_ground_truth() is None

    def test_merges_ideal_fields(self):
        e = TraceMetricsExtractor(config={}, bucket_metadata={
            "ground_truth": {"sla": {"ttd": 60}},
            "ideal_course_of_action": [{"action": "restart"}],
            "ideal_tool_usage_trajectory": ["kubectl get pods"],
        })
        gt = e._get_ground_truth()
        assert gt["sla"] == {"ttd": 60}
        assert gt["ideal_course_of_action"] == [{"action": "restart"}]
        assert gt["ideal_tool_usage_trajectory"] == ["kubectl get pods"]

    def test_empty_ground_truth_returns_none(self):
        e = TraceMetricsExtractor(config={}, bucket_metadata={"other": "x"})
        assert e._get_ground_truth() is None


# ---------------------------------------------------------------------------
# _build_fault_context
# ---------------------------------------------------------------------------

class TestBuildFaultContext:
    def test_empty_without_metadata(self, extractor):
        assert extractor._build_fault_context() == ""

    def test_includes_injection_and_symptoms(self):
        e = TraceMetricsExtractor(config={}, bucket_metadata={
            "injection_timestamp": "2024-01-01T00:00:00Z",
            "fault_name": "pod-delete",
            "namespace": "sock-shop",
            "target_pod": "cart",
            "ground_truth": {
                "fault_description_goal_remediation": {
                    "symptoms": ["CrashLoopBackOff", "5xx errors"],
                    "remediation": "  restart the pod  ",
                }
            },
        })
        ctx = e._build_fault_context()
        assert "## Fault Context" in ctx
        assert "2024-01-01T00:00:00Z" in ctx
        assert "pod-delete" in ctx
        assert "sock-shop" in ctx
        assert "CrashLoopBackOff, 5xx errors" in ctx
        assert "restart the pod" in ctx  # stripped


# ---------------------------------------------------------------------------
# default factories
# ---------------------------------------------------------------------------

class TestDefaultFactories:
    def test_default_quantitative(self):
        q = TraceMetricsExtractor._create_default_quantitative(7)
        assert isinstance(q, LLMQuantitativeExtraction)
        assert q.trajectory_steps == 7
        assert q.detection_success == 0
        assert q.fault_detected == "Unknown - extraction failed"

    def test_default_qualitative(self):
        q = TraceMetricsExtractor._create_default_qualitative()
        assert isinstance(q, LLMQualitativeExtraction)
        assert q.security_compliance_status == "Not Evaluated"
        assert q.agent_summary == "Extraction failed - unable to analyze trace"
