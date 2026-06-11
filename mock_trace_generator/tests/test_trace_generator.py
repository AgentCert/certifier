"""Unit tests for mock_trace_generator.scripts.trace_generator.

The LLM client is faked (see conftest.FakeLLMClient); RNG is seeded by the
autouse fixture so generated structure is deterministic across runs.
"""

import hashlib
import json
from datetime import datetime, timezone

import pytest

from mock_trace_generator.schema.data_models import FaultDefinition
from mock_trace_generator.scripts.tools_registry import AVAILABLE_TOOLS
from mock_trace_generator.scripts.trace_generator import MultiFaultTraceGenerator


# --- static / pure helpers ---


class TestStaticHelpers:
    def test_make_trace_id_format(self):
        tid = MultiFaultTraceGenerator._make_trace_id()
        assert len(tid) == 32
        assert all(c in "0123456789abcdef" for c in tid.lower())

    def test_make_span_id_is_uuid(self):
        import uuid

        sid = MultiFaultTraceGenerator._make_span_id()
        assert uuid.UUID(sid)  # parses without error

    def test_ts_format(self):
        dt = datetime(2026, 6, 9, 12, 30, 45, 123456, tzinfo=timezone.utc)
        ts = MultiFaultTraceGenerator._ts(dt)
        assert ts.endswith("Z")
        assert ts == "2026-06-09T12:30:45.123Z"

    def test_generate_experiment_id_deterministic_and_order_independent(self):
        a = [
            FaultDefinition(name="b", description="x"),
            FaultDefinition(name="a", description="y"),
        ]
        b = [
            FaultDefinition(name="a", description="y"),
            FaultDefinition(name="b", description="x"),
        ]
        eid_a = MultiFaultTraceGenerator.generate_experiment_id(a)
        eid_b = MultiFaultTraceGenerator.generate_experiment_id(b)
        assert eid_a == eid_b
        assert len(eid_a) == 24
        expected = hashlib.sha256("a|b".encode()).hexdigest()[:24]
        assert eid_a == expected


class TestSystemPrompt:
    def test_system_prompt_lists_all_tools(self):
        prompt = MultiFaultTraceGenerator.SYSTEM_PROMPT
        for key in AVAILABLE_TOOLS:
            assert key in prompt


class TestConstructor:
    def test_defaults(self):
        gen = MultiFaultTraceGenerator()
        assert gen.llm_client is None
        assert gen.model_name == "gpt-4o"
        assert gen.agent_metadata == {}

    def test_overrides(self):
        gen = MultiFaultTraceGenerator(
            llm_client="client", model_name="gpt-x", agent_metadata={"agent_name": "Bob"}
        )
        assert gen.llm_client == "client"
        assert gen.model_name == "gpt-x"
        assert gen.agent_metadata["agent_name"] == "Bob"


class TestCallLLMStructuredErrors:
    async def test_raises_without_client(self):
        gen = MultiFaultTraceGenerator(llm_client=None)
        from mock_trace_generator.schema.data_models import MultiFaultScenario

        with pytest.raises(RuntimeError, match="LLM client not initialized"):
            await gen._call_llm_structured("prompt", MultiFaultScenario)

    async def test_returns_instance_when_client_yields_model(self, fake_llm):
        gen = MultiFaultTraceGenerator(llm_client=fake_llm)
        from mock_trace_generator.schema.data_models import MultiFaultScenario

        result = await gen._call_llm_structured("prompt", MultiFaultScenario)
        assert isinstance(result, MultiFaultScenario)
        assert fake_llm.calls[0]["model_name"] == "gpt-4o"

    async def test_parses_dict_response(self, faults):
        """Client returns a dict (not the model) -> validated via model_validate."""
        from mock_trace_generator.schema.data_models import ClusterScanResult

        payload = {
            "pods_list_output": "p",
            "events_output": "e",
            "nodes_top_output": "n",
            "initial_anomalies": ["a"],
            "agent_reasoning": "r",
        }

        class DictClient:
            async def with_structured_output(self, **kwargs):
                return payload, 0.0

        gen = MultiFaultTraceGenerator(llm_client=DictClient())
        result = await gen._call_llm_structured("p", ClusterScanResult)
        assert isinstance(result, ClusterScanResult)
        assert result.pods_list_output == "p"

    async def test_parses_raw_json_string_in_response_key(self):
        from mock_trace_generator.schema.data_models import ClusterScanResult

        inner = {
            "pods_list_output": "p",
            "events_output": "e",
            "nodes_top_output": "n",
            "initial_anomalies": [],
            "agent_reasoning": "r",
        }
        raw_str = "```json\n" + json.dumps(inner) + "\n```"

        class StrClient:
            async def with_structured_output(self, **kwargs):
                return {"response": raw_str}, 0.0

        gen = MultiFaultTraceGenerator(llm_client=StrClient())
        result = await gen._call_llm_structured("p", ClusterScanResult)
        assert isinstance(result, ClusterScanResult)

    async def test_recovers_after_validation_failure_then_valid_dict(self):
        from mock_trace_generator.schema.data_models import ClusterScanResult

        good = {
            "pods_list_output": "p",
            "events_output": "e",
            "nodes_top_output": "n",
            "initial_anomalies": [],
            "agent_reasoning": "r",
        }
        responses = iter([
            {"missing": "fields"},  # dict that fails model_validate
            good,                   # valid on retry
        ])

        class FlakyClient:
            async def with_structured_output(self, **kwargs):
                return next(responses), 0.0

        gen = MultiFaultTraceGenerator(llm_client=FlakyClient())
        result = await gen._call_llm_structured("p", ClusterScanResult, max_retries=2)
        assert isinstance(result, ClusterScanResult)
        assert result.pods_list_output == "p"

    async def test_raises_after_retries_on_unparseable(self):
        from mock_trace_generator.schema.data_models import ClusterScanResult

        class BadClient:
            async def with_structured_output(self, **kwargs):
                return {"response": "not json at all {{{"}, 0.0

        gen = MultiFaultTraceGenerator(llm_client=BadClient())
        with pytest.raises(RuntimeError, match="Failed to get valid structured output"):
            await gen._call_llm_structured("p", ClusterScanResult, max_retries=1)


# --- span builders ---


def _decode(span):
    """Return parsed metadata + input dicts from a span."""
    return json.loads(span["metadata"]), json.loads(span["input"])


class TestSpanBuilders:
    def test_build_span_shape_and_short_id(self):
        gen = MultiFaultTraceGenerator()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        span = gen._build_span(
            trace_id="t1",
            parent_span_id=None,
            span_type="SPAN",
            name="thing",
            start_time=start,
            end_time=start,
            input_data={"a": 1},
            output_data={"b": 2},
            metadata={"m": 3},
            experiment_id="exp",
            run_id="run",
        )
        assert span["traceId"] == "t1"
        assert span["type"] == "SPAN"
        # name suffixed with first 8 chars of id
        assert span["name"].startswith("thing (")
        assert span["name"].endswith(")")
        assert span["id"][:8] in span["name"]
        assert span["depth"] == 0
        assert span["parentObservationId"] is None
        meta = json.loads(span["metadata"])
        assert meta["experiment_id"] == "exp"
        assert meta["run_id"] == "run"
        # dict output is json-encoded
        assert json.loads(span["output"]) == {"b": 2}

    def test_build_span_depth_one_with_parent(self):
        gen = MultiFaultTraceGenerator()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        span = gen._build_span(
            trace_id="t",
            parent_span_id="parent-id",
            span_type="SPAN",
            name="child",
            start_time=start,
            end_time=None,
            input_data={},
            output_data="raw string output",
            metadata={},
        )
        assert span["depth"] == 1
        assert span["parentObservationId"] == "parent-id"
        assert span["endTime"] is None
        # non-dict output passed through as-is
        assert span["output"] == "raw string output"

    def test_build_tool_span_returns_two_spans(self):
        from mock_trace_generator.tests.conftest import _tool_call

        gen = MultiFaultTraceGenerator()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        spans = gen._build_tool_span(
            trace_id="t",
            parent_span_id="p",
            tool_call=_tool_call("k8s_pods_log"),
            start_time=start,
            duration_seconds=2.0,
            agent_id="agent-1",
        )
        assert len(spans) == 2
        tool_span, reasoning_span = spans
        assert tool_span["type"] == "SPAN"
        assert reasoning_span["type"] == "GENERATION"
        assert tool_span["name"].startswith("tool_call:k8s_pods_log")
        assert reasoning_span["name"].startswith("tool_reasoning:k8s_pods_log")
        tmeta = json.loads(tool_span["metadata"])
        assert tmeta["tool_category"] == "kubernetes"
        assert tmeta["llm_used"] is False
        rmeta = json.loads(reasoning_span["metadata"])
        assert rmeta["llm_used"] is True
        assert rmeta["tokens_consumed"] > 0  # reasoning text present

    def test_build_tool_span_unknown_tool_category(self):
        from mock_trace_generator.tests.conftest import _tool_call

        gen = MultiFaultTraceGenerator()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        tc = _tool_call("not_a_real_tool", reasoning="")
        spans = gen._build_tool_span(
            trace_id="t",
            parent_span_id="p",
            tool_call=tc,
            start_time=start,
            duration_seconds=1.0,
            agent_id="a",
        )
        tmeta = json.loads(spans[0]["metadata"])
        assert tmeta["tool_category"] == "unknown"
        rmeta = json.loads(spans[1]["metadata"])
        # no reasoning text => 0 tokens
        assert rmeta["tokens_consumed"] == 0

    def test_agent_onboarding_span_defaults(self, faults):
        gen = MultiFaultTraceGenerator()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        span = gen._build_agent_onboarding_span(
            trace_id="t",
            agent_id="agent-1",
            start_time=start,
            faults=faults,
            experiment_id="e",
            run_id="r",
        )
        meta, inp = _decode(span)
        assert span["name"].startswith("agent_onboarding")
        assert meta["agent_name"] == "ITOps Autonomous Agent"
        assert inp["num_faults"] == 2
        assert inp["num_tools"] == len(AVAILABLE_TOOLS)
        assert inp["scenario_type"] == "multi_fault"
        assert [f["name"] for f in inp["faults"]] == ["pod-delete", "disk-fill"]

    def test_agent_onboarding_span_metadata_override(self, faults):
        gen = MultiFaultTraceGenerator(
            agent_metadata={"agent_name": "Custom", "extra_field": "kept"}
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        span = gen._build_agent_onboarding_span(
            trace_id="t", agent_id="a", start_time=start, faults=faults
        )
        meta, inp = _decode(span)
        assert inp["agent_name"] == "Custom"
        # custom non-reserved keys are merged into metadata
        assert meta["extra_field"] == "kept"

    def test_fault_injection_spans_one_per_fault(self, faults, fake_llm):
        from mock_trace_generator.tests.conftest import make_single_fault_detail
        from mock_trace_generator.schema.data_models import MultiFaultScenario

        scenario = MultiFaultScenario(
            cluster_name="c",
            affected_namespaces=["default"],
            fault_scenarios=[make_single_fault_detail(f.name) for f in faults],
            cross_fault_interactions=[],
            overall_severity="high",
            triage_order=[f.name for f in faults],
        )
        gen = MultiFaultTraceGenerator()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        spans = gen._build_fault_injection_spans(
            trace_id="t",
            faults=faults,
            scenario=scenario,
            start_time=start,
            experiment_id="e",
            run_id="r",
        )
        assert len(spans) == 2
        for span in spans:
            assert span["type"] == "FAULT_DATA"
            meta, inp = _decode(span)
            assert meta["action"] == "fault_injection"
            assert "ground_truth" in inp
            assert inp["ground_truth"]["fault_description"]  # mapped from FaultDefinition
            assert json.loads(span["output"]) == {"status": "injected"}


# --- full trace assembly ---


class TestGenerateTrace:
    async def test_generates_expected_span_types_and_invariants(self, faults, fake_llm):
        gen = MultiFaultTraceGenerator(llm_client=fake_llm)
        spans = await gen.generate_trace(
            faults=faults, agent_id="agent-1", experiment_id="exp-1", run_id="run-1"
        )
        assert isinstance(spans, list)
        assert len(spans) > 0

        names = [s["name"] for s in spans]
        # phase markers present
        assert any(n.startswith("agent_onboarding") for n in names)
        assert any(n.startswith("success_confirmed") for n in names)
        assert any(n.startswith("triage_reasoning") for n in names)
        assert any(n.startswith("final_stability_check") for n in names)
        assert any(n.startswith("cluster_scan_reasoning") for n in names)

        # all spans share the same trace id, experiment_id, run_id
        trace_ids = {s["traceId"] for s in spans}
        assert len(trace_ids) == 1
        for s in spans:
            meta = json.loads(s["metadata"])
            assert meta["experiment_id"] == "exp-1"
            assert meta["run_id"] == "run-1"

        # one FAULT_DATA span and one fault_detected span per fault
        fault_data = [s for s in spans if s["type"] == "FAULT_DATA"]
        assert len(fault_data) == len(faults)
        detected = [s for s in spans if s["name"].startswith("fault_detected")]
        assert len(detected) == len(faults)

        # per-fault lifecycle spans (investigate/remediate/verify/confirm) per fault
        for prefix in ("investigate:", "remediate:", "verify:", "confirm:"):
            matches = [n for n in names if n.startswith(prefix)]
            assert len(matches) == len(faults), prefix

    async def test_span_ids_unique(self, faults, fake_llm):
        gen = MultiFaultTraceGenerator(llm_client=fake_llm)
        spans = await gen.generate_trace(faults=faults)
        ids = [s["id"] for s in spans]
        assert len(ids) == len(set(ids))

    async def test_every_span_input_metadata_is_valid_json(self, faults, fake_llm):
        gen = MultiFaultTraceGenerator(llm_client=fake_llm)
        spans = await gen.generate_trace(faults=faults)
        for s in spans:
            json.loads(s["input"])
            json.loads(s["metadata"])
            assert s["startTime"].endswith("Z")

    async def test_auto_generates_ids_when_blank(self, faults, fake_llm):
        gen = MultiFaultTraceGenerator(llm_client=fake_llm)
        spans = await gen.generate_trace(faults=faults)
        meta = json.loads(spans[0]["metadata"])
        # experiment_id defaults to deterministic hash
        assert meta["experiment_id"] == MultiFaultTraceGenerator.generate_experiment_id(
            faults
        )
        assert meta["run_id"]  # non-empty generated uuid

    async def test_tool_spans_have_parent(self, faults, fake_llm):
        gen = MultiFaultTraceGenerator(llm_client=fake_llm)
        spans = await gen.generate_trace(faults=faults)
        tool_calls = [s for s in spans if s["name"].startswith("tool_call:")]
        # scan tool calls (pods_list/events/nodes_top) have no parent;
        # investigation/remediation/verify/final tool calls do.
        parented = [s for s in tool_calls if s["parentObservationId"]]
        assert len(parented) > 0
        for s in parented:
            assert s["depth"] == 1


    async def test_triage_fault_not_in_scenario_is_skipped(self, faults, fake_llm):
        from mock_trace_generator.schema.data_models import (
            FaultPriority,
            TriageDecision,
        )

        real_build = fake_llm._build

        def patched_build(output_format):
            result = real_build(output_format)
            if isinstance(result, TriageDecision):
                # Append a phantom fault that has no matching scenario detail.
                result.prioritized_faults.append(
                    FaultPriority(
                        fault_name="ghost-fault",
                        priority=99,
                        severity="low",
                        reason="phantom",
                        blocks_other_faults=False,
                    )
                )
            return result

        fake_llm._build = patched_build

        gen = MultiFaultTraceGenerator(llm_client=fake_llm)
        spans = await gen.generate_trace(faults=faults)
        names = [s["name"] for s in spans]
        # ghost-fault must not produce investigate/remediate/etc. spans
        assert not any("ghost-fault" in n for n in names)
        # the two real faults still produce their lifecycle spans
        for prefix in ("investigate:", "remediate:", "verify:", "confirm:"):
            assert len([n for n in names if n.startswith(prefix)]) == len(faults)


class TestGenerateAndSave:
    async def test_writes_file_with_spans(self, faults, fake_llm, tmp_path):
        gen = MultiFaultTraceGenerator(llm_client=fake_llm)
        out = tmp_path / "traces"
        filepath = await gen.generate_and_save(
            faults=faults, output_dir=str(out), run_id="run-xyz"
        )
        assert filepath.exists()
        assert filepath.name == "trace-multi_fault_run-xyz.json"
        data = json.loads(filepath.read_text(encoding="utf-8"))
        assert isinstance(data, list) and len(data) > 0
        assert data[0]["name"].startswith("agent_onboarding")

    async def test_creates_output_dir(self, faults, fake_llm, tmp_path):
        gen = MultiFaultTraceGenerator(llm_client=fake_llm)
        out = tmp_path / "nested" / "deeper"
        assert not out.exists()
        filepath = await gen.generate_and_save(faults=faults, output_dir=str(out))
        assert out.exists()
        assert filepath.parent == out
