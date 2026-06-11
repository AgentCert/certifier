"""Unit tests for metrics_extractor.scripts.combined_judge.

Pure helpers (build_trajectory, _truncate, _mean, _parse) are tested directly.
The LLM-driven judge_combined / _judge_step paths are tested with an AsyncMock
client so no Azure/network calls occur — verifying the deterministic aggregation
math over mocked per-step judgments.
"""

from unittest.mock import AsyncMock

import pytest

from metrics_extractor.schema.metrics_model import CombinedStepJudgment
from metrics_extractor.scripts.combined_judge import (
    _mean,
    _parse,
    _truncate,
    build_trajectory,
    judge_combined,
)


# ---------------------------------------------------------------------------
# _parse
# ---------------------------------------------------------------------------

class TestParse:
    def test_parses_json_string(self):
        assert _parse('{"a": 1}') == {"a": 1}

    def test_returns_invalid_string(self):
        assert _parse("not json") == "not json"

    def test_passthrough_non_string(self):
        assert _parse({"a": 1}) == {"a": 1}
        assert _parse(5) == 5


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------

class TestTruncate:
    def test_short_string_unchanged(self):
        assert _truncate("hello", max_chars=100) == "hello"

    def test_long_string_truncated_with_ellipsis(self):
        out = _truncate("x" * 50, max_chars=10)
        assert out == "x" * 10 + "…"

    def test_dict_serialized(self):
        out = _truncate({"a": 1}, max_chars=1000)
        assert '"a": 1' in out


# ---------------------------------------------------------------------------
# _mean
# ---------------------------------------------------------------------------

class TestMean:
    def test_empty_is_zero(self):
        assert _mean([]) == 0.0

    def test_rounded_to_three(self):
        assert _mean([1.0, 2.0]) == pytest.approx(1.5)
        assert _mean([1, 1, 1, 2]) == pytest.approx(1.25)


# ---------------------------------------------------------------------------
# build_trajectory
# ---------------------------------------------------------------------------

class TestBuildTrajectory:
    def test_extracts_steps_with_content(self):
        trace = {
            "events": [
                {"output": {"content": "I see a crash"}, "input": {}},
                {"output": {"content": ""}},  # empty content -> skipped
                {"output": "not a dict"},  # non-dict output -> skipped
            ]
        }
        steps = build_trajectory(trace)
        assert len(steps) == 1
        assert steps[0]["step_index"] == 0
        assert steps[0]["content"] == "I see a crash"

    def test_captures_tool_responses(self):
        trace = {
            "events": [
                {
                    "output": {"content": "analysis"},
                    "input": {
                        "messages": [
                            {"role": "tool", "name": "get_pods", "content": "pod list"},
                            {"role": "user", "content": "ignored"},
                        ]
                    },
                }
            ]
        }
        steps = build_trajectory(trace)
        assert steps[0]["tool_responses"] == [
            {"tool_name": "get_pods", "response": "pod list"}
        ]

    def test_json_string_content_parsed(self):
        trace = {"events": [{"output": '{"content": "{\\"a\\": 1}"}'}]}
        steps = build_trajectory(trace)
        assert steps[0]["content"] == {"a": 1}

    def test_no_events(self):
        assert build_trajectory({}) == []
        assert build_trajectory({"events": []}) == []


# ---------------------------------------------------------------------------
# judge_combined (LLM-driven, mocked client)
# ---------------------------------------------------------------------------

class TestJudgeCombined:
    async def test_non_dict_trace_returns_empty_response(self):
        client = AsyncMock()
        resp = await judge_combined(client, "not a dict")
        assert resp.hallucination_count == 0
        assert resp.total_response_count == 0
        client.with_structured_output.assert_not_called()

    async def test_no_steps_returns_empty_response(self):
        client = AsyncMock()
        resp = await judge_combined(client, {"events": []})
        assert resp.total_response_count == 0
        client.with_structured_output.assert_not_called()

    async def test_aggregates_mocked_step_judgments(self):
        trace = {
            "events": [
                {"output": {"content": "step one claim"}, "input": {}},
                {"output": {"content": "step two claim"}, "input": {}},
            ]
        }

        judgments = [
            CombinedStepJudgment(
                step_index=0,
                ungrounded_count=1,
                ungrounded_external_count=1,
                fabricated_tool_call_count=0,
                trajectory_deviation_count=0,
                ignored_error_count=0,
                non_operational_count=1,
                total_claims=5,
                logical_coherence=0.8,
                diagnostic_depth=0.6,
                tool_usage_relevance=0.5,
                explanation_clarity=0.7,
                composite=0.65,
                hallucination_summary="s1",
                reasoning_notes="r1",
            ),
            CombinedStepJudgment(
                step_index=1,
                ungrounded_count=0,
                ungrounded_external_count=0,
                fabricated_tool_call_count=2,
                trajectory_deviation_count=1,
                ignored_error_count=1,
                non_operational_count=0,
                total_claims=3,
                logical_coherence=0.4,
                diagnostic_depth=0.4,
                tool_usage_relevance=0.5,
                explanation_clarity=0.3,
                composite=0.35,
                hallucination_summary="s2",
                reasoning_notes="r2",
            ),
        ]

        client = AsyncMock()
        # with_structured_output returns (result, usage); one call per step.
        client.with_structured_output.side_effect = [
            (judgments[0], {}),
            (judgments[1], {}),
        ]

        resp = await judge_combined(client, trace, model="gpt-4o")

        # hallucination_count = ungrounded_op(1) + ext(1) + fab(2) + traj(1) + ignored(1) = 6
        assert resp.hallucination_count == 6
        assert resp.total_response_count == 8  # 5 + 3
        assert resp.breakdown["ungrounded_external"] == 1
        assert resp.breakdown["fabricated_tool_calls"] == 2
        assert resp.breakdown["non_operational"] == 1  # excluded from hallucination_count
        # means over scored steps (both have composite>0): composite (0.65+0.35)/2 = 0.5
        assert resp.mean_composite == pytest.approx(0.5)
        assert resp.mean_logical_coherence == pytest.approx(0.6)
        assert "s1" in resp.hallucination_notes and "s2" in resp.hallucination_notes
        assert "r1" in resp.overall_reasoning_notes

    async def test_step_failure_yields_zero_judgment(self):
        trace = {"events": [{"output": {"content": "a claim"}, "input": {}}]}
        client = AsyncMock()
        client.with_structured_output.side_effect = RuntimeError("LLM down")
        resp = await judge_combined(client, trace)
        # failed step -> zero-valued judgment with tool_usage_relevance=0.5
        assert resp.hallucination_count == 0
        assert resp.total_response_count == 0
