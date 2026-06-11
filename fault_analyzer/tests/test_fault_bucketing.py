"""Unit tests for the deterministic parts of fault_analyzer.scripts.fault_bucketing.

The FaultBucketingPipeline constructs a FaultEventClassifier (which would create
an Azure LLM client lazily). We patch FaultEventClassifier so no network/config
is touched, then exercise the deterministic span-scanning, metadata-extraction,
temporal-filtering, bucket-creation, detection-recording, and event-placement
logic in code.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from fault_analyzer.schema.data_models import EventClassification, FaultBucket
from fault_analyzer.scripts.fault_bucketing import FaultBucketingPipeline


@pytest.fixture
def pipeline(tmp_path):
    """A pipeline with a stubbed classifier and no real config/LLM."""
    with patch(
        "fault_analyzer.scripts.fault_bucketing.FaultEventClassifier"
    ) as MockCls:
        MockCls.return_value = MagicMock()
        p = FaultBucketingPipeline(
            trace_file_path=str(tmp_path / "trace.json"),
            output_dir=str(tmp_path / "out"),
            config={},
            batch_size=5,
            debug=False,
        )
    return p


# ---------------------------------------------------------------------------
# _is_fault_name_span / _extract_fault_name_from_span (static)
# ---------------------------------------------------------------------------

class TestFaultSpanIdentification:
    def test_is_fault_name_span_positive(self):
        assert FaultBucketingPipeline._is_fault_name_span(
            {"name": "fault: pod-delete"}
        )

    def test_is_fault_name_span_empty_after_prefix(self):
        assert FaultBucketingPipeline._is_fault_name_span({"name": "fault:"}) is False
        assert FaultBucketingPipeline._is_fault_name_span({"name": "fault:   "}) is False

    def test_is_fault_name_span_non_fault(self):
        assert FaultBucketingPipeline._is_fault_name_span({"name": "agent-step"}) is False
        assert FaultBucketingPipeline._is_fault_name_span({}) is False

    def test_is_fault_name_span_non_string(self):
        assert FaultBucketingPipeline._is_fault_name_span({"name": 123}) is False

    def test_extract_fault_name(self):
        assert (
            FaultBucketingPipeline._extract_fault_name_from_span(
                {"name": "fault: disk-fill"}
            )
            == "disk-fill"
        )

    def test_extract_fault_name_strips_whitespace(self):
        assert (
            FaultBucketingPipeline._extract_fault_name_from_span(
                {"name": "fault:   pod-delete  "}
            )
            == "pod-delete"
        )

    def test_extract_fault_name_none_for_non_fault(self):
        assert (
            FaultBucketingPipeline._extract_fault_name_from_span({"name": "other"})
            is None
        )
        assert (
            FaultBucketingPipeline._extract_fault_name_from_span({"name": "fault:"})
            is None
        )


# ---------------------------------------------------------------------------
# _extract_metadata_dict (static)
# ---------------------------------------------------------------------------

class TestExtractMetadataDict:
    def test_dict_metadata(self):
        assert FaultBucketingPipeline._extract_metadata_dict(
            {"metadata": {"a": 1}}
        ) == {"a": 1}

    def test_json_string_metadata(self):
        assert FaultBucketingPipeline._extract_metadata_dict(
            {"metadata": '{"a": 1}'}
        ) == {"a": 1}

    def test_invalid_string_metadata(self):
        assert FaultBucketingPipeline._extract_metadata_dict(
            {"metadata": "not json"}
        ) == {}

    def test_missing_metadata(self):
        assert FaultBucketingPipeline._extract_metadata_dict({}) == {}


# ---------------------------------------------------------------------------
# _extract_injection_metadata (static)
# ---------------------------------------------------------------------------

class TestExtractInjectionMetadata:
    def test_empty_event_has_status_only(self):
        result = FaultBucketingPipeline._extract_injection_metadata({})
        assert result == {"status": "injected"}

    def test_basic_fields(self):
        event = {
            "metadata": {
                "attributes": {
                    "fault.name": "pod-delete",
                    "fault.engine_name": "litmus",
                    "fault.namespace": "litmus",
                    "fault.injection_timestamp": "2024-01-01T00:00:00Z",
                    "fault.injection_end_timestamp": "2024-01-01T00:05:00Z",
                }
            }
        }
        result = FaultBucketingPipeline._extract_injection_metadata(event)
        assert result["name"] == "pod-delete"
        assert result["engine_name"] == "litmus"
        assert result["namespace"] == "litmus"
        assert result["status"] == "injected"
        assert result["injection_timestamp"] == "2024-01-01T00:00:00Z"
        assert result["injection_end_timestamp"] == "2024-01-01T00:05:00Z"

    def test_target_block_degraded_true(self):
        # target_namespace == namespace and no target_label => degraded True
        event = {
            "metadata": {
                "attributes": {
                    "fault.target_namespace": "sock-shop",
                    "fault.namespace": "sock-shop",
                }
            }
        }
        result = FaultBucketingPipeline._extract_injection_metadata(event)
        assert result["target"]["namespace"] == "sock-shop"
        assert result["target"]["degraded"] is True

    def test_target_block_degraded_false_with_label(self):
        event = {
            "metadata": {
                "attributes": {
                    "fault.target_namespace": "sock-shop",
                    "fault.namespace": "sock-shop",
                    "fault.target_label": "app=cart",
                    "fault.target_kind": "DEPLOYMENT",
                }
            }
        }
        result = FaultBucketingPipeline._extract_injection_metadata(event)
        assert result["target"]["label"] == "app=cart"
        assert result["target"]["kind"] == "deployment"  # lowercased
        assert result["target"]["degraded"] is False

    def test_timing_block_int_coercion(self):
        event = {
            "metadata": {
                "attributes": {
                    "fault.timing.total_chaos_duration_sec": "300",
                    "fault.timing.ramp_time_sec": "30",
                    "fault.timing.sequence": "PARALLEL",
                }
            }
        }
        result = FaultBucketingPipeline._extract_injection_metadata(event)
        assert result["timing"]["total_chaos_duration_sec"] == 300
        assert result["timing"]["ramp_time_sec"] == 30
        assert result["timing"]["sequence"] == "parallel"  # lowercased

    def test_injection_phase_space_replaced(self):
        event = {
            "metadata": {
                "attributes": {
                    "fault.injection.verdict": "Pass",
                    "fault.injection.phase": "Chaos Injected",
                    "fault.injection.probe_success_pct": 100,
                }
            }
        }
        result = FaultBucketingPipeline._extract_injection_metadata(event)
        assert result["injection"]["verdict"] == "Pass"
        assert result["injection"]["phase"] == "Chaos_Injected"
        assert result["injection"]["probe_success_pct"] == "100"  # stringified

    def test_workflow_cohort_faults_csv_split(self):
        event = {
            "metadata": {
                "attributes": {
                    "fault.workflow.sequence_mode": "SERIAL",
                    "fault.workflow.cohort_faults": "a, b , c",
                }
            }
        }
        result = FaultBucketingPipeline._extract_injection_metadata(event)
        assert result["workflow"]["sequence_mode"] == "serial"
        assert result["workflow"]["cohort_faults"] == ["a", "b", "c"]

    def test_metadata_as_json_string(self):
        event = {
            "metadata": '{"attributes": {"fault.name": "disk-fill"}}'
        }
        result = FaultBucketingPipeline._extract_injection_metadata(event)
        assert result["name"] == "disk-fill"


# ---------------------------------------------------------------------------
# _sort_events_chronologically (static)
# ---------------------------------------------------------------------------

class TestSortEvents:
    def test_sorted_by_start_time(self):
        events = [
            {"id": "c", "startTime": "2024-01-01T00:00:03Z"},
            {"id": "a", "startTime": "2024-01-01T00:00:01Z"},
            {"id": "b", "startTime": "2024-01-01T00:00:02Z"},
        ]
        out = FaultBucketingPipeline._sort_events_chronologically(events)
        assert [e["id"] for e in out] == ["a", "b", "c"]

    def test_null_start_time_sorts_last(self):
        events = [
            {"id": "no-ts"},
            {"id": "a", "startTime": "2024-01-01T00:00:01Z"},
        ]
        out = FaultBucketingPipeline._sort_events_chronologically(events)
        assert [e["id"] for e in out] == ["a", "no-ts"]


# ---------------------------------------------------------------------------
# _create_event_batches (static)
# ---------------------------------------------------------------------------

class TestCreateEventBatches:
    def test_exact_split(self):
        events = [{"id": str(i)} for i in range(6)]
        batches = FaultBucketingPipeline._create_event_batches(events, 3)
        assert len(batches) == 2
        assert [e["id"] for e in batches[0]] == ["0", "1", "2"]

    def test_remainder_batch(self):
        events = [{"id": str(i)} for i in range(5)]
        batches = FaultBucketingPipeline._create_event_batches(events, 2)
        assert [len(b) for b in batches] == [2, 2, 1]

    def test_empty(self):
        assert FaultBucketingPipeline._create_event_batches([], 3) == []


# ---------------------------------------------------------------------------
# _extract_tokens_from_trace
# ---------------------------------------------------------------------------

class TestExtractTokensFromTrace:
    def test_sums_usage_strings_and_dicts(self, pipeline):
        events = [
            {"id": "1", "usage": '{"input": 100, "output": 20}'},
            {"id": "2", "usage": {"input": 50, "output": 5}},
            {"id": "3"},  # no usage
            {"id": "4", "usage": "garbage"},  # unparseable -> skipped
        ]
        pipeline._extract_tokens_from_trace(events)
        assert pipeline.trace_input_tokens == 150
        assert pipeline.trace_output_tokens == 25

    def test_none_values_treated_as_zero(self, pipeline):
        pipeline._extract_tokens_from_trace(
            [{"id": "1", "usage": {"input": None, "output": None}}]
        )
        assert pipeline.trace_input_tokens == 0
        assert pipeline.trace_output_tokens == 0


# ---------------------------------------------------------------------------
# _extract_agent_metadata
# ---------------------------------------------------------------------------

class TestExtractAgentMetadata:
    def test_extracts_from_input_field(self, pipeline):
        events = [
            {
                "id": "1",
                "name": "init",
                "input": {
                    "agent_id": "agent-7",
                    "agent_name": "ops-agent",
                    "agent_version": "1.0",
                    "experiment_id": "exp-1",
                    "run_id": "run-1",
                },
            }
        ]
        pipeline._extract_agent_metadata(events)
        assert pipeline.agent_id == "agent-7"
        assert pipeline.agent_name == "ops-agent"
        assert pipeline.run_id == "run-1"

    def test_stops_at_fault_span(self, pipeline):
        events = [
            {"id": "1", "name": "fault: pod-delete", "input": {"agent_id": "should-not-read"}},
            {"id": "2", "name": "later", "input": {"agent_id": "later"}},
        ]
        pipeline._extract_agent_metadata(events)
        assert pipeline.agent_id is None

    def test_reads_nested_attributes_and_alt_keys(self, pipeline):
        events = [
            {
                "id": "1",
                "name": "init",
                "metadata": {
                    "attributes": {
                        "agentid": "a1",
                        "experiment.id": "e1",
                        "experiment.run_id": "r1",
                    }
                },
            }
        ]
        pipeline._extract_agent_metadata(events)
        assert pipeline.agent_id == "a1"
        assert pipeline.experiment_id == "e1"
        assert pipeline.run_id == "r1"

    def test_empty_events_no_error(self, pipeline):
        pipeline._extract_agent_metadata([])
        assert pipeline.agent_id is None


# ---------------------------------------------------------------------------
# _extract_ground_truth_from_metadata
# ---------------------------------------------------------------------------

class TestExtractGroundTruth:
    def test_top_level_metadata(self, pipeline):
        event = {"metadata": {"ground_truth": {"sla": {"ttd": 60}}}}
        gt = pipeline._extract_ground_truth_from_metadata(event)
        assert gt == {"sla": {"ttd": 60}}

    def test_nested_attributes(self, pipeline):
        event = {"metadata": {"attributes": {"ground_truth": {"x": 1}}}}
        assert pipeline._extract_ground_truth_from_metadata(event) == {"x": 1}

    def test_from_input_field(self, pipeline):
        event = {"input": {"ground_truth": {"y": 2}}}
        assert pipeline._extract_ground_truth_from_metadata(event) == {"y": 2}

    def test_ground_truth_as_string_parsed(self, pipeline):
        event = {"metadata": {"ground_truth": "{'z': 3}"}}
        assert pipeline._extract_ground_truth_from_metadata(event) == {"z": 3}

    def test_no_ground_truth(self, pipeline):
        assert pipeline._extract_ground_truth_from_metadata({"metadata": {}}) is None


# ---------------------------------------------------------------------------
# _temporally_active_faults
# ---------------------------------------------------------------------------

def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class TestTemporallyActiveFaults:
    def test_none_event_ts_returns_all(self, pipeline):
        faults = {"a": FaultBucket(fault_id="a", fault_name="a")}
        result = pipeline._temporally_active_faults(faults, None)
        assert set(result.keys()) == {"a"}

    def test_no_injection_ts_included_defensively(self, pipeline):
        faults = {"a": FaultBucket(fault_id="a", fault_name="a", injection_timestamp=None)}
        result = pipeline._temporally_active_faults(faults, _ts("2024-01-01T00:00:00Z"))
        assert "a" in result

    def test_open_ended_window_after_start(self, pipeline):
        faults = {
            "a": FaultBucket(
                fault_id="a", fault_name="a",
                injection_timestamp="2024-01-01T00:00:00Z",
            )
        }
        # event after start, no end timestamp => included
        assert "a" in pipeline._temporally_active_faults(
            faults, _ts("2024-01-01T00:10:00Z")
        )
        # event before start => excluded
        assert "a" not in pipeline._temporally_active_faults(
            faults, _ts("2023-12-31T23:00:00Z")
        )

    def test_closed_window_inside_and_outside(self, pipeline):
        faults = {
            "a": FaultBucket(
                fault_id="a", fault_name="a",
                injection_timestamp="2024-01-01T00:00:00Z",
                injection_end_timestamp="2024-01-01T00:05:00Z",
            )
        }
        assert "a" in pipeline._temporally_active_faults(
            faults, _ts("2024-01-01T00:02:00Z")
        )
        assert "a" not in pipeline._temporally_active_faults(
            faults, _ts("2024-01-01T00:10:00Z")
        )

    def test_ramp_widens_window(self, pipeline):
        faults = {
            "a": FaultBucket(
                fault_id="a", fault_name="a",
                injection_timestamp="2024-01-01T00:00:00Z",
                injection_end_timestamp="2024-01-01T00:05:00Z",
                injection_metadata={"timing": {"ramp_time_sec": 60}},
            )
        }
        # 30s before injection start: inside ramp window
        assert "a" in pipeline._temporally_active_faults(
            faults, _ts("2023-12-31T23:59:30Z")
        )
        # ramp excluded -> outside
        assert "a" not in pipeline._temporally_active_faults(
            faults, _ts("2023-12-31T23:59:30Z"), include_ramp=False
        )


# ---------------------------------------------------------------------------
# _create_fault_bucket_from_span
# ---------------------------------------------------------------------------

class TestCreateFaultBucket:
    def test_creates_bucket_with_metadata(self, pipeline):
        event = {
            "name": "fault: pod-delete",
            "startTime": "2024-01-01T00:00:00Z",
            "metadata": {
                "attributes": {
                    "fault.target_label": "app=cart",
                    "fault.target_namespace": "sock-shop",
                    "fault.injection_end_timestamp": "2024-01-01T00:05:00Z",
                }
            },
        }
        pipeline._create_fault_bucket_from_span(event)
        assert "pod-delete" in pipeline.active_faults
        b = pipeline.active_faults["pod-delete"]
        assert b.fault_name == "pod-delete"
        assert b.target_pod == "app=cart"
        assert b.namespace == "sock-shop"
        assert b.injection_timestamp == "2024-01-01T00:00:00Z"
        assert b.injection_end_timestamp == "2024-01-01T00:05:00Z"
        assert b.status == "active"
        assert b.events == []  # injection span not added to events

    def test_duplicate_active_skipped(self, pipeline):
        event = {"name": "fault: pod-delete", "startTime": "2024-01-01T00:00:00Z"}
        pipeline._create_fault_bucket_from_span(event)
        pipeline._create_fault_bucket_from_span(event)
        assert len(pipeline.active_faults) == 1

    def test_new_bucket_after_close_gets_suffix(self, pipeline):
        event = {"name": "fault: pod-delete", "startTime": "2024-01-01T00:00:00Z"}
        pipeline._create_fault_bucket_from_span(event)
        # close it
        pipeline._close_fault("pod-delete", mitigated_at="2024-01-01T00:05:00Z")
        assert "pod-delete" in pipeline.closed_faults
        # second injection of same fault -> new bucket with suffix
        pipeline._create_fault_bucket_from_span(event)
        assert "pod-delete_2" in pipeline.active_faults

    def test_ground_truth_fields_extracted(self, pipeline):
        event = {
            "name": "fault: pod-delete",
            "startTime": "2024-01-01T00:00:00Z",
            "metadata": {
                "ground_truth": {
                    "sla": {"ttd": 60},
                    "ideal_course_of_action": [{"action": "restart"}],
                    "ideal_tool_usage_trajectory": [{"command": "kubectl"}],
                }
            },
        }
        pipeline._create_fault_bucket_from_span(event)
        b = pipeline.active_faults["pod-delete"]
        assert b.sla == {"ttd": 60}
        assert b.ideal_course_of_action == [{"action": "restart"}]
        assert b.ideal_tool_usage_trajectory == [{"command": "kubectl"}]

    def test_empty_fault_name_no_bucket(self, pipeline):
        pipeline._create_fault_bucket_from_span({"name": "fault:"})
        assert pipeline.active_faults == {}


# ---------------------------------------------------------------------------
# _close_fault
# ---------------------------------------------------------------------------

class TestCloseFault:
    def test_moves_active_to_closed(self, pipeline):
        pipeline.active_faults["a"] = FaultBucket(fault_id="a", fault_name="a")
        pipeline._close_fault("a", mitigated_at="2024-01-01T00:05:00Z")
        assert "a" not in pipeline.active_faults
        assert pipeline.closed_faults["a"].status == "closed"
        assert pipeline.closed_faults["a"].mitigated_at == "2024-01-01T00:05:00Z"

    def test_close_unknown_noop(self, pipeline):
        pipeline._close_fault("missing")
        assert pipeline.closed_faults == {}


# ---------------------------------------------------------------------------
# _place_event_in_buckets
# ---------------------------------------------------------------------------

class TestPlaceEvent:
    def test_places_into_related_buckets(self, pipeline):
        pipeline.active_faults["a"] = FaultBucket(fault_id="a", fault_name="a")
        pipeline.active_faults["b"] = FaultBucket(fault_id="b", fault_name="b")
        event = {"id": "e1"}
        cls = EventClassification(event_id="e1", related_faults=["a", "b"])
        pipeline._place_event_in_buckets(event, cls)
        assert pipeline.active_faults["a"].events == [event]
        assert pipeline.active_faults["b"].events == [event]
        assert pipeline.unclassified_events == []

    def test_unmatched_goes_to_unclassified(self, pipeline):
        event = {"id": "e1"}
        cls = EventClassification(event_id="e1", related_faults=["nonexistent"])
        pipeline._place_event_in_buckets(event, cls)
        assert pipeline.unclassified_events == [event]

    def test_empty_related_goes_to_unclassified(self, pipeline):
        event = {"id": "e1"}
        cls = EventClassification(event_id="e1", related_faults=[])
        pipeline._place_event_in_buckets(event, cls)
        assert pipeline.unclassified_events == [event]


# ---------------------------------------------------------------------------
# _record_fault_detection
# ---------------------------------------------------------------------------

class TestRecordFaultDetection:
    def test_updates_matching_bucket(self, pipeline):
        pipeline.active_faults["pod-delete"] = FaultBucket(
            fault_id="pod-delete", fault_name="pod-delete",
            injection_timestamp="2024-01-01T00:00:00Z",
        )
        cls = EventClassification(
            event_id="e1",
            fault_detected="pod-delete",
            detected_fault_severity="critical",
            detected_fault_target_pod="cart",
            detected_fault_namespace="sock-shop",
            detected_fault_signals=["CrashLoopBackOff"],
        )
        pipeline._record_fault_detection(cls, detection_ts="2024-01-01T00:00:30Z")
        b = pipeline.active_faults["pod-delete"]
        assert b.detected_at == "2024-01-01T00:00:30Z"
        assert b.severity == "critical"
        assert b.target_pod == "cart"
        assert b.namespace == "sock-shop"
        assert b.detection_signals == ["CrashLoopBackOff"]

    def test_no_fault_detected_noop(self, pipeline):
        cls = EventClassification(event_id="e1", fault_detected=None)
        pipeline._record_fault_detection(cls, detection_ts="2024-01-01T00:00:30Z")
        assert pipeline.other_detected_faults == []

    def test_unmatched_detection_goes_to_other(self, pipeline):
        cls = EventClassification(
            event_id="e1",
            fault_detected="mystery-fault",
            detected_fault_severity="high",
        )
        pipeline._record_fault_detection(cls, detection_ts="2024-01-01T00:00:30Z")
        assert len(pipeline.other_detected_faults) == 1
        assert pipeline.other_detected_faults[0]["fault_name"] == "mystery-fault"
        assert pipeline.other_detected_faults[0]["detected_at"] == "2024-01-01T00:00:30Z"

    def test_existing_detection_not_overwritten(self, pipeline):
        pipeline.active_faults["a"] = FaultBucket(
            fault_id="a", fault_name="a",
            detected_at="2024-01-01T00:00:01Z",
            severity="low",
        )
        cls = EventClassification(
            event_id="e1", fault_detected="a",
            detected_fault_severity="critical",
        )
        pipeline._record_fault_detection(cls, detection_ts="2024-01-01T00:00:99Z")
        b = pipeline.active_faults["a"]
        # detected_at already set (and != injection_timestamp) -> not overwritten
        assert b.detected_at == "2024-01-01T00:00:01Z"
        # severity already set -> not overwritten
        assert b.severity == "low"


# ---------------------------------------------------------------------------
# _all_agent_metadata_found
# ---------------------------------------------------------------------------

class TestAllAgentMetadataFound:
    def test_false_when_partial(self, pipeline):
        pipeline.agent_id = "x"
        assert pipeline._all_agent_metadata_found() is False

    def test_true_when_all_present(self, pipeline):
        pipeline.agent_id = "a"
        pipeline.agent_name = "b"
        pipeline.agent_version = "c"
        pipeline.experiment_id = "d"
        pipeline.run_id = "e"
        assert pipeline._all_agent_metadata_found() is True


# ---------------------------------------------------------------------------
# _timestamp_fallback_buckets (deprecated; always [])
# ---------------------------------------------------------------------------

def test_timestamp_fallback_buckets_always_empty(pipeline):
    assert pipeline._timestamp_fallback_buckets({"id": "x"}) == []
