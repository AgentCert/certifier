"""Unit tests for utils.file_storage.AsyncFileStorage.

Azure Blob SDK is fully mocked; no real blob storage is touched.
"""

from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

import utils.file_storage as fs
from utils import custom_errors
from utils.file_storage import AsyncFileStorage


def _make_container_client():
    """A MagicMock standing in for a ContainerClient with async methods."""
    cc = MagicMock(name="ContainerClient")
    cc.exists = AsyncMock(return_value=True)
    cc.create_container = AsyncMock()
    cc.close = AsyncMock()
    return cc


class TestCreateConnection:
    async def test_connection_string_path(self, monkeypatch):
        store = AsyncFileStorage(
            {
                "storage_connections": {
                    "mycontainer": {
                        "isActive": True,
                        "connection_str": "conn-str",
                    }
                }
            }
        )
        container_client = _make_container_client()
        blob_service = MagicMock()
        blob_service.get_container_client.return_value = container_client

        fake_bsc = MagicMock()
        fake_bsc.from_connection_string.return_value = blob_service
        monkeypatch.setattr(fs, "BlobServiceClient", fake_bsc)

        await store.create_file_storage_connection()

        fake_bsc.from_connection_string.assert_called_once_with(
            conn_str="conn-str", api_version="2020-10-02"
        )
        assert store.connection["mycontainer"] is container_client
        container_client.create_container.assert_not_called()  # exists() True

    async def test_account_name_uses_default_credential(self, monkeypatch):
        store = AsyncFileStorage(
            {
                "storage_connections": {
                    "c1": {"isActive": True, "account_name": "myacct"}
                }
            }
        )
        container_client = _make_container_client()
        container_client.exists = AsyncMock(return_value=False)  # triggers create
        blob_service = MagicMock()
        blob_service.get_container_client.return_value = container_client

        fake_bsc = MagicMock(return_value=blob_service)
        monkeypatch.setattr(fs, "BlobServiceClient", fake_bsc)
        monkeypatch.setattr(fs, "DefaultAzureCredential", MagicMock())

        await store.create_file_storage_connection()

        # Constructed with the expected account URL.
        _, kwargs = fake_bsc.call_args
        assert kwargs["account_url"] == "https://myacct.blob.core.windows.net"
        container_client.create_container.assert_awaited_once()

    async def test_inactive_container_skipped(self, monkeypatch):
        store = AsyncFileStorage(
            {"storage_connections": {"c1": {"isActive": False}}}
        )
        monkeypatch.setattr(fs, "BlobServiceClient", MagicMock())
        await store.create_file_storage_connection()
        assert store.connection == {}

    async def test_error_wrapped_in_custom_error(self, monkeypatch):
        store = AsyncFileStorage(
            {"storage_connections": {"c1": {"isActive": True, "connection_str": "x"}}}
        )
        fake_bsc = MagicMock()
        fake_bsc.from_connection_string.side_effect = RuntimeError("boom")
        monkeypatch.setattr(fs, "BlobServiceClient", fake_bsc)
        with pytest.raises(custom_errors.AsyncFileStorageError):
            await store.create_file_storage_connection()


class TestReadFile:
    async def test_not_connected_raises(self):
        store = AsyncFileStorage({})
        with pytest.raises(custom_errors.AsyncFileStorageError, match="not connected"):
            await store.read_file("missing", "f.txt")

    async def test_file_not_found_raises(self):
        store = AsyncFileStorage({})
        blob_client = MagicMock()
        blob_client.exists = AsyncMock(return_value=False)
        cc = MagicMock()
        cc.get_blob_client.return_value = blob_client
        store.connection["c1"] = cc
        with pytest.raises(custom_errors.AsyncFileStorageError, match="not found"):
            await store.read_file("c1", "missing.txt")

    async def test_read_returns_content(self):
        store = AsyncFileStorage({})
        download_stream = MagicMock()
        download_stream.readall = AsyncMock(return_value=b"hello")
        blob_client = MagicMock()
        blob_client.exists = AsyncMock(return_value=True)
        blob_client.download_blob = AsyncMock(return_value=download_stream)
        cc = MagicMock()
        cc.get_blob_client.return_value = blob_client
        store.connection["c1"] = cc

        result = await store.read_file("c1", "f.txt")
        assert result == b"hello"
        cc.get_blob_client.assert_called_once_with("f.txt")


class TestUploadFile:
    async def test_not_connected_raises(self):
        store = AsyncFileStorage({})
        with pytest.raises(custom_errors.AsyncFileStorageError, match="not connected"):
            await store.upload_file("c1", "/tmp/x.txt")

    async def test_upload_derives_container_path_from_basename(self, monkeypatch):
        store = AsyncFileStorage({})
        blob_client = MagicMock()
        blob_client.upload_blob = AsyncMock()
        blob_client.url = "https://blob/x.txt"
        cc = MagicMock()
        cc.get_blob_client.return_value = blob_client
        store.connection["c1"] = cc

        m = mock_open(read_data=b"data")
        with patch("builtins.open", m):
            url = await store.upload_file("c1", "/local/path/x.txt")

        cc.get_blob_client.assert_called_once_with("x.txt")
        blob_client.upload_blob.assert_awaited_once()
        assert url == "https://blob/x.txt"

    async def test_upload_uses_explicit_container_path(self, monkeypatch):
        store = AsyncFileStorage({})
        blob_client = MagicMock()
        blob_client.upload_blob = AsyncMock()
        blob_client.url = "u"
        cc = MagicMock()
        cc.get_blob_client.return_value = blob_client
        store.connection["c1"] = cc
        with patch("builtins.open", mock_open(read_data=b"d")):
            await store.upload_file("c1", "/local/x.txt", container_path="sub/y.txt")
        cc.get_blob_client.assert_called_once_with("sub/y.txt")


class TestListFiles:
    async def test_not_connected_raises(self):
        store = AsyncFileStorage({})
        with pytest.raises(custom_errors.AsyncFileStorageError):
            await store.list_files("c1")

    async def test_lists_all(self):
        store = AsyncFileStorage({})
        cc = MagicMock()
        cc.list_blobs = lambda: _aiter(
            [MagicMock(name="a.txt"), MagicMock(name="b.log")]
        )
        # MagicMock(name=...) does not set .name attr; set explicitly.
        blobs = [MagicMock(), MagicMock()]
        blobs[0].name = "a.txt"
        blobs[1].name = "b.log"
        cc.list_blobs = lambda: _aiter(blobs)
        store.connection["c1"] = cc
        result = await store.list_files("c1")
        assert result == ["a.txt", "b.log"]

    async def test_regex_filter(self):
        store = AsyncFileStorage({})
        blobs = [MagicMock(), MagicMock(), MagicMock()]
        blobs[0].name = "metrics_1.json"
        blobs[1].name = "other.txt"
        blobs[2].name = "metrics_2.json"
        cc = MagicMock()
        cc.list_blobs = lambda: _aiter(blobs)
        store.connection["c1"] = cc
        result = await store.list_files("c1", regex_pattern=r"metrics_\d+\.json")
        assert result == ["metrics_1.json", "metrics_2.json"]


class TestDeleteFile:
    async def test_not_connected_raises(self):
        store = AsyncFileStorage({})
        with pytest.raises(custom_errors.AsyncFileStorageError):
            await store.delete_file("c1", "f.txt")

    async def test_missing_file_returns_false(self):
        store = AsyncFileStorage({})
        blob_client = MagicMock()
        blob_client.exists = AsyncMock(return_value=False)
        cc = MagicMock()
        cc.get_blob_client.return_value = blob_client
        store.connection["c1"] = cc
        assert await store.delete_file("c1", "gone.txt") is False

    async def test_delete_success_returns_true(self):
        store = AsyncFileStorage({})
        blob_client = MagicMock()
        blob_client.exists = AsyncMock(return_value=True)
        blob_client.delete_blob = AsyncMock()
        cc = MagicMock()
        cc.get_blob_client.return_value = blob_client
        store.connection["c1"] = cc
        assert await store.delete_file("c1", "f.txt") is True
        blob_client.delete_blob.assert_awaited_once()


class TestClose:
    async def test_close_clears_connections(self):
        store = AsyncFileStorage({})
        cc1 = MagicMock()
        cc1.close = AsyncMock()
        store.connection["c1"] = cc1
        await store.close()
        cc1.close.assert_awaited_once()
        assert store.connection == {}

    async def test_close_swallows_individual_close_errors(self):
        store = AsyncFileStorage({})
        cc1 = MagicMock()
        cc1.close = AsyncMock(side_effect=RuntimeError("nope"))
        store.connection["c1"] = cc1
        # Per-container errors are logged, not raised; dict still cleared.
        await store.close()
        assert store.connection == {}


async def _aiter(items):
    for i in items:
        yield i
