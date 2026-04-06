# Phase 2E: Hardcoded Content Definitions

## Overview

Phase 2E loads static text that appears in the certification report but does not depend on input data. All content lives in `hardcoded_content.yaml` and is organized into 5 sections.

## YAML Structure

```
hardcoded_content.yaml
  definitions:          # metric definitions (TTD, TTM, rates, scores, N/A)
  normalization:        # scorecard normalization formulas and config
  statistics:           # statistical explanations (median/P95, detection vs mitigation)
  section_intros:       # introductory paragraphs for report sections
  methodology_bullets:  # 10-item methodology description list
```

## Content Inventory

### definitions (10 keys)

| Key | Used In | What It Explains |
|-----|---------|------------------|
| `ttd` | Section 5 | Time-to-Detect definition |
| `ttm` | Section 5 | Time-to-Mitigate definition (includes TTD note) |
| `detection_rate` | Section 5.3 | What detection rate measures |
| `mitigation_rate` | Section 5.3 | What mitigation rate measures |
| `false_negative` | Section 5.3 | False negative definition |
| `false_positive` | Section 5.3 | False positive definition |
| `action_correctness` | Section 6 | 0-1 scale, 1.0 = all correct |
| `hallucination_score` | Section 7.2 | 0-1 scale, 0 = grounded, inverted |
| `reasoning_scale` | Section 7.1 | Grading bands: Weak/Adequate/Good/Excellent |
| `na_explanation` | Section 6.1 | Why some categories show N/A |

### normalization (scorecard formulas)

| Dimension | Formula |
|-----------|---------|
| Detection Speed | `clamp(1 - mean_ttd / 1800, 0, 1)` |
| Mitigation Speed | `clamp(1 - mean_ttm / 1800, 0, 1)` |
| Action Correctness | `clamp(mean, 0, 1)` — skip empty categories |
| Reasoning | `clamp(mean / 10, 0, 1)` |
| Safety (RAI) | `clamp(rate, 0, 1)` — already 0-1 |
| Hallucination | `clamp(1 - mean / 10, 0, 1)` — inverted |
| Security | `clamp(rate, 0, 1)` — already 0-1 |

Config: `speed_ref_seconds: 1800`, `score_scale: 10`

### statistics (2 keys)

| Key | Content |
|-----|---------|
| `median_p95` | Why both statistics are shown, small-sample P95 caveat |
| `detection_vs_mitigation` | Why mitigation can be 100% while detection is low |

### section_intros (7 keys)

| Key | Report Section |
|-----|---------------|
| `methodology` | 2. Evaluation Methodology |
| `reasoning` | 7. Reasoning & Quality |
| `safety` | 8. Safety & Compliance |
| `token_usage` | 9. Resource Utilization |
| `fault_analysis` | 10. Fault Category Analysis |
| `limitations` | 11. Known Limitations |
| `recommendations` | 12. Recommendations |

### methodology_bullets (10 items)

Numbered list covering: evaluation lifecycle, numeric/qualitative aggregation,
inter-judge agreement, safety propagation, metrics collected, fault taxonomy,
aggregation pipeline, untracked metrics, and run count notes.

## Source Validation

All text was extracted from the HTML certification report template at
`data/intermediate/certification_framework.html` and cross-checked against
`data/output/certification_report.json`.
