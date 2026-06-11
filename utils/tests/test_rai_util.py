"""Unit tests for utils.rai_util.RAIContentSafety.

All Azure SDK objects (ContentSafetyClient, AzureKeyCredential) are mocked;
no real network calls are made.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import utils.rai_util as rai_util
from utils.rai_util import RAIContentSafety


@pytest.fixture(autouse=True)
def _patch_azure_sdk(monkeypatch):
    """Replace the Azure SDK constructors so __init__ never touches network."""
    monkeypatch.setattr(rai_util, "AzureKeyCredential", lambda key: ("cred", key))
    fake_client = MagicMock(name="ContentSafetyClient")
    monkeypatch.setattr(
        rai_util, "ContentSafetyClient", MagicMock(return_value=fake_client)
    )
    return fake_client


class TestInit:
    def test_missing_endpoint_raises(self, monkeypatch):
        monkeypatch.delenv("AZURE_CONTENT_SAFETY_ENDPOINT", raising=False)
        monkeypatch.setenv("AZURE_CONTENT_SAFETY_API_KEY", "k")
        with pytest.raises(ValueError, match="Content Safety endpoint not set"):
            RAIContentSafety()

    def test_defaults_without_config(self, monkeypatch):
        monkeypatch.setenv("AZURE_CONTENT_SAFETY_ENDPOINT", "https://cs")
        monkeypatch.setenv("AZURE_CONTENT_SAFETY_API_KEY", "k")
        rai = RAIContentSafety()
        assert rai.severity_threshold == {}
        assert rai.overall_severity_threshold == 1

    def test_config_thresholds_applied(self, monkeypatch):
        monkeypatch.setenv("AZURE_CONTENT_SAFETY_ENDPOINT", "https://cs")
        monkeypatch.setenv("AZURE_CONTENT_SAFETY_API_KEY", "k")
        cfg = {
            "rai_severity_threshold": {"Hate": 3},
            "rai_overall_severity_threshold": 2,
        }
        rai = RAIContentSafety(cfg)
        assert rai.severity_threshold == {"Hate": 3}
        assert rai.overall_severity_threshold == 2


def _make_rai(monkeypatch, config=None):
    monkeypatch.setenv("AZURE_CONTENT_SAFETY_ENDPOINT", "https://cs")
    monkeypatch.setenv("AZURE_CONTENT_SAFETY_API_KEY", "k")
    return RAIContentSafety(config)


class TestAnalyzeText:
    async def test_flags_categories_over_threshold(self, monkeypatch):
        rai = _make_rai(
            monkeypatch,
            {"rai_severity_threshold": {"Hate": 2}, "rai_overall_severity_threshold": 1},
        )
        analysis = [
            SimpleNamespace(category="Hate", severity=3),  # >= 2 -> flagged
            SimpleNamespace(category="Violence", severity=0),  # < 1 -> not flagged
            SimpleNamespace(category="Sexual", severity=1),  # >= 1 overall -> flagged
        ]
        rai.client.analyze_text = AsyncMock(
            return_value=SimpleNamespace(categories_analysis=analysis)
        )
        result = await rai.analyze_text("some text")
        assert result == {"Hate": 3, "Sexual": 1}

    async def test_nothing_flagged_when_below_threshold(self, monkeypatch):
        rai = _make_rai(monkeypatch, {"rai_overall_severity_threshold": 5})
        analysis = [SimpleNamespace(category="Hate", severity=2)]
        rai.client.analyze_text = AsyncMock(
            return_value=SimpleNamespace(categories_analysis=analysis)
        )
        result = await rai.analyze_text("clean")
        assert result == {}

    async def test_uses_overall_threshold_for_unconfigured_category(self, monkeypatch):
        rai = _make_rai(
            monkeypatch,
            {"rai_severity_threshold": {"Hate": 4}, "rai_overall_severity_threshold": 1},
        )
        analysis = [SimpleNamespace(category="Violence", severity=1)]
        rai.client.analyze_text = AsyncMock(
            return_value=SimpleNamespace(categories_analysis=analysis)
        )
        result = await rai.analyze_text("x")
        assert result == {"Violence": 1}


class TestClose:
    async def test_close_awaits_client_close(self, monkeypatch):
        rai = _make_rai(monkeypatch)
        rai.client.close = AsyncMock()
        await rai.close()
        rai.client.close.assert_awaited_once()
