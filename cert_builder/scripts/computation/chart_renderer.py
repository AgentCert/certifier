"""
Chart renderer -- renders chart data dicts to PNG images using Plotly.

Styling matches the HTML certification report template:
  - Primary (Median):   #5b44ba (purple)
  - Secondary (P95):    #109B97 (teal)
  - Tertiary:           #e07c3a (amber)
  - Quaternary:         #64748b (slate)
  - Title text:         #1c1438 (dark navy)
  - Axis/label text:    #6a727c (grey)
  - Category labels:    #1b1f24 (near-black)
  - Grid lines:         #e4e7eb (light grey)
  - Background:         #ffffff (white)
  - Font:               Segoe UI, sans-serif
  - Radar fill:         rgba(91,68,186,0.15)
  - Reference line:     #c0392b (red, dashed)
"""

import base64
from pathlib import Path

import plotly.graph_objects as go

# -- Theme constants (from HTML certificate) ----------------------------------

PRIMARY    = "#5b44ba"
SECONDARY  = "#109B97"
TERTIARY   = "#e07c3a"
QUATERNARY = "#64748b"
BAR_COLORS = [PRIMARY, SECONDARY, TERTIARY, QUATERNARY]

TITLE_COLOR    = "#1c1438"
AXIS_COLOR     = "#6a727c"
LABEL_COLOR    = "#1b1f24"
GRID_COLOR     = "#e4e7eb"
REF_LINE_COLOR = "#c0392b"
BG_COLOR       = "#ffffff"

FONT_FAMILY = "Segoe UI, sans-serif"

# Common layout settings
_BASE_LAYOUT = dict(
    font=dict(family=FONT_FAMILY, color=LABEL_COLOR),
    paper_bgcolor=BG_COLOR,
    plot_bgcolor=BG_COLOR,
    margin=dict(l=60, r=30, t=60, b=50),
)


DEFAULT_DIMS = dict(width=700, height=450)


def _save(fig, path, width=700, height=450):
    """Save a plotly figure to PNG."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.write_image(str(path), width=width, height=height, scale=2)


def _to_base64(image_path):
    """Read a PNG file and return its base64-encoded string."""
    return base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")


# -- Radar chart --------------------------------------------------------------

def _render_radar(chart, path=None):
    dims = chart["dimensions"]
    labels = [d["dimension"] for d in dims]
    values = [d["value"] for d in dims]

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=values + [values[0]],
        theta=labels + [labels[0]],
        fill="toself",
        fillcolor="rgba(91,68,186,0.15)",
        line=dict(color=PRIMARY, width=2.5),
        marker=dict(size=7, color=PRIMARY, line=dict(color="white", width=2)),
        text=[f"{v:.2f}" for v in values] + [f"{values[0]:.2f}"],
        textposition="top center",
        textfont=dict(color=PRIMARY, size=11, family=FONT_FAMILY),
        mode="lines+markers+text",
        name="Score",
    ))

    fig.update_layout(
        font=dict(family=FONT_FAMILY, color=LABEL_COLOR),
        paper_bgcolor=BG_COLOR,
        plot_bgcolor=BG_COLOR,
        title=dict(text=chart["title"], font=dict(size=16, color=TITLE_COLOR, family=FONT_FAMILY)),
        polar=dict(
            bgcolor=BG_COLOR,
            radialaxis=dict(
                visible=True, range=[0, 1],
                tickvals=[0.2, 0.4, 0.6, 0.8, 1.0],
                tickfont=dict(size=9, color=SECONDARY, family=FONT_FAMILY),
                gridcolor=GRID_COLOR, gridwidth=0.7,
                linecolor=GRID_COLOR,
            ),
            angularaxis=dict(
                tickfont=dict(size=11, color=AXIS_COLOR, family=FONT_FAMILY),
                gridcolor="#d8dbfa", gridwidth=0.6,
                linecolor=GRID_COLOR,
            ),
        ),
        showlegend=False,
        margin=dict(l=80, r=80, t=80, b=60),
    )
    if path:
        _save(fig, path, width=600, height=550)
    return fig


# -- Grouped bar chart --------------------------------------------------------

def _render_grouped_bar(chart, path=None):
    cat_labels = chart["categories"]
    series_list = chart["series"]

    fig = go.Figure()
    for i, s in enumerate(series_list):
        color = BAR_COLORS[i % len(BAR_COLORS)]
        text_color = color if i == 0 else AXIS_COLOR
        vals = s["values"]
        text_vals = []
        for v in vals:
            if v is None or v == 0:
                text_vals.append("")
            elif v >= 10:
                text_vals.append(f"{v:.0f}")
            else:
                text_vals.append(f"{v:.2f}")

        fig.add_trace(go.Bar(
            name=s["name"],
            x=cat_labels,
            y=vals,
            marker_color=color,
            text=text_vals,
            textposition="outside",
            textfont=dict(size=11, color=text_color, family=FONT_FAMILY),
        ))

    # Reference lines
    for ref in (chart.get("reference_lines") or []):
        fig.add_hline(
            y=ref["value"], line_dash="dash", line_color=REF_LINE_COLOR,
            line_width=1.5,
            annotation_text=ref["label"],
            annotation_position="top left",
            annotation_font=dict(size=10, color=REF_LINE_COLOR, family=FONT_FAMILY),
        )

    fig.update_layout(
        **_BASE_LAYOUT,
        title=dict(text=chart["title"], font=dict(size=16, color=TITLE_COLOR, family=FONT_FAMILY)),
        barmode="group",
        xaxis=dict(
            title=dict(text="Fault Category", font=dict(size=12, color=AXIS_COLOR)),
            tickfont=dict(size=12, color=LABEL_COLOR, family=FONT_FAMILY),
            gridcolor=GRID_COLOR, showgrid=False,
            linecolor=GRID_COLOR,
        ),
        yaxis=dict(
            title=dict(text=chart.get("y_axis", ""), font=dict(size=12, color=AXIS_COLOR)),
            tickfont=dict(size=10, color=AXIS_COLOR, family=FONT_FAMILY),
            gridcolor=GRID_COLOR, gridwidth=0.8,
            linecolor=GRID_COLOR, zeroline=False,
        ),
        legend=dict(
            font=dict(size=11, color=LABEL_COLOR, family=FONT_FAMILY),
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor=GRID_COLOR, borderwidth=1,
        ),
        bargap=0.25,
    )
    if path:
        _save(fig, path)
    return fig


# -- Stacked bar chart --------------------------------------------------------

def _render_stacked_bar(chart, path=None):
    cat_labels = chart["categories"]
    series_list = chart["series"]

    fig = go.Figure()
    for i, s in enumerate(series_list):
        color = BAR_COLORS[i % len(BAR_COLORS)]
        vals = s["values"]
        text_vals = [f"{v:.0f}" if v and v > 0 else "" for v in vals]

        fig.add_trace(go.Bar(
            name=s["name"],
            x=cat_labels,
            y=vals,
            marker_color=color,
            text=text_vals,
            textposition="inside",
            textfont=dict(size=11, color="white", family=FONT_FAMILY),
        ))

    fig.update_layout(
        **_BASE_LAYOUT,
        title=dict(text=chart["title"], font=dict(size=16, color=TITLE_COLOR, family=FONT_FAMILY)),
        barmode="stack",
        xaxis=dict(
            title=dict(text="Fault Category", font=dict(size=12, color=AXIS_COLOR)),
            tickfont=dict(size=12, color=LABEL_COLOR, family=FONT_FAMILY),
            gridcolor=GRID_COLOR, showgrid=False,
            linecolor=GRID_COLOR,
        ),
        yaxis=dict(
            title=dict(text=chart.get("y_axis", ""), font=dict(size=12, color=AXIS_COLOR)),
            tickfont=dict(size=10, color=AXIS_COLOR, family=FONT_FAMILY),
            gridcolor=GRID_COLOR, gridwidth=0.8,
            linecolor=GRID_COLOR, zeroline=False,
        ),
        legend=dict(
            font=dict(size=11, color=LABEL_COLOR, family=FONT_FAMILY),
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor=GRID_COLOR, borderwidth=1,
        ),
        bargap=0.35,
    )
    if path:
        _save(fig, path)
    return fig


# -- Heatmap ------------------------------------------------------------------

def _render_heatmap(chart, path=None):
    x_labels = chart["x_labels"]
    y_labels = chart["y_labels"]
    raw_values = chart["values"]           # normalized 0-1 for coloring
    disp_values = chart.get("display_values", raw_values)  # raw for text

    # Build display text and numeric data (None -> None stays, shows as "N/A")
    z = []
    annotations_text = []
    for i, row in enumerate(raw_values):
        z_row = []
        text_row = []
        for j, v in enumerate(row):
            disp = disp_values[i][j]
            if v is None:
                z_row.append(None)
                text_row.append("N/A")
            else:
                z_row.append(v)
                text_row.append(f"{disp:.2f}" if disp is not None else "N/A")
        z.append(z_row)
        annotations_text.append(text_row)

    # Custom purple-to-teal colorscale
    colorscale = [
        [0.0,  "#f3eff9"],
        [0.3,  "#c4b5e3"],
        [0.5,  "#5b44ba"],
        [0.7,  "#2e8b88"],
        [1.0,  "#109B97"],
    ]

    fig = go.Figure(data=go.Heatmap(
        z=z, x=x_labels, y=y_labels,
        colorscale=colorscale,
        zmin=0, zmax=1,
        text=annotations_text,
        texttemplate="%{text}",
        textfont=dict(size=14, family=FONT_FAMILY, color="white"),
        hovertemplate="Category: %{y}<br>Metric: %{x}<br>Value: %{text}<extra></extra>",
        colorbar=dict(
            tickfont=dict(size=10, color=AXIS_COLOR, family=FONT_FAMILY),
            outlinecolor=GRID_COLOR, outlinewidth=1,
        ),
        connectgaps=False,
    ))

    # Fix N/A cell text color to grey (override white for empty cells)
    annotations = []
    for i, row in enumerate(raw_values):
        for j, v in enumerate(row):
            if v is None:
                annotations.append(dict(
                    x=x_labels[j], y=y_labels[i],
                    text="N/A", showarrow=False,
                    font=dict(size=14, color=AXIS_COLOR, family=FONT_FAMILY),
                ))

    fig.update_layout(
        font=dict(family=FONT_FAMILY, color=LABEL_COLOR),
        paper_bgcolor=BG_COLOR,
        plot_bgcolor=BG_COLOR,
        title=dict(text=chart["title"], font=dict(size=16, color=TITLE_COLOR, family=FONT_FAMILY)),
        xaxis=dict(
            tickfont=dict(size=12, color=LABEL_COLOR, family=FONT_FAMILY),
            side="bottom",
        ),
        yaxis=dict(
            tickfont=dict(size=11, color=LABEL_COLOR, family=FONT_FAMILY),
            autorange="reversed",
        ),
        annotations=annotations,
        margin=dict(l=90, r=30, t=60, b=80),
    )
    if path:
        _save(fig, path, width=700, height=350)
    return fig


# -- Dispatch -----------------------------------------------------------------

RENDERERS = {
    "radar": _render_radar,
    "grouped_bar": _render_grouped_bar,
    "stacked_bar": _render_stacked_bar,
    "heatmap": _render_heatmap,
}


def create_figure(chart):
    """Create a Plotly figure for a chart dict (no file I/O).

    Use fig.show() to display inline in a notebook.
    """
    renderer = RENDERERS.get(chart["chart_type"])
    if renderer is None:
        raise ValueError(f"Unknown chart type: {chart['chart_type']}")
    return renderer(chart)


def render_chart(chart, output_path):
    """Render a single chart dict to a PNG file."""
    renderer = RENDERERS.get(chart["chart_type"])
    if renderer is None:
        raise ValueError(f"Unknown chart type: {chart['chart_type']}")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    renderer(chart, output_path)


def render_all(charts_dict, output_dir, encode_base64=False):
    """Render all charts to PNGs in output_dir.

    Args:
        charts_dict: {"chart_name": {chart data}, ...}
        output_dir:  directory to save PNGs
        encode_base64: if True, add "image_base64" key to each chart

    Returns:
        charts_dict with "image_path" (and optionally "image_base64") added.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for name, chart in charts_dict.items():
        img_path = output_dir / f"{name}.png"
        render_chart(chart, img_path)
        chart["image_path"] = str(img_path)
        if encode_base64:
            chart["image_base64"] = _to_base64(img_path)

    return charts_dict
