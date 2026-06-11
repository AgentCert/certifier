"""Unit tests for utils.load_config (EnvLoader and ConfigLoader)."""

import json

import pytest

from utils.load_config import ConfigLoader, EnvLoader


class TestEnvLoader:
    def test_missing_optional_returns_none(self, monkeypatch):
        monkeypatch.delenv("SOME_MISSING_VAR", raising=False)
        assert EnvLoader.load_env_vars("SOME_MISSING_VAR") is None

    def test_missing_compulsory_raises(self, monkeypatch):
        monkeypatch.delenv("REQUIRED_VAR", raising=False)
        with pytest.raises(ValueError, match="Compulsory environment variable"):
            EnvLoader.load_env_vars("REQUIRED_VAR", compulsory=True)

    def test_plain_string_value(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "hello")
        assert EnvLoader.load_env_vars("MY_VAR") == "hello"

    @pytest.mark.parametrize("truthy", ["True", "true", "TRUE"])
    def test_true_variants_coerced_to_bool(self, monkeypatch, truthy):
        monkeypatch.setenv("BOOL_VAR", truthy)
        assert EnvLoader.load_env_vars("BOOL_VAR") is True

    @pytest.mark.parametrize("falsy", ["False", "false", "FALSE"])
    def test_false_variants_coerced_to_bool(self, monkeypatch, falsy):
        monkeypatch.setenv("BOOL_VAR", falsy)
        assert EnvLoader.load_env_vars("BOOL_VAR") is False

    def test_present_compulsory_returns_value(self, monkeypatch):
        monkeypatch.setenv("REQUIRED_VAR", "present")
        assert EnvLoader.load_env_vars("REQUIRED_VAR", compulsory=True) == "present"


class TestResolveEnvValues:
    def test_env_prefixed_string_resolved(self, monkeypatch):
        monkeypatch.setenv("DB_URL", "mongodb://host")
        assert ConfigLoader._resolve_env_values("ENV_DB_URL") == "mongodb://host"

    def test_non_env_string_unchanged(self):
        assert ConfigLoader._resolve_env_values("plain") == "plain"

    def test_nested_dict_resolution(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "secret123")
        data = {"outer": {"key": "ENV_API_KEY", "literal": "stay"}}
        result = ConfigLoader._resolve_env_values(data)
        assert result == {"outer": {"key": "secret123", "literal": "stay"}}

    def test_list_resolution(self, monkeypatch):
        monkeypatch.setenv("ITEM", "resolved")
        data = ["ENV_ITEM", "literal", 42]
        assert ConfigLoader._resolve_env_values(data) == ["resolved", "literal", 42]

    def test_primitive_passthrough(self):
        assert ConfigLoader._resolve_env_values(123) == 123
        assert ConfigLoader._resolve_env_values(True) is True
        assert ConfigLoader._resolve_env_values(None) is None

    def test_env_bool_value_resolved(self, monkeypatch):
        monkeypatch.setenv("FLAG", "true")
        assert ConfigLoader._resolve_env_values("ENV_FLAG") is True

    def test_missing_env_resolves_to_none(self, monkeypatch):
        monkeypatch.delenv("NOPE", raising=False)
        assert ConfigLoader._resolve_env_values("ENV_NOPE") is None


class TestLoadConfig:
    def test_missing_file_raises(self, monkeypatch, tmp_path):
        import utils.load_config as lc

        monkeypatch.setattr(lc, "Path", lambda *a, **k: _FakeFile(tmp_path))
        # no configs/configs.json created
        with pytest.raises(FileNotFoundError, match="Configuration file not found"):
            ConfigLoader.load_config()

    def test_loads_and_resolves(self, monkeypatch, tmp_path):
        import utils.load_config as lc

        configs_dir = tmp_path / "configs"
        configs_dir.mkdir()
        (configs_dir / "configs.json").write_text(
            json.dumps({"db": "ENV_MY_DB", "static": "value"})
        )
        monkeypatch.setenv("MY_DB", "mongodb://resolved")
        monkeypatch.setattr(lc, "Path", lambda *a, **k: _FakeFile(tmp_path))

        result = ConfigLoader.load_config()
        assert result == {"db": "mongodb://resolved", "static": "value"}

    def test_bad_json_raises(self, monkeypatch, tmp_path):
        import utils.load_config as lc

        configs_dir = tmp_path / "configs"
        configs_dir.mkdir()
        (configs_dir / "configs.json").write_text("{ not valid json ]")
        monkeypatch.setattr(lc, "Path", lambda *a, **k: _FakeFile(tmp_path))

        with pytest.raises(json.JSONDecodeError):
            ConfigLoader.load_config()


# --- Helpers to fake the Path(__file__).parent.parent chain. ---
# load_config does: current_dir = Path(__file__).parent.parent
# then current_dir / "configs" / "configs.json"
# _FakeFile stands in for Path(__file__); .parent.parent yields the tmp root.
class _FakeFile:
    """Stands in for Path(__file__); .parent.parent returns the tmp root."""

    def __init__(self, tmp_root):
        self._tmp_root = tmp_root

    @property
    def parent(self):
        # First .parent -> a node whose .parent is the real tmp root path.
        return _OneMoreParent(self._tmp_root)


class _OneMoreParent:
    def __init__(self, tmp_root):
        self._tmp_root = tmp_root

    @property
    def parent(self):
        from pathlib import Path as _RealPath

        return _RealPath(self._tmp_root)
