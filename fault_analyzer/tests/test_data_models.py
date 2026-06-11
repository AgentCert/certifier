"""Unit tests for fault_analyzer.schema.data_models.

Covers the deterministic Pydantic models, the FaultBucket dataclass +
its to_dict serialization, and the three pure parsing helpers.
"""

from datetime import datetime

import pytest

from fault_analyzer.schema.data_models import (
    BatchClassificationResult,
    EventClassification,
    FaultBucket,
    parse_iso_timestamp,
    safe_parse_json,
    safe_parse_python_literal,
)


# ---------------------------------------------------------------------------
# EventClassification
# ---------------------------------------------------------------------------

class TestEventClassification:
    def test_minimal_creation_defaults(self):
        ec = EventClassification(event_id="evt-1")
        assert ec.event_id == "evt-1"
        # All optional/collection defaults
        assert ec.related_faults == []
        assert ec.fault_detected is None
        assert ec.detected_fault_severity is None
        assert ec.detected_fault_target_pod is None
        assert ec.detected_fault_namespace is None
        assert ec.detected_fault_signals == []
        assert ec.fault_mitigated is None
        assert ec.has_quantitative_value is False
        assert ec.has_qualitative_value is False
        assert ec.has_cost_token_details is False
        assert ec.confidence == 0.0
        assert ec.unclassified_reason is None
        assert ec.fault_reasoning == {}

    def test_default_factories_are_independent(self):
        a = EventClassification(event_id="a")
        b = EventClassification(event_id="b")
        a.related_faults.append("f1")
        a.fault_reasoning["f1"] = "because"
        assert b.related_faults == []
        assert b.fault_reasoning == {}

    def test_full_population(self):
        ec = EventClassification(
            event_id="evt-2",
            related_faults=["pod-delete", "disk-fill"],
            fault_detected="pod-delete",
            detected_fault_severity="critical",
            detected_fault_target_pod="cart-pod",
            detected_fault_namespace="sock-shop",
            detected_fault_signals=["CrashLoopBackOff"],
            fault_mitigated="pod-delete",
            has_quantitative_value=True,
            confidence=0.87,
            fault_reasoning={"pod-delete": "symptom match"},
        )
        assert ec.related_faults == ["pod-delete", "disk-fill"]
        assert ec.confidence == pytest.approx(0.87)
        assert ec.fault_reasoning["pod-delete"] == "symptom match"

    def test_model_validate_from_dict(self):
        ec = EventClassification.model_validate(
            {"event_id": "x", "related_faults": ["f1"], "confidence": 0.5}
        )
        assert ec.event_id == "x"
        assert ec.related_faults == ["f1"]

    def test_missing_event_id_raises(self):
        with pytest.raises(Exception):
            EventClassification()


# ---------------------------------------------------------------------------
# BatchClassificationResult
# ---------------------------------------------------------------------------

class TestBatchClassificationResult:
    def test_wraps_classifications(self):
        batch = BatchClassificationResult(
            classifications=[
                EventClassification(event_id="a"),
                EventClassification(event_id="b"),
            ]
        )
        assert len(batch.classifications) == 2
        assert [c.event_id for c in batch.classifications] == ["a", "b"]

    def test_validate_from_dicts(self):
        batch = BatchClassificationResult.model_validate(
            {"classifications": [{"event_id": "z"}]}
        )
        assert batch.classifications[0].event_id == "z"
        assert isinstance(batch.classifications[0], EventClassification)

    def test_classifications_required(self):
        with pytest.raises(Exception):
            BatchClassificationResult()


# ---------------------------------------------------------------------------
# FaultBucket dataclass + to_dict
# ---------------------------------------------------------------------------

class TestFaultBucket:
    def test_defaults(self):
        b = FaultBucket(fault_id="pod-delete", fault_name="pod-delete")
        assert b.severity is None
        assert b.target_pod is None
        assert b.detection_signals == []
        assert b.events == []
        assert b.status == "active"
        assert b.detected_at is None
        assert b.mitigated_at is None

    def test_mutable_defaults_independent(self):
        a = FaultBucket(fault_id="a", fault_name="a")
        b = FaultBucket(fault_id="b", fault_name="b")
        a.events.append({"id": "1"})
        a.detection_signals.append("sig")
        assert b.events == []
        assert b.detection_signals == []

    def test_to_dict_event_count_and_keys(self):
        b = FaultBucket(
            fault_id="pod-delete",
            fault_name="pod-delete",
            severity="critical",
            target_pod="cart",
            namespace="sock-shop",
            events=[{"id": "e1"}, {"id": "e2"}],
            status="closed",
            detected_at="2024-01-01T00:00:01Z",
            mitigated_at="2024-01-01T00:00:09Z",
            injection_timestamp="2024-01-01T00:00:00Z",
        )
        d = b.to_dict()
        assert d["event_count"] == 2
        assert d["events"] == [{"id": "e1"}, {"id": "e2"}]
        assert d["fault_id"] == "pod-delete"
        assert d["severity"] == "critical"
        assert d["status"] == "closed"
        assert d["detected_at"] == "2024-01-01T00:00:01Z"
        # Sanity: serialized dict contains all the documented keys
        expected_keys = {
            "fault_id", "fault_name", "severity", "target_pod", "namespace",
            "detection_signals", "status", "detected_at", "mitigated_at",
            "injection_timestamp", "injection_end_timestamp", "injection_metadata",
            "ground_truth", "sla", "ideal_course_of_action",
            "ideal_tool_usage_trajectory", "agent_id", "agent_name",
            "agent_version", "experiment_id", "run_id", "event_count", "events",
        }
        assert expected_keys <= set(d.keys())

    def test_to_dict_empty_events(self):
        b = FaultBucket(fault_id="x", fault_name="x")
        assert b.to_dict()["event_count"] == 0
        assert b.to_dict()["events"] == []


# ---------------------------------------------------------------------------
# safe_parse_json
# ---------------------------------------------------------------------------

class TestSafeParseJson:
    def test_parses_valid_json_string(self):
        assert safe_parse_json('{"a": 1}') == {"a": 1}
        assert safe_parse_json("[1, 2, 3]") == [1, 2, 3]

    def test_returns_invalid_string_unchanged(self):
        assert safe_parse_json("not json") == "not json"

    def test_non_string_passthrough(self):
        assert safe_parse_json({"a": 1}) == {"a": 1}
        assert safe_parse_json(42) == 42
        assert safe_parse_json(None) is None


# ---------------------------------------------------------------------------
# safe_parse_python_literal
# ---------------------------------------------------------------------------

class TestSafeParsePythonLiteral:
    def test_dict_passthrough(self):
        d = {"a": 1}
        assert safe_parse_python_literal(d) is d

    def test_python_literal_single_quotes(self):
        # JSON cannot parse single quotes; ast.literal_eval can.
        assert safe_parse_python_literal("{'a': 1}") == {"a": 1}
        assert safe_parse_python_literal("['x', 'y']") == ["x", "y"]

    def test_json_fallback(self):
        # Valid JSON that is also valid python literal
        assert safe_parse_python_literal('{"a": 1}') == {"a": 1}

    def test_unparseable_returns_raw(self):
        assert safe_parse_python_literal("just words") == "just words"

    def test_non_string_non_dict_passthrough(self):
        assert safe_parse_python_literal(7) == 7
        assert safe_parse_python_literal(None) is None


# ---------------------------------------------------------------------------
# parse_iso_timestamp
# ---------------------------------------------------------------------------

class TestParseIsoTimestamp:
    def test_none_and_empty(self):
        assert parse_iso_timestamp(None) is None
        assert parse_iso_timestamp("") is None

    def test_z_suffix_to_offset(self):
        dt = parse_iso_timestamp("2024-01-01T00:00:00Z")
        assert isinstance(dt, datetime)
        assert dt.year == 2024 and dt.month == 1 and dt.day == 1
        assert dt.tzinfo is not None  # Z -> +00:00 -> tz-aware

    def test_plain_iso_naive(self):
        dt = parse_iso_timestamp("2024-06-09T12:30:45")
        assert dt == datetime(2024, 6, 9, 12, 30, 45)

    def test_invalid_returns_none(self):
        assert parse_iso_timestamp("not-a-timestamp") is None
