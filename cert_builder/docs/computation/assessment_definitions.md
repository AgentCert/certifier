# Phase 2D: Assessment Block Definitions

## Overview

Phase 2D takes the LLM Council's qualitative consensus outputs from Phase 1 and reshapes them into structured assessment blocks for the report. No text is modified — consensus summaries are passed through verbatim.

## Dependency

```
phase1_parsed_context.json -> categories[].textual  ──►  4 assessment blocks per category
```

---

## Input: 4 Textual Fields per Category

Each category has these fields under `textual`:

| Field | Contains `severity_label`? | Example Rating |
|-------|---------------------------|----------------|
| `agent_summary` | No | (null) |
| `overall_response_and_reasoning_quality` | Yes | "Strong" |
| `security_compliance_summary` | Yes | "Strong" |
| `rai_check_summary` | Yes | "Strong" |

Each field has: `consensus_summary` (string), `confidence` ("High"/"Medium"/"Low"), `inter_judge_agreement` (float 0-1).

---

## Output: AssessmentBlock Dict

```json
{
  "title": "Response & Reasoning Quality",
  "rating": "Strong",
  "confidence": "High",
  "agreement": 1.0,
  "body": "<verbatim consensus_summary text>"
}
```

## Field Mapping (per category, in order)

| # | Source Field | → `title` | → `rating` |
|---|---|---|---|
| 1 | `agent_summary` | "Agent Summary" | `null` (no severity_label) |
| 2 | `overall_response_and_reasoning_quality` | "Response & Reasoning Quality" | `.severity_label` |
| 3 | `security_compliance_summary` | "Security Compliance" | `.severity_label` |
| 4 | `rai_check_summary` | "RAI Compliance" | `.severity_label` |

For all blocks: `body` = `.consensus_summary`, `confidence` = `.confidence`, `agreement` = `.inter_judge_agreement`.

## Allowed Values

- **rating**: `Strong`, `Clean`, `Moderate`, `Minor`, `Significant`, or `null`
- **confidence**: `High`, `Medium`, `Low`
- **agreement**: float between 0.0 and 1.0

## None Handling

- Missing `textual` section → category gets an empty assessment list
- Missing individual field → that assessment block is skipped
- `agent_summary` always has `rating: null` (no severity_label in source data)
