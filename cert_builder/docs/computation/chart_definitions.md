# Phase 2C: Chart Definitions & Data Logic

## Overview

Phase 2C builds 9 chart data structures from Phase 1 metrics and Phase 2A scorecard. Each chart defines its type, labels, series, and axis metadata — no rendering happens here.

## Dependency

```
phase1_parsed_context.json ──► charts 2-9
Phase 2A scorecard.dimensions ──► chart 1 (radar)
```

---

## Chart 1: scorecard_radar

**Type**: `radar`
**Source**: Phase 2A `scorecard.dimensions`

Passes through the 7 scorecard dimensions directly. No transformation.

| Field      | Value                          |
|------------|--------------------------------|
| dimensions | scorecard.dimensions as-is     |

---

## Chart 2: ttd_bar (Time-to-Detect)

**Type**: `grouped_bar`
**Source**: `categories[].numeric.time_to_detect`

| Series | Raw Field                   |
|--------|-----------------------------|
| Median | `time_to_detect.median`     |
| P95    | `time_to_detect.p95`        |

**Reference line**: 300s (Concern Threshold, from config)

---

## Chart 3: ttm_bar (Time-to-Mitigate)

**Type**: `grouped_bar`
**Source**: `categories[].numeric.time_to_mitigate`

| Series | Raw Field                    |
|--------|------------------------------|
| Median | `time_to_mitigate.median`    |
| P95    | `time_to_mitigate.p95`       |

**Reference line**: 600s (Concern Threshold, from config)

---

## Chart 4: rates_bar (Detection & Mitigation Rates)

**Type**: `grouped_bar`
**Source**: `categories[].derived`

| Series          | Raw Field                         |
|-----------------|-----------------------------------|
| Detection Rate  | `fault_detection_success_rate`    |
| Mitigation Rate | `fault_mitigation_success_rate`   |

**Reference line**: 0.5 (Minimum Acceptable, from config)

---

## Chart 5: accuracy_heatmap

**Type**: `heatmap`
**Source**: `categories[].numeric`
**Title**: "Accuracy & Quality Overview"

Layout: rows = categories (Application, Network, Resource), columns = metrics.

Two value arrays:
- `values` — normalized 0-1 for color scale
- `display_values` — raw values shown as text in cells

| Column (x_label)       | Display Value                                   | Color Value (normalized)                        |
|------------------------|------------------------------------------------|------------------------------------------------|
| Action Correctness     | `action_correctness.mean` (0-1, None if missing) | same (already 0-1)                             |
| Reasoning Score        | `reasoning_score.mean` (raw 0-10)               | `clamp(reasoning_score.mean / 10)`             |
| Response Quality       | `response_quality_score.mean` (raw 0-10)         | `clamp(response_quality_score.mean / 10)`      |
| Hallucination Control  | `clamp(1 - hallucination_score.mean / 10)`       | same (already 0-1)                             |

**Note**: Action Correctness is `None` for categories with missing data (empty `{}`).

---

## Chart 6: reasoning_bar

**Type**: `grouped_bar`
**Source**: `categories[].numeric`

| Series           | Raw Field                        |
|------------------|----------------------------------|
| Reasoning        | `reasoning_score.mean`           |
| Response Quality | `response_quality_score.mean`    |

Y-axis scale: 0-10 (raw scores, not normalized)

---

## Chart 7: hallucination_bar

**Type**: `grouped_bar`
**Source**: `categories[].numeric.hallucination_score`

| Series | Raw Field                    |
|--------|------------------------------|
| Mean   | `hallucination_score.mean`   |
| Max    | `hallucination_score.max`    |

Y-axis scale: 0-10 (raw scores)

---

## Chart 8: compliance_bar

**Type**: `grouped_bar`
**Source**: `categories[].derived`

| Series              | Raw Field                    |
|---------------------|------------------------------|
| RAI Compliance      | `rai_compliance_rate`        |
| Security Compliance | `security_compliance_rate`   |

Y-axis scale: 0-1 (rates)

---

## Chart 9: token_stacked

**Type**: `stacked_bar`
**Source**: `categories[].numeric`

| Series        | Raw Field            |
|---------------|----------------------|
| Input Tokens  | `input_tokens.sum`   |
| Output Tokens | `output_tokens.sum`  |

---

## Configuration

`chart_config.yaml` contains:
- `reference_lines`: threshold values and labels for TTD, TTM, and rates charts
- `heatmap_scale`: breakpoints `[0.0, 0.25, 0.5, 0.75, 1.0]` for heatmap color scale
- `score_scale`: divisor (10) for normalizing 0-10 scores to 0-1

## None Handling

All chart builders use `_safe_get()` with `default=0.0` for missing numeric fields. The one exception is `action_correctness` in the heatmap, which returns `None` for categories without data — this lets the renderer distinguish "no data" from "zero".
