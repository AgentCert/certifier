"""Unit tests for utils.azure_openai_util.AzureLLMClient.

The agent_framework Azure SDK is fully mocked. No real network calls.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import utils.azure_openai_util as aou
from utils import custom_errors
from utils.azure_openai_util import AzureLLMClient


@pytest.fixture(autouse=True)
def _reset_class_state(monkeypatch):
    """Reset the singleton class-level caches and mock the chat client class."""
    AzureLLMClient._shared_client = None
    AzureLLMClient._shared_clients = {}
    AzureLLMClient._model_types = {}
    # Patch AzureOpenAIChatClient so construction never hits Azure.
    monkeypatch.setattr(
        aou, "AzureOpenAIChatClient", MagicMock(return_value=MagicMock(name="chat_client"))
    )
    yield
    AzureLLMClient._shared_client = None
    AzureLLMClient._shared_clients = {}
    AzureLLMClient._model_types = {}


def _usage(i=10, o=5, t=15):
    return SimpleNamespace(
        input_token_count=i, output_token_count=o, total_token_count=t
    )


class TestInitAndConfig:
    def test_init_parses_models_and_model_types(self):
        config = {
            "models": {
                "gpt-4o": {"model_type": "standard", "deployment_name": "d1"},
                "o1": {"model_type": "reasoning", "deployment_name": "d2"},
            }
        }
        client = AzureLLMClient(config)
        assert client.config == config["models"]
        assert AzureLLMClient._model_types["o1"] == "reasoning"
        assert AzureLLMClient._model_types["gpt-4o"] == "standard"

    def test_init_with_empty_config(self):
        client = AzureLLMClient({})
        assert client.config == {}

    def test_init_creates_clients_per_model(self):
        config = {"models": {"gpt-4o": {}, "o1": {}}}
        AzureLLMClient(config)
        assert set(AzureLLMClient._shared_clients.keys()) == {"gpt-4o", "o1"}

    def test_init_wraps_client_creation_error(self, monkeypatch):
        monkeypatch.setattr(
            aou, "AzureOpenAIChatClient", MagicMock(side_effect=RuntimeError("bad"))
        )
        with pytest.raises(custom_errors.LLMError):
            AzureLLMClient({"models": {"gpt-4o": {}}})


class TestIsReasoningModel:
    def test_reasoning_true(self):
        client = AzureLLMClient(
            {"models": {"o1": {"model_type": "reasoning"}}}
        )
        assert client.is_reasoning_model("o1") is True

    def test_standard_false(self):
        client = AzureLLMClient(
            {"models": {"gpt-4o": {"model_type": "standard"}}}
        )
        assert client.is_reasoning_model("gpt-4o") is False

    def test_unknown_defaults_standard(self):
        client = AzureLLMClient({})
        assert client.is_reasoning_model("does-not-exist") is False


class TestConvertMessages:
    def test_string_to_single_user_message(self):
        client = AzureLLMClient({})
        msgs = client._convert_messages_to_chat_messages("hello")
        assert len(msgs) == 1
        assert msgs[0].role.value == "user"
        assert msgs[0].text == "hello"

    def test_dict_with_content(self):
        client = AzureLLMClient({})
        msgs = client._convert_messages_to_chat_messages(
            [{"role": "system", "content": "be nice"}]
        )
        assert msgs[0].text == "be nice"

    def test_dict_with_text_fallback(self):
        client = AzureLLMClient({})
        msgs = client._convert_messages_to_chat_messages([{"text": "via text key"}])
        assert msgs[0].text == "via text key"
        assert msgs[0].role.value == "user"

    def test_passthrough_chat_message(self):
        from agent_framework import ChatMessage

        client = AzureLLMClient({})
        cm = ChatMessage(role="user", text="x")
        msgs = client._convert_messages_to_chat_messages([cm])
        assert msgs == [cm]


def _install_agent(client, run_result):
    """Force _get_or_create_agent to return an agent whose run() yields run_result."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value=run_result)
    client._get_or_create_agent = MagicMock(return_value=agent)
    return agent


class TestCallLLMReasoningHandling:
    async def test_standard_model_passes_temperature(self):
        client = AzureLLMClient(
            {"models": {"gpt-4o": {"model_type": "standard"}}}
        )
        result = SimpleNamespace(text='{"ok": true}', usage_details=_usage())
        agent = _install_agent(client, result)
        await client.call_llm("gpt-4o", "hi", temperature=0.42)
        _, kwargs = agent.run.call_args
        assert kwargs["temperature"] == 0.42

    async def test_reasoning_model_strips_temperature(self):
        client = AzureLLMClient(
            {"models": {"o1": {"model_type": "reasoning"}}}
        )
        result = SimpleNamespace(text='{"ok": true}', usage_details=_usage())
        agent = _install_agent(client, result)
        await client.call_llm("o1", "hi", temperature=0.9)
        _, kwargs = agent.run.call_args
        assert "temperature" not in kwargs


class TestCallLLMResponseParsing:
    async def test_parses_json_response(self):
        client = AzureLLMClient({"models": {"gpt-4o": {}}})
        result = SimpleNamespace(text='{"key": "value"}', usage_details=_usage(1, 2, 3))
        _install_agent(client, result)
        content, usage = await client.call_llm("gpt-4o", "hi")
        assert content == {"key": "value"}
        assert usage == {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}

    async def test_strips_json_code_fence(self):
        client = AzureLLMClient({"models": {"gpt-4o": {}}})
        result = SimpleNamespace(
            text='```json\n{"a": 1}\n```', usage_details=_usage()
        )
        _install_agent(client, result)
        content, _ = await client.call_llm("gpt-4o", "hi")
        assert content == {"a": 1}

    async def test_non_json_wrapped_in_response_key(self):
        client = AzureLLMClient({"models": {"gpt-4o": {}}})
        result = SimpleNamespace(text="plain text reply", usage_details=_usage())
        _install_agent(client, result)
        content, _ = await client.call_llm("gpt-4o", "hi")
        assert content == {"response": "plain text reply"}


class TestCallLLMErrors:
    async def test_custom_error_propagates(self):
        client = AzureLLMClient({"models": {"gpt-4o": {}}})
        agent = MagicMock()
        agent.run = AsyncMock(side_effect=custom_errors.LLMError("inner"))
        client._get_or_create_agent = MagicMock(return_value=agent)
        with pytest.raises(custom_errors.LLMError, match="inner"):
            await client.call_llm("gpt-4o", "hi")

    async def test_generic_error_wrapped(self):
        client = AzureLLMClient({"models": {"gpt-4o": {}}})
        agent = MagicMock()
        agent.run = AsyncMock(side_effect=ValueError("kaboom"))
        client._get_or_create_agent = MagicMock(return_value=agent)
        with pytest.raises(custom_errors.LLMError, match="kaboom"):
            await client.call_llm("gpt-4o", "hi", max_retries=0)

    async def test_rate_limit_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(aou.asyncio, "sleep", AsyncMock())
        client = AzureLLMClient({"models": {"gpt-4o": {}}})
        good = SimpleNamespace(text='{"done": true}', usage_details=_usage())
        agent = MagicMock()
        agent.run = AsyncMock(
            side_effect=[RuntimeError("429 too_many_requests"), good]
        )
        client._get_or_create_agent = MagicMock(return_value=agent)
        content, _ = await client.call_llm("gpt-4o", "hi", max_retries=2)
        assert content == {"done": True}
        assert agent.run.await_count == 2

    async def test_rate_limit_exhausts_retries(self, monkeypatch):
        monkeypatch.setattr(aou.asyncio, "sleep", AsyncMock())
        client = AzureLLMClient({"models": {"gpt-4o": {}}})
        agent = MagicMock()
        agent.run = AsyncMock(side_effect=RuntimeError("429"))
        client._get_or_create_agent = MagicMock(return_value=agent)
        with pytest.raises(custom_errors.LLMError):
            await client.call_llm("gpt-4o", "hi", max_retries=1)


class TestStructuredOutput:
    async def test_reasoning_strips_temperature(self):
        client = AzureLLMClient({"models": {"o1": {"model_type": "reasoning"}}})
        result = SimpleNamespace(text='{"x": 1}', usage_details=_usage())
        agent = _install_agent(client, result)
        await client.with_structured_output("o1", "hi", output_format=None)
        _, kwargs = agent.run.call_args
        assert "temperature" not in kwargs

    async def test_validates_with_pydantic(self):
        from pydantic import BaseModel

        class Out(BaseModel):
            x: int

        client = AzureLLMClient({"models": {"gpt-4o": {}}})
        result = SimpleNamespace(text='{"x": 7}', usage_details=_usage())
        _install_agent(client, result)
        resp, usage = await client.with_structured_output(
            "gpt-4o", "hi", output_format=Out
        )
        assert isinstance(resp, Out)
        assert resp.x == 7

    async def test_no_format_returns_raw_dict(self):
        client = AzureLLMClient({"models": {"gpt-4o": {}}})
        result = SimpleNamespace(text='{"y": 2}', usage_details=_usage())
        _install_agent(client, result)
        resp, _ = await client.with_structured_output(
            "gpt-4o", "hi", output_format=None
        )
        assert resp == {"y": 2}


class TestGetChatCompletion:
    async def test_delegates_to_call_llm(self):
        client = AzureLLMClient({"models": {"gpt-4o": {}}})
        result = SimpleNamespace(text='{"ok": 1}', usage_details=_usage())
        _install_agent(client, result)
        content, _ = await client.get_chat_completion("gpt-4o", "hi")
        assert content == {"ok": 1}


class TestRunAgent:
    async def test_success(self):
        client = AzureLLMClient({"models": {"gpt-4o": {}}})
        result = SimpleNamespace(text="agent says hi", usage_details=_usage(2, 3, 5))
        _install_agent(client, result)
        out = await client.run_agent("gpt-4o", "hi")
        assert out["success"] is True
        assert out["response"] == "agent says hi"
        assert out["usage"]["total_tokens"] == 5

    async def test_failure_returns_error_dict(self):
        client = AzureLLMClient({"models": {"gpt-4o": {}}})
        agent = MagicMock()
        agent.run = AsyncMock(side_effect=RuntimeError("nope"))
        client._get_or_create_agent = MagicMock(return_value=agent)
        out = await client.run_agent("gpt-4o", "hi")
        assert out["success"] is False
        assert "nope" in out["error"]


class TestRunAgentStream:
    async def test_yields_updates(self):
        client = AzureLLMClient({"models": {"gpt-4o": {}}})

        async def _stream(messages):
            for t in ["a", "b", "c"]:
                yield SimpleNamespace(text=t)

        agent = MagicMock()
        agent.run_stream = _stream
        client._get_or_create_agent = MagicMock(return_value=agent)
        chunks = [c async for c in client.run_agent_stream("gpt-4o", "hi")]
        assert chunks == ["a", "b", "c"]

    async def test_stream_error_yields_error_string(self):
        client = AzureLLMClient({"models": {"gpt-4o": {}}})
        agent = MagicMock()

        def _boom(messages):
            raise RuntimeError("stream fail")

        agent.run_stream = _boom
        client._get_or_create_agent = MagicMock(return_value=agent)
        chunks = [c async for c in client.run_agent_stream("gpt-4o", "hi")]
        assert any("stream fail" in c for c in chunks)


class TestGetOrCreateAgent:
    async def test_uses_specific_client_and_caches(self, monkeypatch):
        client = AzureLLMClient({"models": {"gpt-4o": {}}})
        fake_agent = MagicMock(name="ChatAgent")
        monkeypatch.setattr(aou, "ChatAgent", MagicMock(return_value=fake_agent))
        a1 = client._get_or_create_agent("gpt-4o", "sys")
        a2 = client._get_or_create_agent("gpt-4o", "sys")
        assert a1 is a2  # cached by (model, hash(system_prompt))

    def test_unknown_model_falls_back_to_default_client(self, monkeypatch):
        client = AzureLLMClient({"models": {"gpt-4o": {}}})
        fake_agent = MagicMock(name="ChatAgent")
        monkeypatch.setattr(aou, "ChatAgent", MagicMock(return_value=fake_agent))
        agent = client._get_or_create_agent("unknown-model", "sys")
        assert agent is fake_agent


class TestCloseAndContextManager:
    async def test_close_clears_agents(self):
        client = AzureLLMClient({"models": {"gpt-4o": {}}})
        client.model_agents["k"] = MagicMock()
        await client.close()
        assert client.model_agents == {}

    async def test_async_context_manager(self):
        async with AzureLLMClient({"models": {"gpt-4o": {}}}) as client:
            client.model_agents["k"] = MagicMock()
        assert client.model_agents == {}


class TestGetClient:
    def test_get_client_singleton(self):
        c1 = AzureLLMClient.get_client({"gpt-4o": {}})
        c2 = AzureLLMClient.get_client({"gpt-4o": {}})
        assert c1 is c2
