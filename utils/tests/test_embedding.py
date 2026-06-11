"""Unit tests for utils.embedding.OpenAIEmbedding.

The Azure / OpenAI SDK is fully mocked — no network, no credentials. Both
the async and sync client classes are patched at the module level.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import utils.embedding as emb
from utils import custom_errors
from utils.embedding import OpenAIEmbedding


def _emb_response(*vectors):
    """Build a fake embeddings.create response with .data[i].embedding."""
    return SimpleNamespace(data=[SimpleNamespace(embedding=list(v)) for v in vectors])


@pytest.fixture
def patched_clients(monkeypatch):
    """Patch AsyncAzureOpenAI and AzureOpenAI; return the instance mocks."""
    async_inst = MagicMock(name="async_client")
    async_inst.embeddings.create = AsyncMock()
    async_inst.close = AsyncMock()

    sync_inst = MagicMock(name="sync_client")

    monkeypatch.setattr(emb, "AsyncAzureOpenAI", MagicMock(return_value=async_inst))
    monkeypatch.setattr(emb, "AzureOpenAI", MagicMock(return_value=sync_inst))
    monkeypatch.setattr(emb, "DefaultAzureCredential", MagicMock())
    monkeypatch.setattr(emb, "get_bearer_token_provider", MagicMock(return_value="tok"))
    return async_inst, sync_inst


def _config(**over):
    cfg = {
        "apiKey": "secret",
        "endpoint": "https://example.openai.azure.com",
        "deployment_name": "text-embedding-3-small",
        "api_version": "2024-02-01",
    }
    cfg.update(over)
    return {"models": {"embedding_model": cfg}}


class TestInit:
    def test_init_with_api_key_uses_key_auth(self, patched_clients, monkeypatch):
        client = OpenAIEmbedding(_config())
        assert client.api_key == "secret"
        assert client.azure_endpoint == "https://example.openai.azure.com"
        assert client.model == "text-embedding-3-small"
        assert client.api_version == "2024-02-01"
        # DefaultAzureCredential must NOT be used when an API key is present
        emb.DefaultAzureCredential.assert_not_called()
        emb.AsyncAzureOpenAI.assert_called_once()

    def test_init_falls_back_to_env_for_key_and_endpoint(self, patched_clients, monkeypatch):
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "envkey")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://env.endpoint")
        cfg = {"models": {"embedding_model": {"deployment_name": "d", "api_version": "v"}}}
        client = OpenAIEmbedding(cfg)
        assert client.api_key == "envkey"
        assert client.azure_endpoint == "https://env.endpoint"

    def test_missing_endpoint_raises(self, patched_clients, monkeypatch):
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        cfg = {"models": {"embedding_model": {"apiKey": "k", "deployment_name": "d"}}}
        with pytest.raises(ValueError, match="Azure endpoint must be provided"):
            OpenAIEmbedding(cfg)

    def test_uses_credential_when_no_api_key(self, patched_clients, monkeypatch):
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        cfg = {"models": {"embedding_model": {
            "endpoint": "https://x", "deployment_name": "d", "api_version": "v"}}}
        OpenAIEmbedding(cfg)
        emb.DefaultAzureCredential.assert_called_once()
        emb.get_bearer_token_provider.assert_called_once()


class TestAsyncEmbedding:
    @pytest.fixture
    def client(self, patched_clients):
        return OpenAIEmbedding(_config())

    async def test_embed_text(self, client, patched_clients):
        async_inst, _ = patched_clients
        async_inst.embeddings.create.return_value = _emb_response([0.1, 0.2])
        out = await client.embed_text("hello")
        assert out == [0.1, 0.2]
        async_inst.embeddings.create.assert_awaited_once_with(
            input="hello", model="text-embedding-3-small"
        )

    async def test_embed_text_wraps_error(self, client, patched_clients):
        async_inst, _ = patched_clients
        async_inst.embeddings.create.side_effect = RuntimeError("boom")
        with pytest.raises(custom_errors.OpenAIEmbeddingError):
            await client.embed_text("hi")

    async def test_embed_text_reraises_custom_error(self, client, patched_clients):
        async_inst, _ = patched_clients
        async_inst.embeddings.create.side_effect = custom_errors.OpenAIEmbeddingError("x")
        with pytest.raises(custom_errors.OpenAIEmbeddingError):
            await client.embed_text("hi")

    async def test_embed_batch_empty(self, client):
        assert await client.embed_batch([]) == []

    async def test_embed_batch_single_batch(self, client, patched_clients):
        async_inst, _ = patched_clients
        async_inst.embeddings.create.return_value = _emb_response([1], [2], [3])
        out = await client.embed_batch(["a", "b", "c"])
        assert out == [[1], [2], [3]]
        assert async_inst.embeddings.create.await_count == 1

    async def test_embed_batch_splits_at_1000(self, client, patched_clients):
        async_inst, _ = patched_clients
        # 2001 texts → batches of 1000, 1000, 1
        texts = [f"t{i}" for i in range(2001)]
        async_inst.embeddings.create.side_effect = [
            _emb_response(*([[0.0]] * 1000)),
            _emb_response(*([[0.0]] * 1000)),
            _emb_response([0.0]),
        ]
        out = await client.embed_batch(texts)
        assert len(out) == 2001
        assert async_inst.embeddings.create.await_count == 3

    async def test_embed_batch_wraps_error(self, client, patched_clients):
        async_inst, _ = patched_clients
        async_inst.embeddings.create.side_effect = RuntimeError("boom")
        with pytest.raises(custom_errors.OpenAIEmbeddingError):
            await client.embed_batch(["a"])

    async def test_aembed_documents(self, client, patched_clients):
        async_inst, _ = patched_clients
        async_inst.embeddings.create.return_value = _emb_response([1], [2])
        out = await client.aembed_documents(["a", "b"])
        assert out == [[1], [2]]

    async def test_aembed_documents_error(self, client, patched_clients):
        async_inst, _ = patched_clients
        async_inst.embeddings.create.side_effect = RuntimeError("boom")
        with pytest.raises(custom_errors.OpenAIEmbeddingError):
            await client.aembed_documents(["a"])

    async def test_aembed_query(self, client, patched_clients):
        async_inst, _ = patched_clients
        async_inst.embeddings.create.return_value = _emb_response([9, 8])
        assert await client.aembed_query("q") == [9, 8]

    async def test_aembed_query_error(self, client, patched_clients):
        async_inst, _ = patched_clients
        async_inst.embeddings.create.side_effect = RuntimeError("boom")
        with pytest.raises(custom_errors.OpenAIEmbeddingError):
            await client.aembed_query("q")

    async def test_close(self, client, patched_clients):
        async_inst, sync_inst = patched_clients
        await client.close()
        async_inst.close.assert_awaited_once()
        sync_inst.close.assert_called_once()

    async def test_close_error_wrapped(self, client, patched_clients):
        async_inst, _ = patched_clients
        async_inst.close.side_effect = RuntimeError("boom")
        with pytest.raises(custom_errors.OpenAIEmbeddingError):
            await client.close()


class TestSyncEmbedding:
    @pytest.fixture
    def client(self, patched_clients):
        return OpenAIEmbedding(_config())

    def test_embed_documents(self, client, patched_clients):
        _, sync_inst = patched_clients
        sync_inst.embeddings.create.return_value = _emb_response([1], [2])
        assert client.embed_documents(["a", "b"]) == [[1], [2]]

    def test_embed_documents_error(self, client, patched_clients):
        _, sync_inst = patched_clients
        sync_inst.embeddings.create.side_effect = RuntimeError("boom")
        with pytest.raises(custom_errors.OpenAIEmbeddingError):
            client.embed_documents(["a"])

    def test_embed_query(self, client, patched_clients):
        _, sync_inst = patched_clients
        sync_inst.embeddings.create.return_value = _emb_response([7, 7])
        assert client.embed_query("q") == [7, 7]

    def test_embed_query_error(self, client, patched_clients):
        _, sync_inst = patched_clients
        sync_inst.embeddings.create.side_effect = RuntimeError("boom")
        with pytest.raises(custom_errors.OpenAIEmbeddingError):
            client.embed_query("q")
