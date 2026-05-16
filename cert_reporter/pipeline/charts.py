"""charts_node — renders chart content blocks to SVG using Altair + vl-convert.

Updated for the canonical certification framework chart block types:
  radar, grouped_bar, stacked_bar, heatmap
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

from .parameters import ChartResult, GraphState

log = logging.getLogger(__name__)

# Try to import Altair and vl-convert; provide graceful fallback
try:
    import altair as alt
    _ALT_AVAILABLE = True
except ImportError:
    _ALT_AVAILABLE = False

try:
    import vl_convert as vlc
    _VLC_AVAILABLE = True
except ImportError:
    _VLC_AVAILABLE = False


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

_CATEGORY_COLOURS = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2",
    "#59a14f", "#edc948", "#b07aa1", "#ff9da7",
]

def _score_colour(score: float) -> str:
    """Map a 0-1 score to a hex colour."""
    if score >= 0.90:
        return "#2ecc71"   # green — excellent
    if score >= 0.75:
        return "#3498db"   # blue — good
    if score >= 0.60:
        return "#f39c12"   # amber — adequate
    return "#e74c3c"       # red — needs improvement


# ---------------------------------------------------------------------------
# SVG rendering
# ---------------------------------------------------------------------------

def _spec_to_svg(spec: dict[str, Any], width: int, height: int) -> str:
    """Convert a Vega-Lite (or full Vega) spec dict → SVG string via vl-convert."""
    schema = spec.get("$schema", "") if isinstance(spec, dict) else ""
    is_vega = "/schema/vega/" in schema

    if _VLC_AVAILABLE:
        try:
            spec_json = json.dumps(spec)
            if is_vega:
                return vlc.vega_to_svg(spec_json)
            return vlc.vegalite_to_svg(spec_json)
        except Exception as exc:
            log.warning("vl-convert failed: %s", exc)

    if _ALT_AVAILABLE and not is_vega:
        try:
            import io
            chart = alt.Chart.from_dict(spec)
            buf = io.StringIO()
            chart.save(buf, format="svg")
            return buf.getvalue()
        except Exception as exc:
            log.warning("altair save failed: %s", exc)

    # Fallback: plain SVG placeholder
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
        f'<rect width="100%" height="100%" fill="#f5f5f5" rx="8"/>'
        f'<text x="50%" y="50%" text-anchor="middle" dominant-baseline="middle" '
        f'font-family="sans-serif" font-size="14" fill="#999">Chart unavailable</text>'
        f'</svg>'
    )


# ---------------------------------------------------------------------------
# Chart builders — one per framework chart_type
# ---------------------------------------------------------------------------

def _build_radar(block: dict[str, Any]) -> dict[str, Any]:
    """Radar chart from dimensions: [{dimension, value}].

    Values are 0-1 normalised scores where higher is better. Emits a full
    Vega 5 spec that renders:
      * a real polar radar (spokes + concentric grid web)
      * a closed filled polygon for the agent values (fixed purple — the
        chart colour is independent of the agent's score and matches the
        §1.3.2 caption wording in report_assembler.py)
      * any optional ``reference_polygons`` (e.g. the green dashed
        Performance Threshold) as additional closed line marks
      * a right-side legend listing each dimension's value with an
        explicit "↑ higher is better" annotation

    Numeric values are intentionally NOT drawn next to each vertex —
    the right-side legend lists every value, so per-vertex labels would
    only duplicate that information and crowd the axis labels.
    """
    dims = block.get("dimensions", [])
    if not dims:
        return _build_placeholder(block)

    rows: list[dict[str, Any]] = []
    for d in dims:
        if isinstance(d, dict):
            label = str(d.get("dimension", "?"))
            val = float(d.get("value", 0))
        elif hasattr(d, "dimension"):
            label = str(d.dimension)
            val = float(getattr(d, "value", 0))
        else:
            continue
        rows.append({"key": label, "value": max(0.0, min(1.0, val))})

    if not rows:
        return _build_placeholder(block)

    # Fixed agent colour (purple) — kept stable so the radar consistently
    # reads as "this agent's performance" regardless of overall score.
    agent_stroke = "#5b44ba"

    # Reference polygons (e.g. Performance Threshold). Each is rendered as a
    # closed dashed line over the radar.
    ref_polys = block.get("reference_polygons") or []
    ref_datasets: list[dict[str, Any]] = []
    for i, rp in enumerate(ref_polys):
        if isinstance(rp, dict):
            values = rp.get("values") or []
            label = str(rp.get("label", f"Reference {i + 1}"))
            line_color = str(rp.get("line_color") or "#1a7f37")
            line_dash = str(rp.get("line_dash") or "dash")
        else:
            continue
        if len(values) != len(rows):
            continue
        ref_rows = [
            {"key": rows[j]["key"], "value": max(0.0, min(1.0, float(values[j])))}
            for j in range(len(rows))
        ]
        # Convert friendly dash names to Vega numeric strokeDash arrays.
        if line_dash in ("dash", "dashed"):
            dash_array = [6, 4]
        elif line_dash in ("dot", "dotted"):
            dash_array = [2, 3]
        else:
            dash_array = []
        ref_datasets.append({
            "name": f"ref_{i}",
            "label": label,
            "color": line_color,
            "dash": dash_array,
            "rows": ref_rows,
        })

    # Legend rows (right-hand side panel). Anchor y is set per-row so the
    # block reads top-to-bottom alongside the radar.
    legend_rows = [
        {"key": r["key"], "value": r["value"], "idx": i}
        for i, r in enumerate(rows)
    ]

    grid_levels = [0.2, 0.4, 0.6, 0.8, 1.0]
    grid_rows = [
        {"level": lv, "key": r["key"], "value": lv}
        for lv in grid_levels
        for r in rows
    ]
    tick_rows = [{"v": lv} for lv in grid_levels]

    # Layout: left portion holds the radar (with outer axis labels), right
    # portion holds the legend. Canvas is wide enough to keep the widest
    # axis labels ("Hallucination Ctrl", "Action Correctness", ~110-120 px)
    # off both the left edge and the legend column.
    width = 780
    height = 340

    spec: dict[str, Any] = {
        "$schema": "https://vega.github.io/schema/vega/v5.json",
        "width": width,
        "height": height,
        "padding": {"left": 20, "right": 20, "top": 30, "bottom": 30},
        "autosize": {"type": "none"},
        "background": "transparent",
        "signals": [
            # Radar centre. cx leaves room on the left for the widest
            # anchor=end axis label; radius is conservative so axis
            # labels at radius+22 still clear the legend column.
            {"name": "cx", "value": 260},
            {"name": "cy", "value": 170},
            {"name": "radius", "value": 120},
            # Legend anchor (top-left of the legend block). Set so the
            # rightmost axis label (cx + radius + 22 + ~120) ends before
            # the legend names begin.
            {"name": "legendX", "value": 540},
            {"name": "legendYTop", "update": "cy - radius + 6"},
            {"name": "legendStep", "value": 28},
        ],
        "data": [
            {"name": "table", "values": rows},
            {
                "name": "keys",
                "source": "table",
                "transform": [{"type": "aggregate", "groupby": ["key"]}],
            },
            {"name": "grid", "values": grid_rows},
            {"name": "ticks", "values": tick_rows},
            {"name": "legend", "values": legend_rows},
        ] + [
            {"name": rd["name"], "values": rd["rows"]} for rd in ref_datasets
        ],
        "scales": [
            {
                "name": "angular",
                "type": "point",
                "range": {"signal": "[-PI, PI]"},
                "padding": 0.5,
                "domain": {"data": "table", "field": "key"},
            },
            {
                "name": "radial",
                "type": "linear",
                "range": {"signal": "[0, radius]"},
                "zero": True,
                "nice": False,
                "domain": [0, 1],
            },
        ],
        "marks": [
            # Grid web (concentric polygons).
            {
                "type": "group",
                "from": {
                    "facet": {"data": "grid", "name": "grid_facet", "groupby": ["level"]},
                },
                "encode": {
                    "enter": {
                        "x": {"signal": "cx"},
                        "y": {"signal": "cy"},
                    },
                },
                "marks": [
                    {
                        "type": "line",
                        "from": {"data": "grid_facet"},
                        "encode": {
                            "enter": {
                                "interpolate": {"value": "linear-closed"},
                                "x": {"signal": "scale('radial', datum.value) * cos(scale('angular', datum.key))"},
                                "y": {"signal": "scale('radial', datum.value) * sin(scale('angular', datum.key))"},
                                "stroke": {"value": "#dcdcdc"},
                                "strokeWidth": {"value": 1},
                                "fill": {"value": "transparent"},
                            },
                        },
                    },
                ],
            },
            # Radial spokes.
            {
                "type": "rule",
                "from": {"data": "keys"},
                "encode": {
                    "enter": {
                        "x": {"signal": "cx"},
                        "y": {"signal": "cy"},
                        "x2": {"signal": "cx + radius * cos(scale('angular', datum.key))"},
                        "y2": {"signal": "cy + radius * sin(scale('angular', datum.key))"},
                        "stroke": {"value": "#dcdcdc"},
                        "strokeWidth": {"value": 1},
                    },
                },
            },
        ],
    }

    # Reference polygons (drawn UNDER the agent polygon so the agent stands out).
    for rd in ref_datasets:
        spec["marks"].append({
            "type": "line",
            "from": {"data": rd["name"]},
            "encode": {
                "enter": {
                    "interpolate": {"value": "linear-closed"},
                    "x": {"signal": f"cx + scale('radial', datum.value) * cos(scale('angular', datum.key))"},
                    "y": {"signal": f"cy + scale('radial', datum.value) * sin(scale('angular', datum.key))"},
                    "stroke": {"value": rd["color"]},
                    "strokeWidth": {"value": 2},
                    "strokeDash": {"value": rd["dash"]} if rd["dash"] else {"value": []},
                    "fill": {"value": "transparent"},
                },
            },
        })

    # Agent polygon (filled).
    spec["marks"].extend([
        {
            "type": "line",
            "from": {"data": "table"},
            "encode": {
                "enter": {
                    "interpolate": {"value": "linear-closed"},
                    "x": {"signal": "cx + scale('radial', datum.value) * cos(scale('angular', datum.key))"},
                    "y": {"signal": "cy + scale('radial', datum.value) * sin(scale('angular', datum.key))"},
                    "stroke": {"value": agent_stroke},
                    "strokeWidth": {"value": 2},
                    "fill": {"value": agent_stroke},
                    "fillOpacity": {"value": 0.18},
                },
            },
        },
        # Vertex dots.
        {
            "type": "symbol",
            "from": {"data": "table"},
            "encode": {
                "enter": {
                    "x": {"signal": "cx + scale('radial', datum.value) * cos(scale('angular', datum.key))"},
                    "y": {"signal": "cy + scale('radial', datum.value) * sin(scale('angular', datum.key))"},
                    "size": {"value": 60},
                    "fill": {"value": agent_stroke},
                    "stroke": {"value": "#ffffff"},
                    "strokeWidth": {"value": 1},
                },
            },
        },
        # Axis labels (dimension names around the perimeter). Pushed
        # further out (radius + 22) so the names sit clearly outside the
        # polygon and don't crowd the vertex dots.
        {
            "type": "text",
            "from": {"data": "keys"},
            "encode": {
                "enter": {
                    "x": {"signal": "cx + (radius + 22) * cos(scale('angular', datum.key))"},
                    "y": {"signal": "cy + (radius + 22) * sin(scale('angular', datum.key))"},
                    "text": {"field": "key"},
                    "align": [
                        {"test": "abs(cos(scale('angular', datum.key))) < 0.15", "value": "center"},
                        {"test": "cos(scale('angular', datum.key)) > 0", "value": "left"},
                        {"value": "right"},
                    ],
                    "baseline": [
                        {"test": "abs(sin(scale('angular', datum.key))) < 0.15", "value": "middle"},
                        {"test": "sin(scale('angular', datum.key)) > 0", "value": "top"},
                        {"value": "bottom"},
                    ],
                    "fill": {"value": "#333"},
                    "fontSize": {"value": 11},
                },
            },
        },
        # Radial tick labels (0.2, 0.4, …).
        {
            "type": "text",
            "from": {"data": "ticks"},
            "encode": {
                "enter": {
                    "x": {"signal": "cx + 4"},
                    "y": {"signal": "cy - scale('radial', datum.v)"},
                    "text": {"signal": "format(datum.v, '.1f')"},
                    "fontSize": {"value": 9},
                    "fill": {"value": "#888"},
                    "baseline": {"value": "middle"},
                },
            },
        },
        # ---- Side legend (right of the radar) -------------------------
        # Header: "↑ Higher is better"
        {
            "type": "text",
            "encode": {
                "enter": {
                    "x": {"signal": "legendX"},
                    "y": {"signal": "legendYTop - 6"},
                    "text": {"value": "↑ Higher is better (0–1 normalized)"},
                    "fill": {"value": "#555"},
                    "fontSize": {"value": 10},
                    "fontStyle": {"value": "italic"},
                    "align": {"value": "left"},
                    "baseline": {"value": "bottom"},
                },
            },
        },
        # Coloured dot per dimension.
        {
            "type": "symbol",
            "from": {"data": "legend"},
            "encode": {
                "enter": {
                    "x": {"signal": "legendX"},
                    "y": {"signal": "legendYTop + datum.idx * legendStep"},
                    "size": {"value": 60},
                    "fill": {"value": agent_stroke},
                    "stroke": {"value": agent_stroke},
                },
            },
        },
        # Dimension name. The per-row "↑" indicator is intentionally
        # omitted here because the header already states the directional
        # rule; including it on every row crowds the value column.
        {
            "type": "text",
            "from": {"data": "legend"},
            "encode": {
                "enter": {
                    "x": {"signal": "legendX + 14"},
                    "y": {"signal": "legendYTop + datum.idx * legendStep"},
                    "text": {"field": "key"},
                    "fill": {"value": "#1b1f24"},
                    "fontSize": {"value": 11},
                    "align": {"value": "left"},
                    "baseline": {"value": "middle"},
                },
            },
        },
        # Dimension value (right-aligned, in the dedicated value column).
        {
            "type": "text",
            "from": {"data": "legend"},
            "encode": {
                "enter": {
                    "x": {"signal": "width - 30"},
                    "y": {"signal": "legendYTop + datum.idx * legendStep"},
                    "text": {"signal": "format(datum.value, '.2f')"},
                    "fill": {"value": agent_stroke},
                    "fontSize": {"value": 11},
                    "fontWeight": {"value": "bold"},
                    "align": {"value": "right"},
                    "baseline": {"value": "middle"},
                },
            },
        },
    ])

    return spec


def _build_grouped_bar(block: dict[str, Any]) -> dict[str, Any]:
    """Grouped bar chart from categories + series + optional reference_lines."""
    categories = block.get("categories", [])
    series_list = block.get("series", [])
    y_axis = block.get("y_axis", "Value")
    ref_lines = block.get("reference_lines") or []

    if not categories or not series_list:
        return _build_placeholder(block)

    # Flatten to long-form for Vega-Lite
    flat_rows = []
    for s in series_list:
        if isinstance(s, dict):
            name = s.get("name", "?")
            values = s.get("values", [])
        elif hasattr(s, "name"):
            name = s.name
            values = getattr(s, "values", [])
        else:
            continue
        for cat, val in zip(categories, values):
            try:
                val = float(val)
            except (TypeError, ValueError):
                val = 0.0
            flat_rows.append({"category": str(cat), "series": name, "value": val})

    if not flat_rows:
        return _build_placeholder(block)

    layers = [
        {
            "mark": {"type": "bar", "cornerRadiusEnd": 3},
            "encoding": {
                "x": {"field": "category", "type": "nominal", "axis": {"title": None}},
                "y": {"field": "value", "type": "quantitative",
                      "axis": {"title": y_axis}},
                "xOffset": {"field": "series", "type": "nominal"},
                "color": {"field": "series", "type": "nominal",
                          "scale": {"scheme": "tableau10"}, "title": "Series"},
                "tooltip": [
                    {"field": "category", "type": "nominal"},
                    {"field": "series", "type": "nominal"},
                    {"field": "value", "type": "quantitative", "format": ".3f"},
                ],
            },
        }
    ]

    # Add reference lines
    for rl in ref_lines:
        if isinstance(rl, dict):
            rv, rl_label = rl.get("value", 0), rl.get("label", "")
        elif hasattr(rl, "value"):
            rv, rl_label = rl.value, getattr(rl, "label", "")
        else:
            continue
        layers.append({
            "mark": {"type": "rule", "color": "#e74c3c", "strokeDash": [6, 4], "strokeWidth": 1.5},
            "encoding": {"y": {"datum": rv}},
        })
        layers.append({
            "mark": {"type": "text", "align": "right", "dx": -4, "dy": -6,
                     "color": "#e74c3c", "fontSize": 10},
            "encoding": {"y": {"datum": rv}, "text": {"value": rl_label}},
        })

    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "width": 500,
        "height": 320,
        "background": "transparent",
        "data": {"values": flat_rows},
        "layer": layers,
    }
    return spec


def _build_stacked_bar(block: dict[str, Any]) -> dict[str, Any]:
    """Stacked bar chart from categories + series."""
    categories = block.get("categories", [])
    series_list = block.get("series", [])
    y_axis = block.get("y_axis", "Value")

    if not categories or not series_list:
        return _build_placeholder(block)

    flat_rows = []
    for s in series_list:
        if isinstance(s, dict):
            name = s.get("name", "?")
            values = s.get("values", [])
        elif hasattr(s, "name"):
            name = s.name
            values = getattr(s, "values", [])
        else:
            continue
        for cat, val in zip(categories, values):
            try:
                val = float(val)
            except (TypeError, ValueError):
                val = 0.0
            flat_rows.append({"category": str(cat), "series": name, "value": val})

    if not flat_rows:
        return _build_placeholder(block)

    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "width": 500,
        "height": 320,
        "background": "transparent",
        "data": {"values": flat_rows},
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "category", "type": "nominal", "axis": {"title": None}},
            "y": {"field": "value", "type": "quantitative",
                  "axis": {"title": y_axis}, "stack": True},
            "color": {"field": "series", "type": "nominal",
                      "scale": {"scheme": "category10"}, "title": "Series"},
            "tooltip": [
                {"field": "category", "type": "nominal"},
                {"field": "series", "type": "nominal"},
                {"field": "value", "type": "quantitative", "format": ",.0f"},
            ],
        },
    }
    return spec


def _build_heatmap(block: dict[str, Any]) -> dict[str, Any]:
    """Heatmap from x_labels, y_labels, values matrix."""
    x_labels = block.get("x_labels", [])
    y_labels = block.get("y_labels", [])
    values = block.get("values", [])
    display_values = block.get("display_values")

    if not x_labels or not y_labels or not values:
        return _build_placeholder(block)

    flat_rows = []
    for yi, y_label in enumerate(y_labels):
        if yi >= len(values):
            break
        row = values[yi]
        disp_row = display_values[yi] if display_values and yi < len(display_values) else None
        for xi, x_label in enumerate(x_labels):
            val = row[xi] if xi < len(row) else None
            disp = disp_row[xi] if disp_row and xi < len(disp_row) else val
            if val is None:
                val = 0.0
            try:
                val = float(val)
            except (TypeError, ValueError):
                val = 0.0
            flat_rows.append({
                "x": str(x_label),
                "y": str(y_label),
                "value": val,
                "display": str(disp) if disp is not None else "",
            })

    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "width": 520,
        "height": 300,
        "background": "transparent",
        "data": {"values": flat_rows},
        "layer": [
            {
                "mark": {"type": "rect"},
                "encoding": {
                    "y": {"field": "y", "type": "nominal", "axis": {"title": None}},
                    "x": {"field": "x", "type": "nominal",
                           "axis": {"title": None, "labelAngle": -25}},
                    "color": {
                        "field": "value", "type": "quantitative",
                        "scale": {"scheme": "blues", "domain": [0, 1]},
                        "title": "Score",
                    },
                    "tooltip": [
                        {"field": "y", "type": "nominal", "title": "Category"},
                        {"field": "x", "type": "nominal", "title": "Metric"},
                        {"field": "display", "type": "nominal", "title": "Value"},
                    ],
                },
            },
            {
                "mark": {"type": "text", "fontSize": 10},
                "encoding": {
                    "y": {"field": "y", "type": "nominal"},
                    "x": {"field": "x", "type": "nominal"},
                    "text": {"field": "display", "type": "nominal"},
                    "color": {
                        "condition": {"test": "datum.value > 0.6", "value": "white"},
                        "value": "black",
                    },
                },
            },
        ],
    }
    return spec


def _build_ci_bar(block: dict[str, Any]) -> dict[str, Any]:
    """CI bar chart: mean (point) + confidence interval (horizontal rule).

    Expects: points: [{label, value, ci_low, ci_high, group?}]
    Renders a per-label point with an error-bar rule from ci_low → ci_high.
    `group` (optional) splits points into colored series.
    """
    points = block.get("points", [])
    if not points:
        return _build_placeholder(block)

    y_label = block.get("y_label", "Value")
    has_groups = any(
        isinstance(p, dict) and p.get("group") for p in points
    )

    rows = []
    for p in points:
        if not isinstance(p, dict):
            continue
        label = str(p.get("label", "?"))
        try:
            value = float(p.get("value", 0))
        except (TypeError, ValueError):
            value = 0.0
        try:
            ci_low = float(p.get("ci_low", value))
        except (TypeError, ValueError):
            ci_low = value
        try:
            ci_high = float(p.get("ci_high", value))
        except (TypeError, ValueError):
            ci_high = value
        row = {
            "label": label,
            "value": value,
            "ci_low": ci_low,
            "ci_high": ci_high,
        }
        if has_groups:
            row["group"] = str(p.get("group") or "—")
        rows.append(row)

    if not rows:
        return _build_placeholder(block)

    rule_encoding = {
        "y": {"field": "ci_low", "type": "quantitative",
              "axis": {"title": y_label}},
        "y2": {"field": "ci_high"},
        "x": {"field": "label", "type": "nominal", "axis": {"title": None, "labelAngle": 0}},
    }
    point_encoding = {
        "y": {"field": "value", "type": "quantitative"},
        "x": {"field": "label", "type": "nominal"},
        "tooltip": [
            {"field": "label", "type": "nominal"},
            {"field": "value", "type": "quantitative", "format": ".3f", "title": "Mean"},
            {"field": "ci_low", "type": "quantitative", "format": ".3f", "title": "CI low"},
            {"field": "ci_high", "type": "quantitative", "format": ".3f", "title": "CI high"},
        ],
    }
    if has_groups:
        color = {
            "field": "group", "type": "nominal",
            "scale": {"scheme": "tableau10"}, "title": "Group",
        }
        rule_encoding["color"] = color
        rule_encoding["xOffset"] = {"field": "group", "type": "nominal"}
        point_encoding["color"] = color
        point_encoding["xOffset"] = {"field": "group", "type": "nominal"}

    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "width": 480,
        "height": 300,
        "background": "transparent",
        "data": {"values": rows},
        "layer": [
            {
                "mark": {"type": "rule", "strokeWidth": 2, "color": "#4c5aa0"},
                "encoding": rule_encoding,
            },
            {
                "mark": {"type": "point", "filled": True, "size": 80, "color": "#1a2744"},
                "encoding": point_encoding,
            },
        ],
        "resolve": {"scale": {"color": "independent"}},
    }
    return spec


def _build_placeholder(block: dict[str, Any]) -> dict[str, Any]:
    """Minimal placeholder spec for charts with no valid data."""
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "width": 400,
        "height": 200,
        "background": "#f5f5f5",
        "data": {"values": [{"x": 1, "y": 0}]},
        "mark": {"type": "text", "text": "No data", "color": "#aaa", "fontSize": 16},
        "encoding": {
            "x": {"field": "x", "type": "quantitative", "axis": None},
            "y": {"field": "y", "type": "quantitative", "axis": None},
        },
    }


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_BUILDERS: dict[str, Any] = {
    "radar": _build_radar,
    "grouped_bar": _build_grouped_bar,
    "stacked_bar": _build_stacked_bar,
    "heatmap": _build_heatmap,
    "ci_bar": _build_ci_bar,
}


def _render_chart(block: dict[str, Any]) -> ChartResult:
    chart_id = block.get("_chart_id", "unknown")
    chart_type = block.get("chart_type", "unknown")
    title = block.get("title", "")
    alt_text = title
    w = block.get("width_px", 500)
    h = block.get("height_px", 350)

    builder = _BUILDERS.get(chart_type, None)
    if builder is None:
        log.warning("Unknown chart type: %s", chart_type)
        return ChartResult(
            chart_id=chart_id, chart_type=chart_type, title=title,
            alt_text=alt_text, width_px=w, height_px=h,
            error=f"Unknown chart type: {chart_type}",
        )

    try:
        spec = builder(block)
        svg = _spec_to_svg(spec, w, h)
        return ChartResult(
            chart_id=chart_id, chart_type=chart_type, title=title,
            svg=svg, alt_text=alt_text, width_px=w, height_px=h,
        )
    except Exception as exc:
        log.error("Chart render error [%s]: %s", chart_id, exc)
        return ChartResult(
            chart_id=chart_id, chart_type=chart_type, title=title,
            alt_text=alt_text, width_px=w, height_px=h,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

def charts_node(state: GraphState) -> GraphState:
    charts_to_render: list[dict[str, Any]] = state.get("charts_to_render", [])
    verbose = state.get("verbose", False)

    results: dict[str, ChartResult] = {}
    for block in charts_to_render:
        cid = block.get("_chart_id", "unknown")
        if verbose:
            log.info("charts_node: rendering %s (%s)", cid, block.get("chart_type"))
        results[cid] = _render_chart(block)

    return {**state, "chart_results": results}
