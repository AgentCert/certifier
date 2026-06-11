"""Unit tests for cert_builder/scripts/narratives/llm_client.py.

The Azure OpenAI client is fully mocked — no network, no real API key.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from cert_builder.scripts.narratives import llm_client as lc


# ── get_client ───────────────────────────────────────────────────────

def test_get_client_reads_env(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://e")
    with patch.object(lc, "AzureOpenAI") as MockClient:
        lc.get_client()
        MockClient.assert_called_once()
        kwargs = MockClient.call_args.kwargs
        assert kwargs["api_key"] == "k"
        assert kwargs["azure_endpoint"] == "https://e"


# ── _prepare_strict_schema ───────────────────────────────────────────

class _Schema(BaseModel):
    name: str
    age: int


def test_prepare_strict_schema_sets_required_and_additional_props():
    raw = _Schema.model_json_schema()
    out = lc._prepare_strict_schema(raw)
    assert out["additionalProperties"] is False
    assert sorted(out["required"]) == ["age", "name"]


def test_prepare_strict_schema_does_not_mutate_input():
    raw = _Schema.model_json_schema()
    raw_copy = json.loads(json.dumps(raw))
    lc._prepare_strict_schema(raw)
    assert raw == raw_copy  # deep-copied internally


def test_prepare_strict_schema_handles_nested_defs():
    schema = {
        "$defs": {"Inner": {"properties": {"x": {"type": "string"}}}},
        "properties": {"items": {"items": {"$ref": "#/$defs/Inner"}}},
    }
    out = lc._prepare_strict_schema(schema)
    assert out["$defs"]["Inner"]["additionalProperties"] is False


# ── call_llm helpers ─────────────────────────────────────────────────

def _fake_response(content, *, model="gpt-4o", prompt=10, completion=5, total=15):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion,
                              total_tokens=total),
        model=model,
    )


def _client_returning(response):
    client = MagicMock()
    client.chat.completions.create.return_value = response
    return client


def test_call_llm_plain_text():
    client = _client_returning(_fake_response("hello world"))
    out = lc.call_llm(client, "sys", "user", expect_json=False)
    assert out["content"] == "hello world"
    assert out["tokens_used"] == 15
    assert out["input_tokens"] == 10
    assert out["output_tokens"] == 5
    assert out["model"] == "gpt-4o"


def test_call_llm_json_parsing():
    client = _client_returning(_fake_response('{"k": 1}'))
    out = lc.call_llm(client, "sys", "user", expect_json=True)
    assert out["content"] == {"k": 1}


def test_call_llm_response_schema_validation():
    client = _client_returning(_fake_response('{"name": "x", "age": 3}'))
    out = lc.call_llm(client, "sys", "user", response_schema=_Schema)
    assert isinstance(out["content"], _Schema)
    assert out["content"].age == 3


def test_call_llm_sends_temperature_for_non_reasoning():
    client = _client_returning(_fake_response("ok"))
    lc.call_llm(client, "sys", "user", expect_json=False, temperature=0.5)
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["temperature"] == 0.5
    assert "max_completion_tokens" in kwargs


def test_call_llm_reasoning_omits_temperature():
    client = _client_returning(_fake_response("ok"))
    lc.call_llm(client, "sys", "user", expect_json=False, is_reasoning_model=True)
    kwargs = client.chat.completions.create.call_args.kwargs
    assert "temperature" not in kwargs
    assert "max_completion_tokens" in kwargs


def test_call_llm_auto_fallback_to_reasoning_params():
    client = MagicMock()
    good = _fake_response("recovered")
    client.chat.completions.create.side_effect = [
        Exception("unsupported_parameter: 'temperature' is not supported"),
        good,
    ]
    out = lc.call_llm(client, "sys", "user", expect_json=False, retries=3)
    assert out["content"] == "recovered"
    # Second (successful) call must omit temperature.
    second_kwargs = client.chat.completions.create.call_args_list[1].kwargs
    assert "temperature" not in second_kwargs


def test_call_llm_retries_then_raises(monkeypatch):
    monkeypatch.setattr(lc.time, "sleep", lambda *_: None)
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="LLM call failed after 3 attempts"):
        lc.call_llm(client, "sys", "user", expect_json=False, retries=3)
    assert client.chat.completions.create.call_count == 3


def test_call_llm_uses_default_deployment(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME", raising=False)
    client = _client_returning(_fake_response("ok", model=None))
    out = lc.call_llm(client, "sys", "user", expect_json=False)
    # model falls back to deployment name when response.model is None
    assert out["model"] == "gpt-4o"
