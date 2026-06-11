"""Unit tests for mock_trace_generator.scripts.tools_registry."""

from mock_trace_generator.scripts.tools_registry import AVAILABLE_TOOLS


class TestRegistryStructure:
    def test_is_dict(self):
        assert isinstance(AVAILABLE_TOOLS, dict)
        assert len(AVAILABLE_TOOLS) > 0

    def test_every_entry_has_required_keys(self):
        for key, val in AVAILABLE_TOOLS.items():
            assert isinstance(key, str) and key
            assert set(val.keys()) == {"name", "description", "category"}
            assert all(isinstance(val[k], str) and val[k] for k in val)

    def test_categories_are_kubernetes_or_prometheus(self):
        cats = {v["category"] for v in AVAILABLE_TOOLS.values()}
        assert cats == {"kubernetes", "prometheus"}

    def test_kubernetes_keys_prefixed_k8s(self):
        for key, val in AVAILABLE_TOOLS.items():
            if val["category"] == "kubernetes":
                assert key.startswith("k8s_")

    def test_prometheus_keys_prefixed_prom(self):
        for key, val in AVAILABLE_TOOLS.items():
            if val["category"] == "prometheus":
                assert key.startswith("prom_")

    def test_keys_unique_count(self):
        # 19 kubernetes + 6 prometheus = 25 tools as defined in source.
        k8s = [k for k, v in AVAILABLE_TOOLS.items() if v["category"] == "kubernetes"]
        prom = [k for k, v in AVAILABLE_TOOLS.items() if v["category"] == "prometheus"]
        assert len(k8s) == 19
        assert len(prom) == 6
        assert len(AVAILABLE_TOOLS) == 25


class TestRegistryLookups:
    def test_known_lookup(self):
        assert AVAILABLE_TOOLS["k8s_pods_log"]["name"] == "Pods: Log"
        assert AVAILABLE_TOOLS["prom_query"]["category"] == "prometheus"

    def test_missing_key_get_default(self):
        assert AVAILABLE_TOOLS.get("does_not_exist") is None
        assert AVAILABLE_TOOLS.get("does_not_exist", {}) == {}

    def test_expected_keys_present(self):
        for k in [
            "k8s_pods_delete",
            "k8s_pods_list",
            "k8s_nodes_top",
            "k8s_events_list",
            "prom_query",
            "prom_health_check",
        ]:
            assert k in AVAILABLE_TOOLS


class TestRegistryRegistration:
    def test_can_register_and_restore(self):
        # The registry is a plain dict, so registration is mutation.
        sentinel = {"name": "X", "description": "Y", "category": "kubernetes"}
        AVAILABLE_TOOLS["k8s_test_tool"] = sentinel
        try:
            assert AVAILABLE_TOOLS["k8s_test_tool"] is sentinel
        finally:
            del AVAILABLE_TOOLS["k8s_test_tool"]
        assert "k8s_test_tool" not in AVAILABLE_TOOLS
