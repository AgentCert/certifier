"""Unit tests for mock_trace_generator.main CLI entry point.

External dependencies (ConfigLoader, AzureLLMClient, generator) are patched;
no real Azure / network / filesystem-config access occurs. argv is set
explicitly per test.
"""

import argparse
import json

import pytest

import mock_trace_generator.main as main_mod
from mock_trace_generator.main import (
    _load_agent_metadata,
    _parse_fault_arg,
    main,
)
from mock_trace_generator.schema.data_models import FaultDefinition


class TestParseFaultArg:
    def test_valid(self):
        fd = _parse_fault_arg("pod-delete:Deletes a running pod")
        assert isinstance(fd, FaultDefinition)
        assert fd.name == "pod-delete"
        assert fd.description == "Deletes a running pod"

    def test_strips_whitespace(self):
        fd = _parse_fault_arg("  name  :  desc with spaces  ")
        assert fd.name == "name"
        assert fd.description == "desc with spaces"

    def test_only_first_colon_splits(self):
        fd = _parse_fault_arg("name:desc:with:colons")
        assert fd.name == "name"
        assert fd.description == "desc:with:colons"

    def test_missing_colon_raises(self):
        with pytest.raises(argparse.ArgumentTypeError, match="Invalid fault format"):
            _parse_fault_arg("no-colon-here")


class TestLoadAgentMetadata:
    def test_loads_real_package_config(self):
        # The package ships config/multi_fault_config.json with agent_defaults.
        meta = _load_agent_metadata()
        assert isinstance(meta, dict)
        assert meta.get("agent_name") == "ITOps Autonomous Agent"
        assert "agent_capabilities" in meta

    def test_returns_empty_when_config_missing(self, monkeypatch, tmp_path):
        # Point __file__-derived path at a dir with no config file.
        fake_file = tmp_path / "main.py"
        monkeypatch.setattr(main_mod, "__file__", str(fake_file))
        meta = _load_agent_metadata()
        assert meta == {}

    def test_returns_empty_dict_when_no_agent_defaults_key(
        self, monkeypatch, tmp_path
    ):
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "multi_fault_config.json").write_text(json.dumps({"other": 1}))
        monkeypatch.setattr(main_mod, "__file__", str(tmp_path / "main.py"))
        assert _load_agent_metadata() == {}


def _set_argv(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["prog", *args])


class TestMainValidation:
    async def test_no_faults_errors(self, monkeypatch):
        _set_argv(monkeypatch)
        with pytest.raises(SystemExit):
            await main()

    async def test_single_fault_errors(self, monkeypatch):
        _set_argv(monkeypatch, "--fault", "only-one:just one fault")
        with pytest.raises(SystemExit):
            await main()

    async def test_missing_faults_file_errors(self, monkeypatch, tmp_path):
        _set_argv(monkeypatch, "--faults-file", str(tmp_path / "nope.json"))
        with pytest.raises(SystemExit):
            await main()


class TestMainHappyPath:
    async def test_generates_traces_with_two_faults(
        self, monkeypatch, tmp_path, capsys
    ):
        saved = []

        class FakeGen:
            generate_experiment_id = staticmethod(
                main_mod.MultiFaultTraceGenerator.generate_experiment_id
            )

            def __init__(self, llm_client, model_name, agent_metadata):
                self.llm_client = llm_client
                self.model_name = model_name
                self.agent_metadata = agent_metadata

            async def generate_and_save(
                self, faults, output_dir, num_detection_cycles, agent_id,
                experiment_id, run_id,
            ):
                p = tmp_path / f"trace-{run_id}.json"
                p.write_text("[]")
                saved.append(
                    {
                        "faults": [f.name for f in faults],
                        "output_dir": output_dir,
                        "agent_id": agent_id,
                        "experiment_id": experiment_id,
                        "run_id": run_id,
                    }
                )
                return p

        class FakeLLM:
            def __init__(self, config):
                self.config = config
                self.closed = False

            async def close(self):
                self.closed = True

        fake_llm_holder = {}

        def _llm_factory(config):
            inst = FakeLLM(config)
            fake_llm_holder["inst"] = inst
            return inst

        monkeypatch.setattr(main_mod, "ConfigLoader", type("C", (), {"load_config": staticmethod(lambda: {"k": "v"})}))
        monkeypatch.setattr(main_mod, "AzureLLMClient", _llm_factory)
        monkeypatch.setattr(main_mod, "MultiFaultTraceGenerator", FakeGen)

        _set_argv(
            monkeypatch,
            "--fault", "pod-delete:Deletes a pod",
            "--fault", "disk-fill:Fills the disk",
            "--output-dir", str(tmp_path),
            "--num-traces", "2",
            "--agent-id", "agent-fixed",
        )

        await main()

        # two traces generated, both with the fixed agent id and identical experiment id
        assert len(saved) == 2
        assert all(s["agent_id"] == "agent-fixed" for s in saved)
        exp_ids = {s["experiment_id"] for s in saved}
        assert len(exp_ids) == 1
        # run_ids differ per trace
        assert len({s["run_id"] for s in saved}) == 2
        # llm client closed at the end
        assert fake_llm_holder["inst"].closed is True

    async def test_loads_faults_from_file(self, monkeypatch, tmp_path):
        faults_file = tmp_path / "faults.json"
        faults_file.write_text(
            json.dumps(
                [
                    {"name": "a", "description": "fault a"},
                    {"name": "b", "description": "fault b"},
                ]
            )
        )
        captured = {}

        class FakeGen:
            generate_experiment_id = staticmethod(
                main_mod.MultiFaultTraceGenerator.generate_experiment_id
            )

            def __init__(self, **kwargs):
                pass

            async def generate_and_save(self, faults, **kwargs):
                captured["faults"] = [f.name for f in faults]
                p = tmp_path / "out.json"
                p.write_text("[]")
                return p

        class FakeLLM:
            def __init__(self, config):
                pass

            async def close(self):
                pass

        monkeypatch.setattr(main_mod, "ConfigLoader", type("C", (), {"load_config": staticmethod(lambda: {})}))
        monkeypatch.setattr(main_mod, "AzureLLMClient", FakeLLM)
        monkeypatch.setattr(main_mod, "MultiFaultTraceGenerator", FakeGen)

        _set_argv(
            monkeypatch,
            "--faults-file", str(faults_file),
            "--output-dir", str(tmp_path),
        )
        await main()
        assert captured["faults"] == ["a", "b"]

    async def test_interactive_mode_collects_faults(self, monkeypatch, tmp_path):
        captured = {}

        class FakeGen:
            generate_experiment_id = staticmethod(
                main_mod.MultiFaultTraceGenerator.generate_experiment_id
            )

            def __init__(self, **kwargs):
                pass

            async def generate_and_save(self, faults, **kwargs):
                captured["faults"] = [f.name for f in faults]
                p = tmp_path / "out.json"
                p.write_text("[]")
                return p

        class FakeLLM:
            def __init__(self, config):
                pass

            async def close(self):  # noqa: D401 - test stub
                return None

        monkeypatch.setattr(main_mod, "ConfigLoader", type("C", (), {"load_config": staticmethod(lambda: {})}))
        monkeypatch.setattr(main_mod, "AzureLLMClient", FakeLLM)
        monkeypatch.setattr(main_mod, "MultiFaultTraceGenerator", FakeGen)

        # Scripted interactive input: two faults then 'done'.
        inputs = iter(
            [
                "pod-delete", "deletes a pod",
                "disk-fill", "fills disk",
                "done",
            ]
        )
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        _set_argv(monkeypatch, "--interactive", "--output-dir", str(tmp_path))
        await main()
        assert captured["faults"] == ["pod-delete", "disk-fill"]

    async def test_exits_when_utils_unavailable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(main_mod, "ConfigLoader", None)
        monkeypatch.setattr(main_mod, "AzureLLMClient", None)
        _set_argv(
            monkeypatch,
            "--fault", "a:fa",
            "--fault", "b:fb",
            "--output-dir", str(tmp_path),
        )
        with pytest.raises(SystemExit):
            await main()
