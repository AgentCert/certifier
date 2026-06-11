"""Unit tests for cert_builder/scripts/computation/chart_builder.py.

All chart *data* construction is deterministic. Image rendering (render=True)
delegates to chart_renderer.render_all, which is patched out so no plotly /
matplotlib call happens.
"""

import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from cert_builder.scripts.computation import chart_builder as cb
from cert_builder.tests._fixtures import make_category, make_meta

_DIMS = [
    {"dimension": "Detection Rate", "value": 0.8},
    {"dimension": "Safety (RAI)", "value": 1.0},
    {"dimension": "Privacy & Security", "value": 0.9},
]


def test_safe_get_default_float():
    assert cb._safe_get({"a": None}, "a") == 0.0
    assert cb._safe_get({"a": {"b": 2}}, "a", "b") == 2


def test_clamp():
    assert cb._clamp(1.5) == 1.0
    assert cb._clamp(-0.2) == 0.0


def test_labels():
    assert cb._labels([make_category()]) == ["Application"]


# ── scorecard radar ──────────────────────────────────────────────────

def test_scorecard_radar_threshold_polygon():
    out = cb._build_scorecard_radar(_DIMS)
    poly = out["reference_polygons"][0]["values"]
    # Safety / Privacy -> 1.0, others -> 0.75
    assert poly == [0.75, 1.0, 1.0]
    assert out["chart_type"] == "radar"
    assert out["dimensions"] == _DIMS


# ── RAI radar / sublabel ─────────────────────────────────────────────

@pytest.mark.parametrize("key,pct,gates,expected", [
    ("privacy_security", 92, {"privacy_security_passed": True}, ("92% ✓ Pass", "#27ae60")),
    ("privacy_security", 80, {"privacy_security_passed": True}, ("80% △ Review", "#e67e22")),
    ("privacy_security", 50, {"privacy_security_passed": False}, ("50% ✗ Fail", "#e74c3c")),
    ("transparency", 90, {}, ("90% ✓ Pass", "#27ae60")),
    ("transparency", 60, {}, ("60% △ Review", "#e67e22")),
    ("fairness", 10, {}, ("10% ✗ Fail", "#e74c3c")),
])
def test_rai_sublabel(key, pct, gates, expected):
    assert cb._rai_sublabel(key, pct, gates) == expected


def test_rai_radar_dimensions():
    out = cb._build_rai_radar(make_meta()["responsible_ai"])
    labels = [d["dimension"] for d in out["dimensions"]]
    assert labels == ["Privacy & Security", "Transparency", "Fairness"]
    assert out["dimensions"][0]["value"] == 0.92


def test_rai_radar_handles_none_score():
    rai = {"principles": {"privacy_security": {"label": "PS", "score": None}},
           "gates": {}}
    out = cb._build_rai_radar(rai)
    # None score -> placeholder 0.0
    assert out["dimensions"][0]["value"] == 0.0


# ── bar charts ───────────────────────────────────────────────────────

def test_ttd_bar_percentages():
    out = cb._build_ttd_bar([make_category()])
    assert out["series"][0]["values"] == [80.0]   # detection_rate*100
    assert out["series"][1]["values"] == [50.0]   # sla_compliance*100


def test_rates_bar_with_reference_line():
    out = cb._build_rates_bar([make_category()])
    assert out["series"][0]["values"] == [80.0]
    assert out["series"][1]["values"] == [70.0]
    assert out["reference_lines"][0]["value"] == 50.0  # config 0.5 * 100


def test_reasoning_bar():
    out = cb._build_reasoning_bar([make_category()])
    assert out["series"][0]["values"] == [0.8]


def test_hallucination_bar_inverts():
    cat = make_category()
    cat["numeric"]["hallucination_score"] = {"mean": 0.2, "max": 0.4}
    out = cb._build_hallucination_bar([cat])
    assert out["series"][0]["values"] == [0.8]   # 1 - 0.2
    assert out["series"][1]["values"] == [0.6]   # 1 - 0.4


# ── heatmap ──────────────────────────────────────────────────────────

def test_accuracy_heatmap_values():
    out = cb._build_accuracy_heatmap([make_category()])
    assert out["y_labels"] == ["Application"]
    assert out["x_labels"] == ["Action Correctness", "Reasoning Score", "Hallucination Control"]
    # action 1.0, reasoning 0.8/scale(1)=0.8, hallu ctrl 1-0/1=1.0
    assert out["values"] == [[1.0, 0.8, 1.0]]


def test_accuracy_heatmap_missing_action_is_none():
    cat = make_category()
    cat["numeric"]["action_correctness"] = {}
    out = cb._build_accuracy_heatmap([cat])
    assert out["values"][0][0] is None
    assert out["display_values"][0][0] is None


# ── token line chart ─────────────────────────────────────────────────

def test_token_stacked_empty():
    out = cb._build_token_stacked([], None)
    assert out["categories"] == ["No data"]
    assert out["series"][0]["values"] == [0]


def test_token_stacked_pads_shorter_series():
    out = cb._build_token_stacked([], {"input_tokens": [1, 2, 3], "output_tokens": [4]})
    assert out["categories"] == ["Run 1", "Run 2", "Run 3"]
    assert out["series"][1]["values"] == [4, 0, 0]


def test_compute_mean_tokens():
    out = cb._compute_mean_tokens({"input_tokens": [10, 20], "output_tokens": [4, 6]})
    assert out["mean_input_tokens"] == 15.0
    assert out["median_output_tokens"] == 5.0
    assert out["total_input_tokens"] == 30
    assert out["max_output_tokens"] == 6


def test_compute_mean_tokens_empty():
    out = cb._compute_mean_tokens(None)
    assert out["mean_input_tokens"] is None
    out2 = cb._compute_mean_tokens({"input_tokens": [], "output_tokens": []})
    assert out2["mean_input_tokens"] is None


# ── public API ───────────────────────────────────────────────────────

def test_build_all_charts_no_render():
    out = cb.build_all_charts([make_category()], _DIMS,
                              run_level_tokens={"input_tokens": [1], "output_tokens": [2]},
                              responsible_ai=make_meta()["responsible_ai"])
    assert set(out["charts"]) == {
        "scorecard_radar", "ttd_bar", "ttm_bar", "rates_bar",
        "accuracy_heatmap", "reasoning_bar", "hallucination_bar",
        "token_stacked", "rai_radar"}


def test_build_all_charts_render_delegates_to_renderer(tmp_path):
    # chart_renderer imports plotly (not installed here); inject a stub module
    # so build_all_charts can `from .chart_renderer import render_all`.
    stub = types.ModuleType("cert_builder.scripts.computation.chart_renderer")
    stub.render_all = MagicMock()
    with patch.dict(
        sys.modules,
        {"cert_builder.scripts.computation.chart_renderer": stub},
    ):
        cb.build_all_charts([make_category()], _DIMS,
                            run_level_tokens={"input_tokens": [1], "output_tokens": [2]},
                            responsible_ai=make_meta()["responsible_ai"],
                            render=True, output_dir=str(tmp_path))
        assert stub.render_all.called


def test_build_from_file_adds_mean_tokens(tmp_path):
    meta = make_meta()
    meta["run_level_tokens"] = {"input_tokens": [100, 200], "output_tokens": [50, 60]}
    ctx = {"categories": [make_category()], "meta": meta}
    p = tmp_path / "phase1.json"
    p.write_text(json.dumps(ctx))
    out = cb.build_from_file(p, _DIMS)
    assert out["charts"]["token_stacked"]["mean_tokens"]["mean_input_tokens"] == 150.0
