"""
Sub-Phase 2C -- Chart data builder.

What this script does:
  1. Reads Phase 1 parsed context (categories with numeric + derived metrics).
  2. Takes Phase 2A scorecard dimensions (for the radar chart).
  3. Builds 9 chart data structures, each with chart_type, title, and data.
  4. Optionally renders charts to PNG images (render=True) and/or encodes
     them as base64 strings (encode_base64=True).

Charts produced:
  1. scorecard_radar   -- Radar chart of 7 scorecard dimensions (from 2A)
  2. ttd_bar           -- Grouped bar: TTD median + P95 per category
  3. ttm_bar           -- Grouped bar: TTM median + P95 per category
  4. rates_bar         -- Grouped bar: detection + mitigation rates
  5. accuracy_heatmap  -- Heatmap: accuracy/quality (categories x metrics, raw display)
  6. reasoning_bar     -- Grouped bar: reasoning + response quality (0-1)
  7. hallucination_bar -- Grouped bar: hallucination mean + max (0-10)
  8. compliance_bar    -- Grouped bar: RAI + security compliance rates
  9. token_stacked     -- Stacked bar: input + output token sums

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
        "title": "Scorecard Snapshot",
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


def _build_ttd_bar(categories):
    return {
        "chart_type": "grouped_bar",
        "title": "Time-to-Detect (SLA-Aware Scores)",
        "categories": _labels(categories),
        "series": [
            {"name": "Category Score", "values": [_safe_get(c, "numeric", "time_to_detect", "category", "category_score") for c in categories]},
            {"name": "SLA Compliance", "values": [_safe_get(c, "numeric", "time_to_detect", "category", "sla_compliance") for c in categories]},
        ],
        "y_axis": "Score (0-1)",
    }


def _build_ttm_bar(categories):
    return {
        "chart_type": "grouped_bar",
        "title": "Time-to-Mitigate (SLA-Aware Scores)",
        "categories": _labels(categories),
        "series": [
            {"name": "Category Score", "values": [_safe_get(c, "numeric", "time_to_mitigate", "category", "category_score") for c in categories]},
            {"name": "SLA Compliance", "values": [_safe_get(c, "numeric", "time_to_mitigate", "category", "sla_compliance") for c in categories]},
        ],
        "y_axis": "Score (0-1)",
    }


def _build_rates_bar(categories):
    ref = CONFIG["reference_lines"]["rates_minimum"]
    return {
        "chart_type": "grouped_bar",
        "title": "Detection & Mitigation Rates",
        "categories": _labels(categories),
        "series": [
            {"name": "Detection Rate",  "values": [_safe_get(c, "derived", "fault_detection_success_rate") for c in categories]},
            {"name": "Mitigation Rate", "values": [_safe_get(c, "derived", "fault_mitigation_success_rate") for c in categories]},
        ],
        "y_axis": "Rate (0-1)",
        "reference_lines": [ref],
    }


def _build_accuracy_heatmap(categories):
    scale = CONFIG["score_scale"]
    cat_labels = _labels(categories)
    metric_labels = ["Action Correctness", "Reasoning Score",
                     "Response Quality", "Hallucination Control"]

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

        # Response Quality (raw 0-1; normalized = already 0-1)
        resp_raw = _safe_get(n, "response_quality_score", "mean")
        resp_norm = _clamp(resp_raw / scale)

        # Hallucination Control (1 - mean/10; already 0-1)
        hal_mean = _safe_get(n, "hallucination_score", "mean")
        hal_ctrl = _clamp(1 - hal_mean / scale)

        values.append([ac_norm, reas_norm, resp_norm, hal_ctrl])
        display_values.append([ac_raw, reas_raw, resp_raw, hal_ctrl])

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
        "title": "Reasoning & Response Quality (out of 1)",
        "categories": _labels(categories),
        "series": [
            {"name": "Reasoning",        "values": [_safe_get(c, "numeric", "reasoning_score", "mean") for c in categories]},
            {"name": "Response Quality",  "values": [_safe_get(c, "numeric", "response_quality_score", "mean") for c in categories]},
        ],
        "y_axis": "Score (0-1)",
    }


def _build_hallucination_bar(categories):
    return {
        "chart_type": "grouped_bar",
        "title": "Hallucination Scores",
        "categories": _labels(categories),
        "series": [
            {"name": "Mean", "values": [_safe_get(c, "numeric", "hallucination_score", "mean") for c in categories]},
            {"name": "Max",  "values": [_safe_get(c, "numeric", "hallucination_score", "max") for c in categories]},
        ],
        "y_axis": "Score (0-1)",
    }


def _build_compliance_bar(categories):
    return {
        "chart_type": "grouped_bar",
        "title": "Compliance Rates",
        "categories": _labels(categories),
        "series": [
            {"name": "RAI Compliance",      "values": [_safe_get(c, "derived", "rai_compliance_rate") for c in categories]},
            {"name": "Security Compliance",  "values": [_safe_get(c, "derived", "security_compliance_rate") for c in categories]},
        ],
        "y_axis": "Rate (0-1)",
    }


def _build_token_stacked(categories, run_level_tokens=None):
    """Build run-wise token chart showing input and output tokens per run.
    
    Args:
        categories: list of category dicts (for compatibility, not used in run-wise chart)
        run_level_tokens: dict with "input_tokens" and "output_tokens" lists from Phase 2
    """
    if not run_level_tokens:
        run_level_tokens = {"input_tokens": [], "output_tokens": []}
    
    input_tokens = run_level_tokens.get("input_tokens", [])
    output_tokens = run_level_tokens.get("output_tokens", [])
    run_ids = run_level_tokens.get("run_ids", [])
    
    # If no token data available, return a placeholder chart with empty series
    # (This can happen when Phase 1 metrics don't include token tracking)
    if not input_tokens and not output_tokens:
        return {
            "chart_type": "grouped_bar",
            "title": "Token Usage per Run",
            "categories": ["Input Tokens", "Output Tokens"],
            "series": [
                {"name": "No data", "values": [0, 0]}
            ],
            "y_axis": "Tokens",
        }
    
    # Generate run labels if run_ids not available
    if not run_ids or len(run_ids) == 0:
        run_count = max(len(input_tokens), len(output_tokens))
        run_labels = [f"Run {i+1}" for i in range(run_count)]
    else:
        run_labels = [f"Run {i+1}" for i in range(len(run_ids))]
    
    return {
        "chart_type": "grouped_bar",
        "title": "Token Usage per Run",
        "categories": ["Input Tokens", "Output Tokens"],
        "series": [
            {"name": run_label, "values": [input_val, output_val]}
            for run_label, input_val, output_val in zip(run_labels, input_tokens, output_tokens)
        ],
        "y_axis": "Tokens",
    }



# -- Public API ---------------------------------------------------------------

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "temp" / "charts"


def _compute_mean_tokens(run_level_tokens):
    """Compute mean input and output tokens across all runs.
    
    Args:
        run_level_tokens: dict with "input_tokens" and "output_tokens" lists
        
    Returns:
        dict with "mean_input_tokens" and "mean_output_tokens" keys (floats or None)
    """
    if not run_level_tokens:
        return {"mean_input_tokens": None, "mean_output_tokens": None}
    
    input_tokens = run_level_tokens.get("input_tokens", [])
    output_tokens = run_level_tokens.get("output_tokens", [])
    
    mean_input = sum(input_tokens) / len(input_tokens) if input_tokens else None
    mean_output = sum(output_tokens) / len(output_tokens) if output_tokens else None
    
    return {
        "mean_input_tokens": mean_input,
        "mean_output_tokens": mean_output,
    }


def build_all_charts(categories, scorecard_dimensions, run_level_tokens=None,
                     render=False, encode_base64=False, output_dir=None):
    """Build all 9 chart data structures.

    Args:
        categories: list of category dicts from Phase 1.
        scorecard_dimensions: list of {"dimension": ..., "value": ...} from 2A.
        run_level_tokens: dict with "input_tokens" and "output_tokens" lists from Phase 2.
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
    charts_output = build_all_charts(ctx["categories"], scorecard_dimensions,
                                     run_level_tokens=run_level_tokens,
                                     render=render, encode_base64=encode_base64,
                                     output_dir=output_dir)
    
    # Compute overall mean tokens and add to token_stacked chart
    mean_tokens = _compute_mean_tokens(run_level_tokens)
    charts_output["charts"]["token_stacked"]["mean_tokens"] = mean_tokens
    
    return charts_output
