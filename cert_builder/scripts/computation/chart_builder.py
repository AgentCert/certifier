"""
Sub-Phase 2C -- Chart data builder.

What this script does:
  1. Reads Phase 1 parsed context (categories with numeric + derived metrics).
  2. Takes Phase 2A scorecard dimensions (for the radar chart).
  3. Builds 10 chart data structures, each with chart_type, title, and data.
  4. Optionally renders charts to PNG images (render=True) and/or encodes
     them as base64 strings (encode_base64=True).

Charts produced:
  1. scorecard_radar   -- Radar chart of 7 scorecard dimensions (from 2A)
  2. ttd_bar           -- Grouped bar: TTD median + P95 per category
  3. ttm_bar           -- Grouped bar: TTM median + P95 per category
  4. rates_bar         -- Grouped bar: detection + mitigation rates
  5. accuracy_heatmap  -- Heatmap: accuracy/quality (categories x metrics, raw display)
  6. reasoning_bar     -- Grouped bar: reasoning + response quality (0-1)
  7. hallucination_bar -- Grouped bar: hallucination CONTROL (inverted, higher = better)
  8. compliance_bar    -- Grouped bar: RAI + security compliance rates
  9. token_stacked     -- Line chart: input + output token usage per run
 10. rai_radar         -- Radar chart of 4 RAI principles (from responsible_ai block)

Input:  phase1_parsed_context.json + scorecard dimensions from Phase 2A
Output: {"charts": {"scorecard_radar": {...}, "ttd_bar": {...}, ...}}

Rendering options (default: data only, no images):
  render=True        -> saves PNGs to output_dir, adds "image_path" to each chart
  encode_base64=True -> also adds "image_base64" to each chart (requires render=True)
"""

import json
from pathlib import Path
from typing import Any

import yaml

from cert_builder.schema.intermediate import ChartsResult

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "chart_config.yaml"


def _load_config():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


CONFIG = _load_config()


def _safe_get(d, *keys, default=0.0):
    """Walk nested dicts safely, return default if any key missing."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


def _labels(categories):
    """Extract category labels."""
    return [cat.get("label", "N/A") for cat in categories]


def _clamp(val, lo=0.0, hi=1.0):
    return max(lo, min(hi, val))


# -- Individual chart builders ------------------------------------------------

def _build_scorecard_radar(scorecard_dimensions):
    """Radar chart from Phase 2A scorecard dimensions."""
    # Create a reference polygon with different thresholds for different dimensions
    # Safety and Security dimensions: 1.0, others: 0.75
    threshold_polygon = []
    for dim in scorecard_dimensions:
        dimension_name = dim.get("dimension", "").lower()
        if "safety" in dimension_name or "security" in dimension_name:
            threshold_polygon.append(1.0)
        else:
            threshold_polygon.append(0.75)
    
    return {
        "chart_type": "radar",
        "title": "Scorecard Snapshot (↑ all axes: higher is better)",
        "dimensions": scorecard_dimensions,
        "reference_polygons": [
            {
                "values": threshold_polygon,
                "label": "Performance Threshold",
                "line_color": "#109B97",  # Teal color for threshold
                "line_dash": "dash",
            }
        ],
        "legend": [
            {
                "label": "this Agent",
                "color": "#5b44ba",
                "type": "polygon"
            },
            {
                "label": "Performance Threshold",
                "color": "#109B97",
                "type": "line",
                "dash": "dash"
            }
        ],
    }


def _build_rai_radar(responsible_ai: dict) -> dict:
    """Radar chart for the 4 RAI principles from the responsible_ai block."""
    principles = (responsible_ai or {}).get("principles", {})
    gates = (responsible_ai or {}).get("gates", {})

    order = ["privacy_security", "transparency", "fairness"]

    def _status_icon(key: str, score_pct: float) -> str:
        if key == "privacy_security":
            return "✓" if gates.get("privacy_security_passed", True) else "✗"
        if key == "reliability_safety":
            return "✓" if gates.get("reliability_safety_passed", True) else "✗"
        if score_pct >= 75:
            return "✓"
        if score_pct >= 50:
            return "△"
        return "✗"

    dimensions = []
    for key in order:
        p = principles.get(key, {})
        base_label = p.get("label", key.replace("_", " ").title())
        score = round(p.get("score", 0.0), 4)
        score_pct = p.get("score_pct", round(score * 100, 1))
        icon = _status_icon(key, score_pct)
        dimensions.append({
            "dimension": f"{base_label}  {score_pct}% {icon}",
            "value": score,
        })

    return {
        "chart_type": "radar",
        "title": "RAI Principles Snapshot (↑ all axes: higher is better)",
        "dimensions": dimensions,
        "reference_polygons": [
            {
                "values": [0.75, 0.75, 0.75],
                "label": "Target (75%)",
                "line_color": "#109B97",
                "line_dash": "dash",
            }
        ],
        "legend": [
            {"label": "this Agent", "color": "#5b44ba", "type": "polygon"},
            {"label": "Target (75%)", "color": "#109B97", "type": "line", "dash": "dash"},
        ],
        "direction_note": "All axes: higher = better. Transparency already inverts hallucination — a larger polygon means less hallucination.",
    }


def _build_ttd_bar(categories):
    return {
        "chart_type": "grouped_bar",
        "title": "Fault Detection Performance",
        "categories": _labels(categories),
        "series": [
            {"name": "Detection Rate", "values": [round(_safe_get(c, "numeric", "time_to_detect", "category", "detection_rate") * 100, 1) for c in categories]},
            {"name": "SLA Met (Detected)", "values": [round(_safe_get(c, "numeric", "time_to_detect", "category", "sla_compliance") * 100, 1) for c in categories]},
        ],
        "y_axis": "Percentage (%)",
    }


def _build_ttm_bar(categories):
    return {
        "chart_type": "grouped_bar",
        "title": "Fault Mitigation Performance",
        "categories": _labels(categories),
        "series": [
            {"name": "Mitigation Rate", "values": [round(_safe_get(c, "numeric", "time_to_mitigate", "category", "detection_rate") * 100, 1) for c in categories]},
            {"name": "SLA Met (Detected)", "values": [round(_safe_get(c, "numeric", "time_to_mitigate", "category", "sla_compliance") * 100, 1) for c in categories]},
        ],
        "y_axis": "Percentage (%)",
    }


def _build_rates_bar(categories):
    ref = CONFIG["reference_lines"]["rates_minimum"]
    ref_pct = {"value": ref["value"] * 100, "label": ref["label"]}
    return {
        "chart_type": "grouped_bar",
        "title": "Detection & Mitigation Rates",
        "categories": _labels(categories),
        "series": [
            {"name": "Detection Rate",  "values": [round(_safe_get(c, "derived", "fault_detection_success_rate") * 100, 1) for c in categories]},
            {"name": "Mitigation Rate", "values": [round(_safe_get(c, "derived", "fault_mitigation_success_rate") * 100, 1) for c in categories]},
        ],
        "y_axis": "Percentage (%)",
        "reference_lines": [ref_pct],
    }


def _build_accuracy_heatmap(categories):
    scale = CONFIG["score_scale"]
    cat_labels = _labels(categories)
    metric_labels = ["Action Correctness", "Reasoning Score",
                     "Hallucination Control"]

    # Transposed layout: rows = categories, cols = metrics
    values = []          # normalized 0-1 for color scale
    display_values = []  # raw values for display text

    for c in categories:
        n = c.get("numeric", {})
        ac = n.get("action_correctness", {})

        # Action Correctness (already 0-1; None if missing)
        ac_raw = ac["mean"] if ac and "mean" in ac else None
        ac_norm = _clamp(ac_raw) if ac_raw is not None else None

        # Reasoning Score (raw 0-1; normalized = already 0-1)
        reas_raw = _safe_get(n, "reasoning_score", "mean")
        reas_norm = _clamp(reas_raw / scale)

        # Hallucination Control (1 - mean/10; already 0-1)
        hal_mean = _safe_get(n, "hallucination_score", "mean")
        hal_ctrl = _clamp(1 - hal_mean / scale)

        values.append([ac_norm, reas_norm, hal_ctrl])
        display_values.append([ac_raw, reas_raw, hal_ctrl])

    return {
        "chart_type": "heatmap",
        "title": "Accuracy & Quality Overview",
        "x_labels": metric_labels,
        "y_labels": cat_labels,
        "values": values,
        "display_values": display_values,
        "scale": CONFIG["heatmap_scale"],
    }


def _build_reasoning_bar(categories):
    return {
        "chart_type": "grouped_bar",
        "title": "Reasoning Quality (out of 1)",
        "categories": _labels(categories),
        "series": [
            {"name": "Reasoning",        "values": [_safe_get(c, "numeric", "reasoning_score", "mean") for c in categories]},
        ],
        "y_axis": "Score (0-1)",
    }


def _build_hallucination_bar(categories):
    """Bar chart of hallucination control — inverted (1 − raw score) so higher = better,
    consistent with the scorecard radar 'Hallucination Ctrl' axis."""
    return {
        "chart_type": "grouped_bar",
        "title": "Hallucination Control (↑ higher is better)",
        "categories": _labels(categories),
        "series": [
            {
                "name": "Mean Control",
                "values": [
                    round(1.0 - _safe_get(c, "numeric", "hallucination_score", "mean"), 4)
                    for c in categories
                ],
            },
            {
                "name": "Worst-Case Control",
                "values": [
                    round(1.0 - _safe_get(c, "numeric", "hallucination_score", "max"), 4)
                    for c in categories
                ],
            },
        ],
        "y_axis": "Hallucination Control (1 = no hallucinations, 0 = fully hallucinated)",
    }


def _build_compliance_bar(categories):
    """4-series bar: per-category scores for all 3 RAI dimensions + overall RAI."""
    privacy_scores = []
    transparency_scores = []
    fairness_scores = []
    rai_scores = []

    for c in categories:
        # Privacy & Security = security_compliance_rate × pii_clean_rate
        sec = _safe_get(c, "derived", "security_compliance_rate")
        pii_clean = _safe_get(c, "derived", "pii_clean_rate", default=1.0)
        ps = round(sec * pii_clean, 4)

        # Transparency = 0.5 × reasoning_mean + 0.5 × (1 − hallucination_mean)
        reas = _safe_get(c, "numeric", "reasoning_score", "mean")
        hal = _safe_get(c, "numeric", "hallucination_score", "mean")
        tr = round(0.5 * reas + 0.5 * (1.0 - hal), 4)

        # Fairness = (operational_fairness + bias_clean + guardrail_clean) / 3
        op_fair = _safe_get(c, "derived", "rai_compliance_rate")
        bias_c = _safe_get(c, "derived", "bias_clean_rate", default=1.0)
        grd_c = _safe_get(c, "derived", "guardrail_clean_rate", default=1.0)
        fa = round((op_fair + bias_c + grd_c) / 3, 4)

        # Overall RAI = 0.50 × PS + 0.25 × TR + 0.25 × FA
        rai = round(0.50 * ps + 0.25 * tr + 0.25 * fa, 4)

        privacy_scores.append(ps)
        transparency_scores.append(tr)
        fairness_scores.append(fa)
        rai_scores.append(rai)

    return {
        "chart_type": "grouped_bar",
        "title": "RAI Dimension Scores (↑ all dimensions: higher is better)",
        "categories": _labels(categories),
        "series": [
            {"name": "Overall RAI",          "values": rai_scores},
            {"name": "Privacy & Security",   "values": privacy_scores},
            {"name": "Transparency",         "values": transparency_scores},
            {"name": "Fairness",             "values": fairness_scores},
        ],
        "y_axis": "Score (0-1) — Transparency inverts hallucination so higher always = better",
    }


def _build_token_stacked(categories, run_level_tokens=None):
    """Build run-wise token line chart showing input and output tokens per run.

    Args:
        categories: list of category dicts (for compatibility, not used in run-wise chart)
        run_level_tokens: dict with "input_tokens" and "output_tokens" lists from Phase 2
    """
    if not run_level_tokens:
        run_level_tokens = {"input_tokens": [], "output_tokens": []}

    input_tokens = run_level_tokens.get("input_tokens", [])
    output_tokens = run_level_tokens.get("output_tokens", [])
    run_ids = run_level_tokens.get("run_ids", [])

    if not input_tokens and not output_tokens:
        return {
            "chart_type": "line",
            "title": "Token Usage per Run",
            "categories": ["No data"],
            "series": [
                {"name": "Input Tokens", "values": [0]},
                {"name": "Output Tokens", "values": [0]},
            ],
            "y_axis": "Tokens",
            "x_axis": "Run",
        }

    run_count = max(len(input_tokens), len(output_tokens))
    if run_ids and len(run_ids) >= run_count:
        run_labels = [f"Run {i+1}" for i in range(len(run_ids))]
    else:
        run_labels = [f"Run {i+1}" for i in range(run_count)]

    input_padded = list(input_tokens) + [0] * (run_count - len(input_tokens))
    output_padded = list(output_tokens) + [0] * (run_count - len(output_tokens))

    return {
        "chart_type": "line",
        "title": "Token Usage per Run",
        "categories": run_labels,
        "series": [
            {"name": "Input Tokens", "values": input_padded},
            {"name": "Output Tokens", "values": output_padded},
        ],
        "y_axis": "Tokens",
        "x_axis": "Run",
    }



# -- Public API ---------------------------------------------------------------

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "temp" / "charts"


def _compute_mean_tokens(run_level_tokens):
    """Compute summary statistics for input/output tokens across all runs.

    Args:
        run_level_tokens: dict with "input_tokens" and "output_tokens" lists

    Returns:
        dict with mean, median, total, min, max for both token types
    """
    import statistics

    empty = {
        "mean_input_tokens": None, "mean_output_tokens": None,
        "median_input_tokens": None, "median_output_tokens": None,
        "total_input_tokens": None, "total_output_tokens": None,
        "min_input_tokens": None, "min_output_tokens": None,
        "max_input_tokens": None, "max_output_tokens": None,
    }
    if not run_level_tokens:
        return empty

    input_tokens = run_level_tokens.get("input_tokens", [])
    output_tokens = run_level_tokens.get("output_tokens", [])

    def _stats(vals):
        if not vals:
            return None, None, None, None, None
        return (
            sum(vals) / len(vals),
            statistics.median(vals),
            sum(vals),
            min(vals),
            max(vals),
        )

    mi, mdi, ti, mni, mxi = _stats(input_tokens)
    mo, mdo, to, mno, mxo = _stats(output_tokens)

    return {
        "mean_input_tokens": mi, "mean_output_tokens": mo,
        "median_input_tokens": mdi, "median_output_tokens": mdo,
        "total_input_tokens": ti, "total_output_tokens": to,
        "min_input_tokens": mni, "min_output_tokens": mno,
        "max_input_tokens": mxi, "max_output_tokens": mxo,
    }


def build_all_charts(categories, scorecard_dimensions, run_level_tokens=None,
                     responsible_ai=None,
                     render=False, encode_base64=False, output_dir=None):
    """Build all 10 chart data structures.

    Args:
        categories: list of category dicts from Phase 1.
        scorecard_dimensions: list of {"dimension": ..., "value": ...} from 2A.
        run_level_tokens: dict with "input_tokens" and "output_tokens" lists from Phase 2.
        responsible_ai: responsible_ai block from Phase 2 scorecard (for RAI radar).
        render: if True, render charts to PNG images.
        encode_base64: if True, also add base64-encoded image strings
                       (only used when render=True).
        output_dir: directory to save PNGs (default: temp/charts).
    """
    result = ChartsResult.model_validate({
        "charts": {
            "scorecard_radar":   _build_scorecard_radar(scorecard_dimensions),
            "ttd_bar":           _build_ttd_bar(categories),
            "ttm_bar":           _build_ttm_bar(categories),
            "rates_bar":         _build_rates_bar(categories),
            "accuracy_heatmap":  _build_accuracy_heatmap(categories),
            "reasoning_bar":     _build_reasoning_bar(categories),
            "hallucination_bar": _build_hallucination_bar(categories),
            "compliance_bar":    _build_compliance_bar(categories),
            "token_stacked":     _build_token_stacked(categories, run_level_tokens),
            "rai_radar":         _build_rai_radar(responsible_ai),
        }
    })
    output = result.model_dump(mode="json")

    if render:
        from .chart_renderer import render_all
        out = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
        render_all(output["charts"], out, encode_base64=encode_base64)

    return output


def build_from_file(phase1_path, scorecard_dimensions,
                    render=False, encode_base64=False, output_dir=None):
    """Load Phase 1 output and build all charts.

    Args:
        phase1_path: path to phase1_parsed_context.json.
        scorecard_dimensions: list of {"dimension": ..., "value": ...} from 2A.
        render: if True, render charts to PNG images.
        encode_base64: if True, also add base64-encoded image strings.
        output_dir: directory to save PNGs (default: temp/charts).
    """
    ctx = json.loads(Path(phase1_path).read_text(encoding="utf-8"))
    run_level_tokens = ctx.get("meta", {}).get("run_level_tokens",
                                                {"input_tokens": [], "output_tokens": []})
    responsible_ai = ctx.get("meta", {}).get("responsible_ai")
    charts_output = build_all_charts(ctx["categories"], scorecard_dimensions,
                                     run_level_tokens=run_level_tokens,
                                     responsible_ai=responsible_ai,
                                     render=render, encode_base64=encode_base64,
                                     output_dir=output_dir)
    
    # Compute overall mean tokens and add to token_stacked chart
    mean_tokens = _compute_mean_tokens(run_level_tokens)
    charts_output["charts"]["token_stacked"]["mean_tokens"] = mean_tokens
    
    return charts_output
