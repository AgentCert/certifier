# Phase 2B: Table Definitions & Calculation Logic

## Overview

Phase 2B builds 13 data tables from Phase 1 parsed metrics. Each table has `headers` (column names) and `rows` (list of lists). Tables 1-11 have one row per fault category. Tables 12-13 merge items across all categories.

## Data Flow

```
phase1_parsed_context.json
  └── categories[]
        ├── numeric    → tables 2-3, 6-8, 11
        ├── derived    → tables 4-5, 9-10
        ├── boolean    → table 5
        └── textual    → tables 9-10, 12-13

table_config.yaml
  └── judge_models  → table 1
  └── severity_order / priority_order → tables 12-13
```

---

## Table 1: judge_models

**Source**: `table_config.yaml` (static/hardcoded)

| Column   | Source    |
|----------|-----------|
| Judge    | config    |
| Model    | config    |
| Provider | config    |
| Role     | config    |

---

## Table 2: ttd_stats (Time-to-Detect)

**Source**: `categories[].numeric.time_to_detect`

| Column   | Raw Field                    | Formatting       |
|----------|------------------------------|-------------------|
| Category | `label`                      | as-is             |
| Runs     | `total_runs`                 | integer           |
| Mean     | `time_to_detect.mean`        | round 1 dec + "s" |
| Median   | `time_to_detect.median`      | round 1 dec + "s" |
| Std Dev  | `time_to_detect.std_dev`     | round 1 dec + "s" |
| P95      | `time_to_detect.p95`         | round 1 dec + "s" |
| Min      | `time_to_detect.min`         | round 1 dec + "s" |
| Max      | `time_to_detect.max`         | round 1 dec + "s" |

---

## Table 3: ttm_stats (Time-to-Mitigate)

Same structure as ttd_stats, using `time_to_mitigate` fields.

---

## Table 4: detection_rates

**Source**: `categories[].derived`

| Column          | Raw Field                         | Formatting       |
|-----------------|-----------------------------------|-------------------|
| Category        | `label`                           | as-is             |
| Runs            | `total_runs`                      | integer           |
| Detection Rate  | `fault_detection_success_rate`    | × 100, + "%"      |
| Mitigation Rate | `fault_mitigation_success_rate`   | × 100, + "%"      |
| False Neg       | `false_negative_rate`             | × 100, + "%"      |
| False Pos       | `false_positive_rate`             | × 100, + "%"      |

---

## Table 5: safety_summary

**Source**: `categories[].derived` + `categories[].boolean`

| Column                 | Raw Field                                    | Formatting   |
|------------------------|----------------------------------------------|--------------|
| Category               | `label`                                      | as-is        |
| RAI Rate               | `derived.rai_compliance_rate`                | × 100, + "%" |
| Security Rate          | `derived.security_compliance_rate`           | × 100, + "%" |
| PII Detected           | `boolean.pii_detection.any_detected`         | true/false   |
| Hallucination Detected | `boolean.hallucination_detection.any_detected`| true/false  |

---

## Table 6: action_correctness

**Source**: `categories[].numeric.action_correctness`

| Column   | Raw Field                      | Formatting   |
|----------|--------------------------------|--------------|
| Category | `label`                        | as-is        |
| Mean     | `action_correctness.mean`      | 2 decimals   |
| Median   | `action_correctness.median`    | 2 decimals   |
| Std Dev  | `action_correctness.std_dev`   | 2 decimals   |

**Note**: Categories with empty `action_correctness {}` show "N/A" for all numeric columns.

---

## Table 7: reasoning_quality

**Source**: `categories[].numeric.reasoning_score` + `response_quality_score`

| Column           | Raw Field                       | Formatting |
|------------------|---------------------------------|------------|
| Category         | `label`                         | as-is      |
| Reasoning Mean   | `reasoning_score.mean`          | 2 decimals |
| Reasoning Median | `reasoning_score.median`        | 2 decimals |
| Response Mean    | `response_quality_score.mean`   | 2 decimals |
| Response Median  | `response_quality_score.median` | 2 decimals |

---

## Table 8: hallucination

**Source**: `categories[].numeric.hallucination_score`

| Column   | Raw Field                   | Formatting |
|----------|-----------------------------|------------|
| Category | `label`                     | as-is      |
| Mean     | `hallucination_score.mean`  | 3 decimals |
| Median   | `hallucination_score.median`| 3 decimals |
| Max      | `hallucination_score.max`   | 3 decimals |

---

## Table 9: rai_compliance

**Source**: `categories[].derived` + `categories[].textual.rai_check_summary`

| Column          | Raw Field                                   | Formatting   |
|-----------------|---------------------------------------------|--------------|
| Category        | `label`                                     | as-is        |
| Compliance Rate | `derived.rai_compliance_rate`               | × 100, + "%" |
| Severity        | `textual.rai_check_summary.severity_label`  | as-is        |
| Confidence      | `textual.rai_check_summary.confidence`      | as-is        |
| Agreement       | `textual.rai_check_summary.inter_judge_agreement` | 2 decimals |

---

## Table 10: security_compliance

Same structure as rai_compliance, using `security_compliance_summary` fields.

---

## Table 11: token_usage

**Source**: `categories[].numeric.input_tokens` + `output_tokens`

| Column      | Raw Field              | Formatting |
|-------------|------------------------|------------|
| Category    | `label`                | as-is      |
| Input Mean  | `input_tokens.mean`    | 1 decimal  |
| Input Sum   | `input_tokens.sum`     | 0 decimals |
| Output Mean | `output_tokens.mean`   | 1 decimal  |
| Output Sum  | `output_tokens.sum`    | 0 decimals |

---

## Table 12: limitations

**Source**: `categories[].textual.known_limitations.ranked_items`

**Merge logic**:
1. Collect all `ranked_items` from every category
2. Tag each item with its source category label
3. Sort by severity: High → Medium → Low (order from `table_config.yaml`)
4. Number sequentially starting at 1

| Column     | Raw Field            | Formatting |
|------------|----------------------|------------|
| #          | sequential index     | integer    |
| Limitation | `item.limitation`    | as-is      |
| Category   | parent `cat.label`   | as-is      |
| Severity   | `item.severity`      | as-is      |
| Frequency  | `item.frequency`     | integer    |

---

## Table 13: recommendations

**Source**: `categories[].textual.recommendations.prioritized_items`

**Merge logic**:
1. Collect all `prioritized_items` from every category
2. Tag each item with its source category label
3. Sort by priority: Critical → High → Medium → Low (order from `table_config.yaml`)
4. Number sequentially starting at 1

| Column         | Raw Field               | Formatting |
|----------------|-------------------------|------------|
| #              | sequential index        | integer    |
| Priority       | `item.priority`         | as-is      |
| Recommendation | `item.recommendation`   | as-is      |
| Category       | parent `cat.label`      | as-is      |

---

## Configuration

`table_config.yaml` contains:
- `judge_models`: static rows for the LLM Council judges table
- `formatting`: suffix and decimal rules (for documentation; applied in code)
- `severity_order`: sort order for limitations
- `priority_order`: sort order for recommendations
