"""Unit tests for metrics_extractor.scripts.span_aggregator.

QuantitativeAggregator and QualitativeAggregator perform all numeric
aggregation in pure Python (no LLM). These tests cover timestamp parsing,
bucket-metadata extraction, deterministic token/tool extraction, the regex
PII pre-scan, the full aggregate() flow, and qualitative count aggregation.
"""

from datetime import datetime

import pytest

from metrics_extractor.scripts.span_aggregator import (
    QualitativeAggregator,
    QuantitativeAggregator,
)


@pytest.fixture
def agg():
    return QuantitativeAggregator()


# ---------------------------------------------------------------------------
# _parse_timestamp
# ---------------------------------------------------------------------------

class TestParseTimestamp:
    def test_empty_returns_none(self):
        assert QuantitativeAggregator._parse_timestamp("") is None

    def test_z_suffix_made_naive_utc(self):
        dt = QuantitativeAggregator._parse_timestamp("2024-01-01T00:00:00Z")
        assert dt == datetime(2024, 1, 1, 0, 0, 0)
        assert dt.tzinfo is None  # always tz-naive

    def test_offset_converted_to_utc_naive(self):
        # +02:00 -> subtract 2h to UTC, then drop tzinfo
        dt = QuantitativeAggregator._parse_timestamp("2024-01-01T02:00:00+02:00")
        assert dt == datetime(2024, 1, 1, 0, 0, 0)
        assert dt.tzinfo is None

    def test_invalid_returns_none(self):
        assert QuantitativeAggregator._parse_timestamp("garbage") is None


# ---------------------------------------------------------------------------
# extract_from_bucket_metadata
# ---------------------------------------------------------------------------

class TestExtractFromBucketMetadata:
    def test_none_returns_empty(self):
        assert QuantitativeAggregator.extract_from_bucket_metadata(None) == {}

    def test_maps_known_keys(self):
        meta = {
            "agent_name": "ops",
            "agent_id": "a1",
            "agent_version": "1.0",
            "experiment_id": "e1",
            "run_id": "r1",
            "injection_timestamp": "2024-01-01T00:00:00Z",
            "fault_name": "pod-delete",
            "severity": "critical",
            "target_pod": "cart",
            "namespace": "sock-shop",
        }
        out = QuantitativeAggregator.extract_from_bucket_metadata(meta)
        assert out["agent_name"] == "ops"
        assert out["fault_injection_time"] == "2024-01-01T00:00:00Z"
        assert out["injected_fault_name"] == "pod-delete"
        assert out["injected_fault_category"] == "critical"
        assert out["fault_target_service"] == "cart"
        assert out["fault_namespace"] == "sock-shop"

    def test_detected_at_not_extracted(self):
        # detected_at / mitigated_at deliberately ignored
        out = QuantitativeAggregator.extract_from_bucket_metadata(
            {"detected_at": "x", "mitigated_at": "y", "agent_id": "a"}
        )
        assert "agent_fault_detection_time" not in out
        assert "agent_fault_mitigation_time" not in out
        assert out == {"agent_id": "a"}


# ---------------------------------------------------------------------------
# find_events_by_timestamp
# ---------------------------------------------------------------------------

class TestFindEventsByTimestamp:
    def test_matches(self):
        events = [
            {"id": "1", "startTime": "t1"},
            {"id": "2", "startTime": "t2"},
            {"id": "3", "startTime": "t1"},
        ]
        out = QuantitativeAggregator.find_events_by_timestamp("t1", events, "startTime")
        assert [e["id"] for e in out] == ["1", "3"]

    def test_empty_inputs(self):
        assert QuantitativeAggregator.find_events_by_timestamp("", [], "startTime") == []
        assert QuantitativeAggregator.find_events_by_timestamp("t1", [], "startTime") == []


# ---------------------------------------------------------------------------
# extract_token_and_tool_metrics
# ---------------------------------------------------------------------------

class TestExtractTokenAndToolMetrics:
    def test_token_sum_with_cache_read(self):
        spans = [
            {
                "usage": {"input": 100, "output": 20},
                "usageDetails": {"cache_read_input_tokens": 50},
            },
            {"usage": {"input": 10, "output": 5}},
        ]
        out = QuantitativeAggregator.extract_token_and_tool_metrics(spans)
        # input = (100 + 50) + (10 + 0) = 160; output = 25
        assert out["input_tokens"] == 160
        assert out["output_tokens"] == 25

    def test_usage_as_json_string(self):
        spans = [{"usage": '{"input": 7, "output": 3}'}]
        out = QuantitativeAggregator.extract_token_and_tool_metrics(spans)
        assert out["input_tokens"] == 7
        assert out["output_tokens"] == 3

    def test_tool_calls_extracted_and_deduped(self):
        spans = [
            {
                "startTime": "t1",
                "output": {
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "get_pods", "arguments": '{"ns": "x"}'}},
                        {"id": "c2", "function": {"name": "logs", "arguments": {"pod": "p"}}},
                    ]
                },
            },
            {
                "startTime": "t2",
                "output": {
                    "tool_calls": [
                        # duplicate id c1 -> skipped
                        {"id": "c1", "function": {"name": "get_pods", "arguments": "{}"}},
                    ]
                },
            },
        ]
        out = QuantitativeAggregator.extract_token_and_tool_metrics(spans)
        names = [tc["tool_name"] for tc in out["tool_calls"]]
        assert names == ["get_pods", "logs"]
        # arguments parsed from JSON string into dict
        assert out["tool_calls"][0]["arguments"] == {"ns": "x"}
        assert out["tool_calls"][0]["call_id"] == "c1"
        assert out["tool_calls"][0]["timestamp"] == "t1"

    def test_unparseable_arguments_kept_raw(self):
        spans = [
            {
                "output": {
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "x", "arguments": "not-json"}}
                    ]
                }
            }
        ]
        out = QuantitativeAggregator.extract_token_and_tool_metrics(spans)
        assert out["tool_calls"][0]["arguments"] == {"raw": "not-json"}

    def test_no_usage_or_output(self):
        out = QuantitativeAggregator.extract_token_and_tool_metrics([{"id": "x"}])
        assert out == {"input_tokens": 0, "output_tokens": 0, "tool_calls": []}


# ---------------------------------------------------------------------------
# prescan_spans_for_sensitive_data
# ---------------------------------------------------------------------------

class TestPrescanSensitiveData:
    def test_clean_spans(self):
        out = QuantitativeAggregator.prescan_spans_for_sensitive_data(
            [{"output": "all healthy"}]
        )
        assert out == {"pii_detected": False, "pii_instance_count": 0}

    def test_detects_aws_key(self):
        spans = [{"output": "key AKIAIOSFODNN7EXAMPLE here"}]
        out = QuantitativeAggregator.prescan_spans_for_sensitive_data(spans)
        assert out["pii_detected"] is True
        assert out["pii_instance_count"] >= 1

    def test_detects_password_kv_and_secret_manifest(self):
        spans = [{"output": "password: hunter2supersecret"}, {"output": "kind: Secret"}]
        out = QuantitativeAggregator.prescan_spans_for_sensitive_data(spans)
        assert out["pii_detected"] is True
        assert out["pii_instance_count"] >= 2

    def test_detects_bearer_token(self):
        spans = [{"output": "Authorization: Bearer abc123XYZdef456"}]
        out = QuantitativeAggregator.prescan_spans_for_sensitive_data(spans)
        assert out["pii_detected"] is True


# ---------------------------------------------------------------------------
# QuantitativeAggregator.aggregate
# ---------------------------------------------------------------------------

class TestAggregate:
    def test_ttd_ttm_computed_from_timestamps(self, agg):
        bucket_meta = {"injection_timestamp": "2024-01-01T00:00:00Z"}
        span_times = {
            "agent_fault_detection_time": "2024-01-01T00:00:30Z",
            "agent_fault_mitigation_time": "2024-01-01T00:02:00Z",
        }
        out = agg.aggregate(
            partial_metrics=[],
            total_spans=10,
            span_times=span_times,
            bucket_metadata=bucket_meta,
        )
        assert out["time_to_detect"] == pytest.approx(30.0)
        assert out["time_to_mitigate"] == pytest.approx(120.0)
        assert out["detection_success"] == 1
        assert out["trajectory_steps"] == 10

    def test_detection_success_zero_when_no_detection(self, agg):
        out = agg.aggregate(
            partial_metrics=[], total_spans=3, span_times={}, bucket_metadata={}
        )
        assert out["detection_success"] == 0

    def test_fault_detected_picks_longest(self, agg):
        partials = [
            {"fault_detected": "pod down"},
            {"fault_detected": "pod-delete on cart service detected"},
            {"fault_detected": "Unknown"},
        ]
        out = agg.aggregate(partials, total_spans=1, span_times={}, bucket_metadata={})
        assert out["fault_detected"] == "pod-delete on cart service detected"

    def test_fault_detected_unknown_when_all_trivial(self, agg):
        out = agg.aggregate(
            [{"fault_detected": "Unknown"}, {}], total_spans=1,
            span_times={}, bucket_metadata={},
        )
        assert out["fault_detected"] == "Unknown"

    def test_summable_security_fields(self, agg):
        partials = [
            {"sensitive_data_exposure_count": 2, "adversarial_input_count": 1},
            {"sensitive_data_exposure_count": 3},
        ]
        out = agg.aggregate(partials, total_spans=1, span_times={}, bucket_metadata={})
        assert out["sensitive_data_exposure_count"] == 5
        assert out["adversarial_input_count"] == 1

    def test_first_non_null_text_fields(self, agg):
        partials = [
            {"injected_fault_name": None},
            {"injected_fault_name": "disk-fill"},
            {"injected_fault_name": "ignored-later"},
        ]
        out = agg.aggregate(partials, total_spans=1, span_times={}, bucket_metadata={})
        assert out["injected_fault_name"] == "disk-fill"

    def test_tool_selection_accuracy_ratio(self, agg):
        partials = [
            {"correct_tool_selections": 3, "total_tool_selections": 4},
            {"correct_tool_selections": 1, "total_tool_selections": 4},
        ]
        out = agg.aggregate(partials, total_spans=1, span_times={}, bucket_metadata={})
        # (3+1)/(4+4) = 0.5
        assert out["tool_selection_accuracy"] == pytest.approx(0.5)

    def test_span_metrics_override_tokens(self, agg):
        agg._span_metrics = {
            "input_tokens": 500,
            "output_tokens": 100,
            "tool_calls": [{"tool_name": "x"}],
        }
        out = agg.aggregate(
            [{"input_tokens": 1, "output_tokens": 1}], total_spans=1,
            span_times={}, bucket_metadata={},
        )
        assert out["input_tokens"] == 500
        assert out["output_tokens"] == 100
        assert out["tool_calls"] == [{"tool_name": "x"}]

    def test_token_fallback_from_batches(self, agg):
        # No _span_metrics set -> fall back to LLM batch sums
        out = agg.aggregate(
            [{"input_tokens": 10, "output_tokens": 2}, {"input_tokens": 5, "output_tokens": 1}],
            total_spans=1, span_times={}, bucket_metadata={},
        )
        assert out["input_tokens"] == 15
        assert out["output_tokens"] == 3

    def test_pii_prescan_sets_detected_and_floor_count(self, agg):
        agg._prescan_result = {"pii_detected": True, "pii_instance_count": 4}
        out = agg.aggregate(
            [{"sensitive_data_exposure_count": 1, "personal_pii_detected": False}],
            total_spans=1, span_times={}, bucket_metadata={},
        )
        assert out["personal_pii_detected"] is True
        # floor is max(existing=1, prescan=4)
        assert out["sensitive_data_exposure_count"] == 4

    def test_personal_pii_none_when_uncertain(self, agg):
        out = agg.aggregate(
            [{"personal_pii_detected": None}], total_spans=1,
            span_times={}, bucket_metadata={},
        )
        assert out["personal_pii_detected"] is None

    def test_personal_pii_false_when_all_clean(self, agg):
        out = agg.aggregate(
            [{"personal_pii_detected": False}], total_spans=1,
            span_times={}, bucket_metadata={},
        )
        assert out["personal_pii_detected"] is False


# ---------------------------------------------------------------------------
# QualitativeAggregator.aggregate
# ---------------------------------------------------------------------------

class TestQualitativeAggregator:
    def test_hallucination_score_from_counts(self):
        qa = QualitativeAggregator()
        out = qa.aggregate([
            {"hallucination_count": 1, "total_response_count": 4},
            {"hallucination_count": 1, "total_response_count": 4},
        ])
        # (1+1)/(4+4) = 0.25
        assert out["hallucination_score"] == pytest.approx(0.25)

    def test_no_score_when_zero_responses(self):
        qa = QualitativeAggregator()
        out = qa.aggregate([{"hallucination_count": 0, "total_response_count": 0}])
        assert "hallucination_score" not in out

    def test_breakdown_fields_summed(self):
        qa = QualitativeAggregator()
        out = qa.aggregate([
            {"hallucination_ungrounded_external_count": 1, "hallucination_fabricated_tool_count": 2},
            {"hallucination_ungrounded_external_count": 3},
        ])
        assert out["hallucination_ungrounded_external_count"] == 4
        assert out["hallucination_fabricated_tool_count"] == 2

    def test_non_numeric_counts_skipped(self):
        qa = QualitativeAggregator()
        out = qa.aggregate([
            {"hallucination_count": "bad", "total_response_count": 5},
            {"hallucination_count": 2, "total_response_count": 5},
        ])
        # only the numeric 2 counted: 2/10 = 0.2
        assert out["hallucination_score"] == pytest.approx(0.2)

    def test_empty_observations(self):
        qa = QualitativeAggregator()
        assert qa.aggregate([]) == {}
