"""
Unit tests for the Fault Bucketing module.

Tests cover:
  - schema/data_models.py: Pydantic models, FaultBucket dataclass, parsing helpers
  - scripts/classifier.py: user message building, fallback classification, config/prompt loading
  - scripts/fault_bucketing.py: pipeline helper methods (event sorting, batching, ground truth, etc.)
"""

import json
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from fault_analyzer.schema.data_models import (
    BatchClassificationResult,
    EventClassification,
    FaultBucket,
    parse_iso_timestamp,
    safe_parse_json,
    safe_parse_python_literal,
)
from fault_analyzer.scripts.classifier import (
    FaultEventClassifier,
    _load_prompt,
    _load_module_config,
)
from fault_analyzer.scripts.fault_bucketing import FaultBucketingPipeline


# ============================================================================
# schema/data_models.py — Pydantic Models
# ============================================================================

class TestEventClassification:
    """Tests for EventClassification Pydantic model."""

    def test_minimal_creation(self):
        ec = EventClassification(event_id="evt-1")
        assert ec.event_id == "evt-1"
        assert ec.related_faults == []
        assert ec.fault_detected is None
        assert ec.fault_mitigated is None
        assert ec.confidence == 0.0
        assert ec.has_quantitative_value is False
        assert ec.has_qualitative_value is False
        assert ec.has_cost_token_details is False

    def test_full_creation(self):
        ec = EventClassification(
            event_id="evt-2",
            related_faults=["pod-delete", "disk-fill"],
            fault_detected="pod-delete",
            detected_fault_severity="critical",
            detected_fault_target_pod="my-pod",
            detected_fault_namespace="default",
            detected_fault_signals=["CrashLoopBackOff"],
            fault_mitigated=None,
            has_quantitative_value=True,
            has_qualitative_value=True,
            has_cost_token_details=False,
            confidence=0.95,
        )
        assert ec.event_id == "evt-2"
        assert len(ec.related_faults) == 2
        assert ec.fault_detected == "pod-delete"
        assert ec.detected_fault_severity == "critical"
        assert ec.detected_fault_target_pod == "my-pod"
        assert ec.detected_fault_namespace == "default"
        assert ec.detected_fault_signals == ["CrashLoopBackOff"]
        assert ec.confidence == 0.95

    def test_model_validate_from_dict(self):
        data = {
            "event_id": "evt-3",
            "related_faults": ["f1"],
            "confidence": 0.8,
        }
        ec = EventClassification.model_validate(data)
        assert ec.event_id == "evt-3"
        assert ec.related_faults == ["f1"]
        assert ec.confidence == 0.8


class TestBatchClassificationResult:
    """Tests for BatchClassificationResult Pydantic model."""

    def test_creation(self):
        classifications = [
            EventClassification(event_id="e1", confidence=0.9),
            EventClassification(event_id="e2", confidence=0.7),
        ]
        batch = BatchClassificationResult(classifications=classifications)
        assert len(batch.classifications) == 2
        assert batch.classifications[0].event_id == "e1"

    def test_empty_classifications(self):
        batch = BatchClassificationResult(classifications=[])
        assert batch.classifications == []


# ============================================================================
# schema/data_models.py — FaultBucket dataclass
# ============================================================================

class TestFaultBucket:
    """Tests for FaultBucket dataclass."""

    def test_minimal_creation(self):
        bucket = FaultBucket(fault_id="f1", fault_name="pod-delete")
        assert bucket.fault_id == "f1"
        assert bucket.fault_name == "pod-delete"
        assert bucket.status == "active"
        assert bucket.events == []
        assert bucket.severity is None
        assert bucket.ground_truth is None

    def test_to_dict(self):
        bucket = FaultBucket(
            fault_id="f1",
            fault_name="pod-delete",
            severity="critical",
            target_pod="my-pod",
            namespace="default",
            detection_signals=["CrashLoopBackOff"],
            events=[{"id": "e1"}, {"id": "e2"}],
            status="closed",
            detected_at="2025-01-01T10:00:00Z",
            mitigated_at="2025-01-01T10:15:00Z",
            ground_truth={"symptoms": ["pod crash"]},
        )
        d = bucket.to_dict()
        assert d["fault_id"] == "f1"
        assert d["fault_name"] == "pod-delete"
        assert d["severity"] == "critical"
        assert d["event_count"] == 2
        assert d["status"] == "closed"
        assert d["ground_truth"] == {"symptoms": ["pod crash"]}
        assert len(d["events"]) == 2

    def test_to_dict_empty_events(self):
        bucket = FaultBucket(fault_id="f1", fault_name="test")
        d = bucket.to_dict()
        assert d["event_count"] == 0
        assert d["events"] == []


# ============================================================================
# schema/data_models.py — Parsing Helpers
# ============================================================================

class TestSafeParseJson:
    """Tests for safe_parse_json helper."""

    def test_valid_json_string(self):
        result = safe_parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_invalid_json_string(self):
        result = safe_parse_json("not json")
        assert result == "not json"

    def test_already_parsed_dict(self):
        original = {"key": "value"}
        result = safe_parse_json(original)
        assert result is original

    def test_none_input(self):
        result = safe_parse_json(None)
        assert result is None

    def test_integer_input(self):
        result = safe_parse_json(42)
        assert result == 42

    def test_json_array(self):
        result = safe_parse_json('[1, 2, 3]')
        assert result == [1, 2, 3]


class TestSafeParsePythonLiteral:
    """Tests for safe_parse_python_literal helper."""

    def test_dict_passthrough(self):
        original = {"key": "value"}
        result = safe_parse_python_literal(original)
        assert result is original

    def test_python_literal_string(self):
        result = safe_parse_python_literal("{'key': 'value'}")
        assert result == {"key": "value"}

    def test_json_string_fallback(self):
        result = safe_parse_python_literal('{"key": "value"}')
        assert result == {"key": "value"}

    def test_unparseable_string(self):
        result = safe_parse_python_literal("definitely not parseable %%")
        assert result == "definitely not parseable %%"

    def test_none_input(self):
        result = safe_parse_python_literal(None)
        assert result is None

    def test_list_literal(self):
        result = safe_parse_python_literal("[1, 2, 3]")
        assert result == [1, 2, 3]


class TestParseIsoTimestamp:
    """Tests for parse_iso_timestamp helper."""

    def test_valid_iso_timestamp(self):
        result = parse_iso_timestamp("2025-01-15T10:30:00")
        assert isinstance(result, datetime)
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 10
        assert result.minute == 30

    def test_timestamp_with_z_suffix(self):
        result = parse_iso_timestamp("2025-01-15T10:30:00Z")
        assert isinstance(result, datetime)
        assert result.year == 2025

    def test_timestamp_with_offset(self):
        result = parse_iso_timestamp("2025-01-15T10:30:00+05:30")
        assert isinstance(result, datetime)

    def test_none_input(self):
        result = parse_iso_timestamp(None)
        assert result is None

    def test_empty_string(self):
        result = parse_iso_timestamp("")
        assert result is None

    def test_invalid_string(self):
        result = parse_iso_timestamp("not-a-timestamp")
        assert result is None


# ============================================================================
# scripts/classifier.py — Config and Prompt Loading
# ============================================================================

class TestConfigAndPromptLoading:
    """Tests for module config and prompt YAML loading."""

    def test_load_module_config(self):
        config = _load_module_config()
        assert "classifier" in config
        assert "pipeline" in config
        assert config["classifier"]["model_name"] == "extraction_model"
        assert config["classifier"]["temperature"] == 0.1
        assert config["classifier"]["max_tokens"] == 4000
        assert config["classifier"]["fallback_confidence"] == 0.3
        assert config["pipeline"]["default_batch_size"] == 10
        assert config["pipeline"]["max_filename_stem_length"] == 80

    def test_load_prompt(self):
        prompt = _load_prompt()
        assert isinstance(prompt, str)
        assert "fault-event classifier" in prompt
        assert "Fault Detection Rules" in prompt
        assert "Fault Mitigation Rules" in prompt
        assert "Classification Rules" in prompt


# ============================================================================
# scripts/classifier.py — FaultEventClassifier
# ============================================================================

class TestFaultEventClassifier:
    """Tests for FaultEventClassifier."""

    def _make_classifier(self):
        return FaultEventClassifier(config={})

    def test_init_loads_config(self):
        classifier = self._make_classifier()
        assert classifier._model_name == "extraction_model"
        assert classifier._temperature == 0.1
        assert classifier._max_tokens == 4000
        assert classifier._fallback_confidence == 0.3
        assert classifier.total_input_tokens == 0
        assert classifier.total_output_tokens == 0

    def test_build_user_message_no_faults(self):
        classifier = self._make_classifier()
        batch = [
            {"id": "e1", "type": "SPAN", "name": "scan", "startTime": "2025-01-01T10:00:00Z"},
        ]
        msg = classifier.build_user_message(batch, {})
        assert "Known Faults" in msg
        assert "No faults have been identified yet" in msg
        assert "Event Batch" in msg
        assert "e1" in msg

    def test_build_user_message_with_known_faults(self):
        classifier = self._make_classifier()
        known = {
            "pod-delete": FaultBucket(
                fault_id="pod-delete",
                fault_name="pod-delete",
                severity="critical",
                target_pod="my-pod",
                namespace="default",
            ),
        }
        batch = [{"id": "e1", "type": "SPAN", "name": "investigate"}]
        msg = classifier.build_user_message(batch, known)
        assert "pod-delete" in msg
        assert "critical" in msg
        assert "my-pod" in msg

    def test_fallback_classify(self):
        classifier = self._make_classifier()
        batch = [
            {"id": "e1"},
            {"id": "e2"},
            {"id": "e3"},
        ]
        known = {
            "f1": FaultBucket(fault_id="f1", fault_name="fault-1"),
            "f2": FaultBucket(fault_id="f2", fault_name="fault-2"),
        }
        results = classifier.fallback_classify(batch, known)
        assert len(results) == 3
        for r in results:
            assert set(r.related_faults) == {"f1", "f2"}
            assert r.confidence == 0.3

    def test_fallback_classify_no_known_faults(self):
        classifier = self._make_classifier()
        batch = [{"id": "e1"}]
        results = classifier.fallback_classify(batch, {})
        assert len(results) == 1
        assert results[0].related_faults == []
        assert results[0].confidence == 0.3

    def test_fallback_classify_missing_id(self):
        classifier = self._make_classifier()
        batch = [{}]
        results = classifier.fallback_classify(batch, {})
        assert len(results) == 1
        assert results[0].event_id == "unknown"


# ============================================================================
# scripts/fault_bucketing.py — FaultBucketingPipeline helpers
# ============================================================================

class TestFaultBucketingPipelineHelpers:
    """Tests for FaultBucketingPipeline static/instance helper methods."""

    # --- _sort_events_chronologically ---

    def test_sort_events_chronologically(self):
        events = [
            {"id": "e3", "startTime": "2025-01-01T12:00:00Z"},
            {"id": "e1", "startTime": "2025-01-01T10:00:00Z"},
            {"id": "e2", "startTime": "2025-01-01T11:00:00Z"},
        ]
        sorted_events = FaultBucketingPipeline._sort_events_chronologically(events)
        assert [e["id"] for e in sorted_events] == ["e1", "e2", "e3"]

    # --- _create_event_batches ---

    def test_create_event_batches(self):
        events = [{"id": f"e{i}"} for i in range(25)]
        batches = FaultBucketingPipeline._create_event_batches(events, 10)
        assert len(batches) == 3
        assert len(batches[0]) == 10
        assert len(batches[1]) == 10
        assert len(batches[2]) == 5

    def test_create_event_batches_exact_fit(self):
        events = [{"id": f"e{i}"} for i in range(20)]
        batches = FaultBucketingPipeline._create_event_batches(events, 10)
        assert len(batches) == 2
        assert all(len(b) == 10 for b in batches)

    def test_create_event_batches_smaller_than_batch(self):
        events = [{"id": "e1"}, {"id": "e2"}]
        batches = FaultBucketingPipeline._create_event_batches(events, 10)
        assert len(batches) == 1
        assert len(batches[0]) == 2

    def test_create_event_batches_empty(self):
        batches = FaultBucketingPipeline._create_event_batches([], 10)
        assert batches == []

    # --- _is_fault_name_span ---

    def test_is_fault_name_span_true(self):
        event = {"name": "fault: pod-delete"}
        assert FaultBucketingPipeline._is_fault_name_span(event) is True

    def test_is_fault_name_span_false(self):
        event = {"name": "experiment-run"}
        assert FaultBucketingPipeline._is_fault_name_span(event) is False

    def test_is_fault_name_span_no_space_after_colon(self):
        event = {"name": "fault:pod-delete"}  # no space after colon — still valid
        assert FaultBucketingPipeline._is_fault_name_span(event) is True

    def test_is_fault_name_span_missing_name(self):
        event = {"type": "SPAN"}
        assert FaultBucketingPipeline._is_fault_name_span(event) is False

    # --- _extract_fault_name_from_span ---

    def test_extract_fault_name(self):
        event = {"name": "fault: pod-delete"}
        assert FaultBucketingPipeline._extract_fault_name_from_span(event) == "pod-delete"

    def test_extract_fault_name_with_spaces(self):
        event = {"name": "fault:  disk-fill "}
        assert FaultBucketingPipeline._extract_fault_name_from_span(event) == "disk-fill"

    def test_extract_fault_name_empty_after_prefix(self):
        event = {"name": "fault: "}
        assert FaultBucketingPipeline._extract_fault_name_from_span(event) is None

    def test_extract_fault_name_no_match(self):
        event = {"name": "experiment-run"}
        assert FaultBucketingPipeline._extract_fault_name_from_span(event) is None

    # --- _extract_metadata_dict ---

    def test_extract_metadata_dict_json_string(self):
        event = {"metadata": '{"attributes": {"fault.name": "pod-delete"}}'}
        result = FaultBucketingPipeline._extract_metadata_dict(event)
        assert result == {"attributes": {"fault.name": "pod-delete"}}

    def test_extract_metadata_dict_already_dict(self):
        event = {"metadata": {"key": "value"}}
        result = FaultBucketingPipeline._extract_metadata_dict(event)
        assert result == {"key": "value"}

    def test_extract_metadata_dict_missing(self):
        event = {"name": "test"}
        result = FaultBucketingPipeline._extract_metadata_dict(event)
        assert result == {}

    def test_extract_metadata_dict_invalid_json(self):
        event = {"metadata": "not json"}
        result = FaultBucketingPipeline._extract_metadata_dict(event)
        assert result == {}

    # --- _place_event_in_buckets ---

    def test_place_event_in_matching_bucket(self):
        pipeline = FaultBucketingPipeline.__new__(FaultBucketingPipeline)
        pipeline.active_faults = {
            "f1": FaultBucket(fault_id="f1", fault_name="fault-1"),
        }
        pipeline.closed_faults = {}
        pipeline.unclassified_events = []

        event = {"id": "e1"}
        classification = EventClassification(
            event_id="e1", related_faults=["f1"], confidence=0.9
        )
        pipeline._place_event_in_buckets(event, classification)

        assert len(pipeline.active_faults["f1"].events) == 1
        assert pipeline.unclassified_events == []

    def test_place_event_unclassified(self):
        pipeline = FaultBucketingPipeline.__new__(FaultBucketingPipeline)
        pipeline.active_faults = {}
        pipeline.closed_faults = {}
        pipeline.unclassified_events = []

        event = {"id": "e1"}
        classification = EventClassification(
            event_id="e1", related_faults=["nonexistent"], confidence=0.5
        )
        pipeline._place_event_in_buckets(event, classification)

        assert len(pipeline.unclassified_events) == 1

    def test_place_event_in_closed_bucket(self):
        pipeline = FaultBucketingPipeline.__new__(FaultBucketingPipeline)
        pipeline.active_faults = {}
        pipeline.closed_faults = {
            "f1": FaultBucket(fault_id="f1", fault_name="fault-1", status="closed"),
        }
        pipeline.unclassified_events = []

        event = {"id": "e1"}
        classification = EventClassification(
            event_id="e1", related_faults=["f1"], confidence=0.8
        )
        pipeline._place_event_in_buckets(event, classification)

        assert len(pipeline.closed_faults["f1"].events) == 1

    # --- _create_fault_bucket_from_span ---

    def test_create_fault_bucket_from_span_basic(self):
        pipeline = FaultBucketingPipeline.__new__(FaultBucketingPipeline)
        pipeline.active_faults = {}
        pipeline.closed_faults = {}
        pipeline.agent_id = None
        pipeline.agent_name = None
        pipeline.agent_version = None
        pipeline.experiment_id = None
        pipeline.run_id = None

        event = {
            "name": "fault: pod-delete",
            "startTime": "2025-01-01T10:00:00Z",
            "endTime": "2025-01-01T10:15:00Z",
            "metadata": json.dumps({
                "attributes": {
                    "fault.target_label": "name=catalogue",
                    "fault.target_namespace": "sock-shop",
                    "fault.status": "completed",
                },
            }),
        }
        pipeline._create_fault_bucket_from_span(event)

        assert "pod-delete" not in pipeline.active_faults  # closed immediately
        assert "pod-delete" in pipeline.closed_faults
        bucket = pipeline.closed_faults["pod-delete"]
        assert bucket.fault_name == "pod-delete"
        assert bucket.namespace == "sock-shop"
        assert bucket.status == "closed"

    def test_create_fault_bucket_dedup_active(self):
        pipeline = FaultBucketingPipeline.__new__(FaultBucketingPipeline)
        existing = FaultBucket(
            fault_id="pod-delete", fault_name="pod-delete", status="active"
        )
        pipeline.active_faults = {"pod-delete": existing}
        pipeline.closed_faults = {}
        pipeline.agent_id = None
        pipeline.agent_name = None
        pipeline.agent_version = None
        pipeline.experiment_id = None
        pipeline.run_id = None

        event = {
            "name": "fault: pod-delete",
            "startTime": "2025-01-01T10:00:00Z",
            "metadata": "{}",
        }
        pipeline._create_fault_bucket_from_span(event)

        # Should NOT create a new bucket, just add event to existing
        assert len(pipeline.active_faults) == 1
        assert len(pipeline.active_faults["pod-delete"].events) == 1

    def test_create_fault_bucket_new_after_closed(self):
        pipeline = FaultBucketingPipeline.__new__(FaultBucketingPipeline)
        closed = FaultBucket(
            fault_id="pod-delete", fault_name="pod-delete", status="closed"
        )
        pipeline.active_faults = {}
        pipeline.closed_faults = {"pod-delete": closed}
        pipeline.agent_id = None
        pipeline.agent_name = None
        pipeline.agent_version = None
        pipeline.experiment_id = None
        pipeline.run_id = None

        event = {
            "name": "fault: pod-delete",
            "startTime": "2025-01-01T11:00:00Z",
            "endTime": "2025-01-01T11:15:00Z",
            "metadata": json.dumps({
                "attributes": {"fault.status": "completed"},
            }),
        }
        pipeline._create_fault_bucket_from_span(event)

        # New bucket with suffix
        assert "pod-delete_2" in pipeline.closed_faults
        assert pipeline.closed_faults["pod-delete_2"].fault_name == "pod-delete"

    # --- _close_fault ---

    def test_close_fault(self):
        pipeline = FaultBucketingPipeline.__new__(FaultBucketingPipeline)
        pipeline.active_faults = {
            "f1": FaultBucket(
                fault_id="f1", fault_name="fault-1",
                events=[{"id": "e1"}, {"id": "e2"}],
            ),
        }
        pipeline.closed_faults = {}

        pipeline._close_fault("f1", mitigated_at="2025-01-01T10:15:00Z")

        assert "f1" not in pipeline.active_faults
        assert "f1" in pipeline.closed_faults
        assert pipeline.closed_faults["f1"].status == "closed"
        assert pipeline.closed_faults["f1"].mitigated_at == "2025-01-01T10:15:00Z"

    def test_close_fault_not_active(self):
        pipeline = FaultBucketingPipeline.__new__(FaultBucketingPipeline)
        pipeline.active_faults = {}
        pipeline.closed_faults = {}

        # Should not raise
        pipeline._close_fault("nonexistent")
        assert pipeline.closed_faults == {}


