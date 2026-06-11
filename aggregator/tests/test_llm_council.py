"""
Unit tests for aggregator.scripts.llm_council.

The LLM client is fully mocked with AsyncMock — NO network. Covers:
  * _collect_narratives                 — narrative extraction helper
  * config / member resolution          — __init__ priority order
  * get_council_model_info              — deployment metadata fan-out
  * _model_for_judge                    — index cycling
  * _run_single_judge                   — dict vs non-dict normalization
  * _run_meta_judge                     — success + exception fallback
  * synthesize_textual_metric           — k-judge + meta orchestration, token sum
  * synthesize_list_metric              — picks judge with most items
  * synthesize_limitations_and_recommendations — success + exception fallback
  * compute_textual_aggregates          — defaults + per-metric synthesis
"""

import pytest
from unittest.mock import AsyncMock

from aggregator.scripts import llm_council as lc
from aggregator.scripts.llm_council import LLMCouncil, _collect_narratives


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def _mock_client(return_value=None, side_effect=None):
    client = AsyncMock()
    if side_effect is not None:
        client.call_llm = AsyncMock(side_effect=side_effect)
    else:
        client.call_llm = AsyncMock(return_value=return_value)
    client.config = {}
    return client


def _council(client, **kwargs):
    return LLMCouncil(client, **kwargs)


# ---------------------------------------------------------------------------
# _collect_narratives
# ---------------------------------------------------------------------------

class TestCollectNarratives:
    def test_collects_non_empty_strings(self):
        docs = [
            {"qualitative": {"agent_summary": "good"}},
            {"qualitative": {"agent_summary": "  spaced  "}},
            {"qualitative": {"agent_summary": ""}},
            {"qualitative": {"agent_summary": None}},
            {"qualitative": {}},
            {},
        ]
        out = _collect_narratives(docs, "qualitative", "agent_summary")
        assert out == ["good", "spaced"]

    def test_ignores_non_strings(self):
        docs = [{"qualitative": {"x": 123}}, {"qualitative": {"x": ["list"]}}]
        assert _collect_narratives(docs, "qualitative", "x") == []


# ---------------------------------------------------------------------------
# __init__ member resolution
# ---------------------------------------------------------------------------

class TestInit:
    def test_config_defaults_council_size_one(self):
        # aggregation_config.json sets council_size=1, members=["gpt-4o"]
        c = _council(_mock_client())
        assert c.council_size == 1
        assert c.council_members == ["gpt-4o"]
        assert c.meta_judge_model == "gpt-4o"
        assert c.model_name == "gpt-4o"

    def test_explicit_council_members_take_priority(self):
        c = _council(_mock_client(), council_members=["m1", "m2"])
        assert c.council_members == ["m1", "m2"]
        assert c.meta_judge_model == "gpt-4o"  # config meta_judge_model wins over member[0]
        assert c.model_name == "m1"

    def test_explicit_meta_judge_model(self):
        c = _council(_mock_client(), council_members=["m1"], meta_judge_model="meta-x")
        assert c.meta_judge_model == "meta-x"

    def test_explicit_council_size_with_model_name_fallback(self, monkeypatch):
        # When config has no council_members, fall back to model_name * size.
        monkeypatch.setattr(lc, "_MODULE_CONFIG", {"llm_council": {"council_size": 2}})
        c = _council(_mock_client(), model_name="foo")
        assert c.council_members == ["foo", "foo"]
        assert c.council_size == 2


# ---------------------------------------------------------------------------
# get_council_model_info
# ---------------------------------------------------------------------------

class TestModelInfo:
    def test_info_per_member_and_meta(self):
        c = _council(_mock_client(), council_members=["m1", "m2"], meta_judge_model="meta")
        model_config = {
            "m1": {"deployment_name": "dep1", "api_version": "v1"},
            "meta": {"deployment_name": "depM", "api_version": "vM"},
        }
        info = c.get_council_model_info(model_config)
        assert info["member_1"]["model_name"] == "m1"
        assert info["member_1"]["deployment_name"] == "dep1"
        # m2 absent from config → unknown defaults
        assert info["member_2"]["deployment_name"] == "unknown"
        assert info["meta_model"]["deployment_name"] == "depM"


# ---------------------------------------------------------------------------
# _model_for_judge
# ---------------------------------------------------------------------------

class TestModelForJudge:
    def test_cycles_through_members(self):
        c = _council(_mock_client(), council_members=["a", "b"])
        assert c._model_for_judge(0) == "a"
        assert c._model_for_judge(1) == "b"
        assert c._model_for_judge(2) == "a"  # wraps


# ---------------------------------------------------------------------------
# _run_single_judge
# ---------------------------------------------------------------------------

class TestRunSingleJudge:
    async def test_dict_response_passthrough(self):
        resp = {"consensus_summary": "x", "severity_label": "Strong"}
        client = _mock_client(return_value=(resp, USAGE))
        c = _council(client)
        out, usage = await c._run_single_judge("p", "sys", 0)
        assert out == resp
        assert usage == USAGE

    async def test_non_dict_response_wrapped(self):
        client = _mock_client(return_value=("plain text", USAGE))
        c = _council(client)
        out, usage = await c._run_single_judge("p", "sys", 0)
        assert out["consensus_summary"] == "plain text"
        assert out["severity_label"] == "Adequate"
        assert out["confidence"] == "Medium"


# ---------------------------------------------------------------------------
# _run_meta_judge
# ---------------------------------------------------------------------------

class TestRunMetaJudge:
    async def test_dict_response(self):
        resp = {"consensus_summary": "meta", "inter_judge_agreement": 0.9}
        client = _mock_client(return_value=(resp, USAGE))
        c = _council(client)
        out, usage = await c._run_meta_judge([{"a": 1}], "metric", "cat", 3)
        assert out == resp
        assert usage == USAGE

    async def test_non_dict_response_wrapped(self):
        client = _mock_client(return_value=("text", USAGE))
        c = _council(client)
        out, _ = await c._run_meta_judge([{"a": 1}], "metric", "cat", 3)
        assert out["consensus_summary"] == "text"
        assert out["inter_judge_agreement"] == 0.5

    async def test_exception_returns_fallback(self):
        client = _mock_client(side_effect=RuntimeError("boom"))
        c = _council(client)
        out, usage = await c._run_meta_judge([{"a": 1}], "metric", "cat", 3)
        assert out["consensus_summary"] == "Meta-judge reconciliation failed."
        assert out["confidence"] == "Low"
        assert out["inter_judge_agreement"] == 0.0
        assert usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


# ---------------------------------------------------------------------------
# synthesize_textual_metric
# ---------------------------------------------------------------------------

class TestSynthesizeTextualMetric:
    async def test_empty_narratives_short_circuits(self):
        client = _mock_client(return_value=({}, USAGE))
        c = _council(client)
        out, usage = await c.synthesize_textual_metric([], "m", "cat")
        assert out == {}
        assert usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        client.call_llm.assert_not_awaited()

    async def test_single_judge_plus_meta_token_sum(self):
        # council_size=1 → 1 judge call + 1 meta call = 2 calls, tokens summed
        judge_resp = {"consensus_summary": "judge"}
        meta_resp = {"consensus_summary": "consensus", "inter_judge_agreement": 1.0}
        client = _mock_client(side_effect=[(judge_resp, USAGE), (meta_resp, USAGE)])
        c = _council(client)
        out, usage = await c.synthesize_textual_metric(["n1", "n2"], "m", "cat")
        assert out == meta_resp
        # 2 calls each with USAGE
        assert usage == {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}
        assert client.call_llm.await_count == 2

    async def test_judge_exception_uses_fallback_then_meta(self):
        # Judge raises → gather returns the exception → fallback judge output;
        # meta still runs.
        meta_resp = {"consensus_summary": "meta"}

        calls = {"n": 0}

        async def side(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("judge died")
            return meta_resp, USAGE

        client = _mock_client(side_effect=side)
        c = _council(client)
        out, usage = await c.synthesize_textual_metric(["n1"], "m", "cat")
        assert out == meta_resp
        # judge failed → contributed 0 tokens; only meta's USAGE counted
        assert usage == USAGE


# ---------------------------------------------------------------------------
# synthesize_list_metric
# ---------------------------------------------------------------------------

class TestSynthesizeListMetric:
    async def test_empty_items_short_circuits(self):
        client = _mock_client()
        c = _council(client)
        out, usage = await c.synthesize_list_metric([], "m", "cat", "tmpl {narratives}")
        assert out == {}
        client.call_llm.assert_not_awaited()

    async def test_picks_response_with_most_items(self):
        # The judge loop runs council_size (config=1) times regardless of member count.
        resp = {"ranked_items": [{"limitation": "a"}, {"limitation": "b"}]}
        client = _mock_client(return_value=(resp, USAGE))
        c = _council(client, council_members=["m1", "m2"])
        out, usage = await c.synthesize_list_metric(
            ["i1", "i2"], "known_limitations", "cat", "tmpl {n} {narratives} {metric_name} {fault_category}"
        )
        assert out == resp
        # council_size=1 → single judge call → single USAGE
        assert usage == USAGE

    async def test_prioritized_items_key(self):
        resp = {"prioritized_items": [{"recommendation": "x"}]}
        client = _mock_client(return_value=(resp, USAGE))
        c = _council(client)
        out, _ = await c.synthesize_list_metric(
            ["i1"], "recommendations", "cat",
            "tmpl {n} {narratives} {metric_name} {fault_category}"
        )
        assert out == resp


# ---------------------------------------------------------------------------
# synthesize_limitations_and_recommendations
# ---------------------------------------------------------------------------

class TestSynthesizeLimitations:
    async def test_success_extracts_both_keys(self):
        resp = {
            "known_limitations": {"ranked_items": [{"limitation": "x"}]},
            "recommendations": {"prioritized_items": [{"recommendation": "y"}]},
            "extra": "ignored",
        }
        client = _mock_client(return_value=(resp, USAGE))
        c = _council(client)
        out, usage = await c.synthesize_limitations_and_recommendations(
            fault_category="cat",
            faults_tested=["f1"],
            total_runs=5,
            numeric_aggs={"m": {"mean": 1}},
            derived_rates={"r": 0.5},
            boolean_aggs={"b": True},
            textual_aggs={"agent_summary": {"consensus_summary": "s"}},
        )
        assert out["known_limitations"] == resp["known_limitations"]
        assert out["recommendations"] == resp["recommendations"]
        assert "extra" not in out
        assert usage == USAGE

    async def test_exception_returns_empty_blocks(self):
        client = _mock_client(side_effect=RuntimeError("boom"))
        c = _council(client)
        out, usage = await c.synthesize_limitations_and_recommendations(
            fault_category="cat",
            faults_tested=[],
            total_runs=0,
            numeric_aggs={},
            derived_rates={},
            boolean_aggs={},
            textual_aggs={},
        )
        assert out == {"known_limitations": {}, "recommendations": {}}
        assert usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    async def test_non_dict_response_yields_empty_result(self):
        client = _mock_client(return_value=("not a dict", USAGE))
        c = _council(client)
        out, usage = await c.synthesize_limitations_and_recommendations(
            fault_category="cat",
            faults_tested=[],
            total_runs=1,
            numeric_aggs={},
            derived_rates={},
            boolean_aggs={},
            textual_aggs={"known_limitations": "skip", "recommendations": "skip"},
        )
        # non-dict → result has neither key
        assert out == {}
        assert usage == USAGE


# ---------------------------------------------------------------------------
# compute_textual_aggregates
# ---------------------------------------------------------------------------

class TestComputeTextualAggregates:
    async def test_no_narratives_returns_defaults(self):
        client = _mock_client()
        c = _council(client)
        out, usage = await c.compute_textual_aggregates([], "cat")
        # All six default keys present, no LLM calls made
        assert set(out.keys()) == {
            "rai_check_summary",
            "overall_response_and_reasoning_quality",
            "security_compliance_summary",
            "agent_summary",
            "sensitive_data_exposure_notes",
            "hallucination_notes",
        }
        assert out["rai_check_summary"]["consensus_summary"] == "Not evaluated."
        assert out["agent_summary"]["confidence"] == "High"
        assert usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        client.call_llm.assert_not_awaited()

    async def test_one_metric_synthesized(self):
        # Provide narrative only for agent_summary → one synthesize cycle
        # (1 judge + 1 meta = 2 calls). meta returns dict.
        meta_resp = {"consensus_summary": "summary text", "confidence": "High", "inter_judge_agreement": 1.0}
        client = _mock_client(return_value=(meta_resp, USAGE))
        c = _council(client)
        docs = [{"qualitative": {"agent_summary": "the agent did well"}}]
        out, usage = await c.compute_textual_aggregates(docs, "cat")
        assert out["agent_summary"]["consensus_summary"] == "summary text"
        # agent_summary fields are [consensus_summary, confidence, inter_judge_agreement]
        assert "severity_label" not in out["agent_summary"]
        # rai_check_summary stays at default (no narrative)
        assert out["rai_check_summary"]["consensus_summary"] == "Not evaluated."
        # 2 calls (judge + meta) for the single metric
        assert client.call_llm.await_count == 2
        assert usage["total_tokens"] == 30

    async def test_llm_failure_falls_back_gracefully(self):
        # Both judge and meta calls raise. synthesize_textual_metric swallows
        # them (gather return_exceptions + meta-judge internal try/except), so
        # compute_textual_aggregates does NOT propagate — it records the
        # meta-judge fallback consensus instead.
        client = _mock_client(side_effect=RuntimeError("synth failed"))
        c = _council(client)
        docs = [{"qualitative": {"rai_check_notes": "note"}}]
        out, usage = await c.compute_textual_aggregates(docs, "cat")
        assert out["rai_check_summary"]["consensus_summary"] == "Meta-judge reconciliation failed."
        assert out["rai_check_summary"]["confidence"] == "Low"
        # failed judge + failed meta both contribute 0 tokens
        assert usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
