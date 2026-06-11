"""Unit tests for cert_reporter.pipeline.charts.

The actual image render (vl-convert / altair) is patched off so the
deterministic spec-building and the SVG fallback are exercised without
any native rendering backend.
"""

from __future__ import annotations

import pytest

import cert_reporter.pipeline.charts as charts
from cert_reporter.pipeline.charts import (
    _build_ci_bar,
    _build_grouped_bar,
    _build_heatmap,
    _build_line,
    _build_placeholder,
    _build_radar,
    _build_stacked_bar,
    _render_chart,
    _score_colour,
    _spec_to_svg,
    charts_node,
)


@pytest.fixture(autouse=True)
def _no_render_backend(monkeypatch):
    """Force the placeholder SVG path: no vl-convert, no altair."""
    monkeypatch.setattr(charts, "_VLC_AVAILABLE", False)
    monkeypatch.setattr(charts, "_ALT_AVAILABLE", False)


class TestScoreColour:
    @pytest.mark.parametrize(
        "score,expected",
        [
            (0.95, "#2ecc71"),
            (0.90, "#2ecc71"),
            (0.80, "#3498db"),
            (0.75, "#3498db"),
            (0.65, "#f39c12"),
            (0.60, "#f39c12"),
            (0.10, "#e74c3c"),
        ],
    )
    def test_thresholds(self, score, expected):
        assert _score_colour(score) == expected


class TestSpecToSvg:
    def test_fallback_placeholder_svg(self):
        svg = _spec_to_svg({"$schema": "vega-lite"}, 600, 400)
        assert svg.startswith("<svg")
        assert 'width="600"' in svg
        assert 'height="400"' in svg
        assert "Chart unavailable" in svg

    def test_vlc_vegalite_path(self, monkeypatch):
        monkeypatch.setattr(charts, "_VLC_AVAILABLE", True)
        fake = type("VLC", (), {})()
        fake.vegalite_to_svg = lambda s: "<svg>vl</svg>"
        fake.vega_to_svg = lambda s: "<svg>vega</svg>"
        monkeypatch.setattr(charts, "vlc", fake, raising=False)
        out = _spec_to_svg({"$schema": "vega-lite/v5"}, 1, 1)
        assert out == "<svg>vl</svg>"

    def test_vlc_vega_path(self, monkeypatch):
        monkeypatch.setattr(charts, "_VLC_AVAILABLE", True)
        fake = type("VLC", (), {})()
        fake.vegalite_to_svg = lambda s: "<svg>vl</svg>"
        fake.vega_to_svg = lambda s: "<svg>vega</svg>"
        monkeypatch.setattr(charts, "vlc", fake, raising=False)
        out = _spec_to_svg({"$schema": "https://vega.github.io/schema/vega/v5.json"}, 1, 1)
        assert out == "<svg>vega</svg>"

    def test_vlc_failure_falls_back(self, monkeypatch):
        monkeypatch.setattr(charts, "_VLC_AVAILABLE", True)
        fake = type("VLC", (), {})()

        def boom(_):
            raise RuntimeError("native crash")

        fake.vegalite_to_svg = boom
        fake.vega_to_svg = boom
        monkeypatch.setattr(charts, "vlc", fake, raising=False)
        out = _spec_to_svg({"$schema": "vl"}, 10, 20)
        assert "Chart unavailable" in out


class TestBuilders:
    def test_radar_builds_vega_spec(self):
        block = {
            "dimensions": [
                {"dimension": "Accuracy", "value": 0.8},
                {"dimension": "Safety", "value": 1.5},  # clamped to 1.0
            ],
            "reference_polygons": [
                {"values": [0.5, 0.5], "label": "Threshold", "line_dash": "dot"}
            ],
        }
        spec = _build_radar(block)
        assert "/schema/vega/" in spec["$schema"]
        # table data has clamped values
        vals = [r["value"] for r in spec["data"][0]["values"]]
        assert vals == [0.8, 1.0]
        # reference dataset present
        names = [d["name"] for d in spec["data"]]
        assert "ref_0" in names

    def test_radar_empty_dims_placeholder(self):
        spec = _build_radar({"dimensions": []})
        assert spec["mark"]["text"] == "No data"

    def test_radar_ref_polygon_wrong_length_skipped(self):
        block = {
            "dimensions": [{"dimension": "A", "value": 0.5}],
            "reference_polygons": [{"values": [0.1, 0.2], "label": "X"}],
        }
        spec = _build_radar(block)
        names = [d["name"] for d in spec["data"]]
        assert "ref_0" not in names

    def test_grouped_bar_flattens_rows(self):
        block = {
            "categories": ["c1", "c2"],
            "series": [{"name": "S", "values": [1, 2]}],
            "reference_lines": [{"value": 1.5, "label": "ref"}],
        }
        spec = _build_grouped_bar(block)
        flat = spec["data"]["values"]
        assert {"category": "c1", "series": "S", "value": 1.0} in flat
        # ref line added two extra layers (rule + text) on top of the bar layer
        assert len(spec["layer"]) == 3

    def test_grouped_bar_bad_value_coerced_zero(self):
        block = {"categories": ["c"], "series": [{"name": "S", "values": ["nan!"]}]}
        spec = _build_grouped_bar(block)
        assert spec["data"]["values"][0]["value"] == 0.0

    def test_grouped_bar_missing_data_placeholder(self):
        assert _build_grouped_bar({"categories": [], "series": []})["mark"]["text"] == "No data"

    def test_stacked_bar(self):
        block = {"categories": ["a"], "series": [{"name": "S", "values": [3]}], "y_axis": "Y"}
        spec = _build_stacked_bar(block)
        assert spec["encoding"]["y"]["stack"] is True
        assert spec["data"]["values"][0]["value"] == 3.0

    def test_stacked_bar_placeholder(self):
        assert _build_stacked_bar({})["mark"]["text"] == "No data"

    def test_heatmap(self):
        block = {
            "x_labels": ["m1", "m2"],
            "y_labels": ["r1"],
            "values": [[0.1, 0.9]],
            "display_values": [["lo", "hi"]],
        }
        spec = _build_heatmap(block)
        flat = spec["data"]["values"]
        assert len(flat) == 2
        assert flat[1]["display"] == "hi"
        assert flat[1]["value"] == 0.9

    def test_heatmap_placeholder(self):
        assert _build_heatmap({"x_labels": [], "y_labels": [], "values": []})["mark"]["text"] == "No data"

    def test_heatmap_none_value_coerced(self):
        block = {"x_labels": ["x"], "y_labels": ["y"], "values": [[None]]}
        spec = _build_heatmap(block)
        assert spec["data"]["values"][0]["value"] == 0.0

    def test_ci_bar_with_groups(self):
        block = {
            "points": [
                {"label": "p1", "value": 0.5, "ci_low": 0.4, "ci_high": 0.6, "group": "g1"},
            ],
        }
        spec = _build_ci_bar(block)
        rows = spec["data"]["values"]
        assert rows[0]["group"] == "g1"
        # rule encoding carries color when grouped
        assert "color" in spec["layer"][0]["encoding"]

    def test_ci_bar_bad_numbers(self):
        block = {"points": [{"label": "p", "value": "x", "ci_low": "y", "ci_high": "z"}]}
        spec = _build_ci_bar(block)
        row = spec["data"]["values"][0]
        assert row["value"] == 0.0
        assert row["ci_low"] == 0.0 and row["ci_high"] == 0.0

    def test_ci_bar_placeholder(self):
        assert _build_ci_bar({"points": []})["mark"]["text"] == "No data"

    def test_line(self):
        block = {"categories": ["t0", "t1"], "series": [{"name": "S", "values": [1, 2]}]}
        spec = _build_line(block)
        assert spec["mark"]["type"] == "line"
        assert len(spec["data"]["values"]) == 2

    def test_line_placeholder(self):
        assert _build_line({})["mark"]["text"] == "No data"

    def test_placeholder_shape(self):
        spec = _build_placeholder({})
        assert spec["mark"]["text"] == "No data"


class TestRenderChart:
    def test_unknown_type_returns_error_result(self):
        block = {"_chart_id": "x", "chart_type": "bogus", "title": "T"}
        res = _render_chart(block)
        assert res.error == "Unknown chart type: bogus"
        assert res.svg == ""

    def test_known_type_renders_fallback_svg(self):
        block = {
            "_chart_id": "c1",
            "chart_type": "line",
            "title": "L",
            "categories": ["a"],
            "series": [{"name": "S", "values": [1]}],
            "width_px": 300,
            "height_px": 200,
        }
        res = _render_chart(block)
        assert res.error is None
        assert "Chart unavailable" in res.svg
        assert res.width_px == 300
        assert res.alt_text == "L"

    def test_builder_exception_captured(self, monkeypatch):
        def boom(_):
            raise ValueError("builder boom")

        monkeypatch.setitem(charts._BUILDERS, "line", boom)
        res = _render_chart({"_chart_id": "c", "chart_type": "line", "title": "T"})
        assert "builder boom" in res.error


class TestChartsNode:
    def test_renders_all_blocks_keyed_by_id(self):
        state = {
            "charts_to_render": [
                {"_chart_id": "c1", "chart_type": "line",
                 "categories": ["a"], "series": [{"name": "S", "values": [1]}]},
                {"_chart_id": "c2", "chart_type": "bogus"},
            ],
            "verbose": True,
        }
        out = charts_node(state)
        results = out["chart_results"]
        assert set(results) == {"c1", "c2"}
        assert results["c2"].error is not None

    def test_empty_charts(self):
        out = charts_node({"charts_to_render": []})
        assert out["chart_results"] == {}
