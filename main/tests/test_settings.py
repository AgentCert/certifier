"""Unit tests for main.config.settings.

Settings reads env vars at instantiation via dataclass default_factory, and
get_settings() memoises a process-wide singleton. We reset that singleton
between tests so each test sees a clean read of the (monkeypatched) env.
"""
from pathlib import Path

import pytest

import main.config.settings as settings_mod
from main.config.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure each test starts (and ends) with a fresh settings singleton."""
    settings_mod._settings = None
    yield
    settings_mod._settings = None


def test_defaults_with_minimal_env(monkeypatch):
    monkeypatch.setenv("MONGODB_CONNECTION_STRING", "mongodb://host:27017")
    for var in (
        "MONGODB_DATABASE", "API_TASK_COLLECTION", "WORKSPACE_DIR",
        "API_MAX_CONCURRENT_TASKS", "API_HOST", "API_PORT",
        "CERT_TASK_COLLECTION", "CERT_METADATA_COLLECTION",
        "AGG_CATEGORY_COLLECTION", "API_MAX_CONCURRENT_CERT_TASKS",
    ):
        monkeypatch.delenv(var, raising=False)

    s = Settings()
    assert s.mongodb_uri == "mongodb://host:27017"
    assert s.mongodb_database == "agentcert"
    assert s.task_collection == "pipeline_tasks"
    assert s.max_concurrent_tasks == 4
    assert s.host == "0.0.0.0"
    assert s.port == 8000
    assert s.cert_task_collection == "certification_tasks"
    assert s.cert_metadata_collection == "certification_metadata"
    assert s.agg_category_collection == "aggregated_category_metadata"
    assert s.max_concurrent_cert_tasks == 2
    assert isinstance(s.workspace_dir, Path)
    assert s.workspace_dir.is_absolute()  # resolve() makes it absolute


def test_missing_mongodb_uri_raises(monkeypatch):
    monkeypatch.delenv("MONGODB_CONNECTION_STRING", raising=False)
    with pytest.raises(KeyError):
        Settings()


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("MONGODB_CONNECTION_STRING", "mongodb://m:1")
    monkeypatch.setenv("MONGODB_DATABASE", "mydb")
    monkeypatch.setenv("API_TASK_COLLECTION", "tasks2")
    monkeypatch.setenv("API_MAX_CONCURRENT_TASKS", "9")
    monkeypatch.setenv("API_HOST", "127.0.0.1")
    monkeypatch.setenv("API_PORT", "9999")
    monkeypatch.setenv("CERT_TASK_COLLECTION", "ct")
    monkeypatch.setenv("CERT_METADATA_COLLECTION", "cm")
    monkeypatch.setenv("AGG_CATEGORY_COLLECTION", "ac")
    monkeypatch.setenv("API_MAX_CONCURRENT_CERT_TASKS", "7")
    monkeypatch.setenv("WORKSPACE_DIR", "/tmp/ws-test")

    s = Settings()
    assert s.mongodb_database == "mydb"
    assert s.task_collection == "tasks2"
    assert s.max_concurrent_tasks == 9
    assert s.host == "127.0.0.1"
    assert s.port == 9999
    assert s.cert_task_collection == "ct"
    assert s.cert_metadata_collection == "cm"
    assert s.agg_category_collection == "ac"
    assert s.max_concurrent_cert_tasks == 7
    assert s.workspace_dir == Path("/tmp/ws-test").resolve()


def test_get_settings_memoises(monkeypatch):
    monkeypatch.setenv("MONGODB_CONNECTION_STRING", "mongodb://m:1")
    first = get_settings()
    # Changing the env after the first call must not affect the cached instance
    monkeypatch.setenv("MONGODB_CONNECTION_STRING", "mongodb://other:2")
    second = get_settings()
    assert first is second
    assert second.mongodb_uri == "mongodb://m:1"
