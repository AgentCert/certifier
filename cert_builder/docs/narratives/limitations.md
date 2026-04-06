# Phase 3E: Limitation Enrichment & Labeling — Requirements

## Overview

Phase 3E takes the existing 10 Phase 2 limitations, **labels** each one with a classification tag, **enriches** descriptions with specific numbers, and **analyzes all Phase 1/2 tables** to identify additional limitations the rules-based Phase 2 approach may have missed.

**What changed from Phase 2:** Phase 2 generates limitations deterministically using per-category threshold rules. It cannot spot cross-category patterns, implicit data quality issues, or nuanced gaps that only emerge when reading multiple tables together. The LLM adds these.

**LLM Call**: 5 of 6 (JSON output — array of enriched limitation items)
**Dependencies**: None — this call is independent. However, its output is consumed by Phase 3F (Call 6).

---

## Target in Report

| Target | Path |
|--------|------|
| `certification_report.json` | `sections[10]` (limitations) -> `content[]` (limitation blocks with severity, category, frequency, label, description) |
| HTML | Section 11 -> styled limitation blocks with `[Severity]`, `(Category)`, frequency, optional label badge |

---

## Input Context Assembly

```
LIMITATIONS ENRICHMENT CONTEXT

=== EXISTING LIMITATIONS (Phase 2, 10 items) ===

#  Severity  Category     Limitation
-- --------  -----------  ----------
1  High      Application  Low fault detection success rate
                          (20%), 80% false negatives
2  High      Network      0% fault detection, 100% false
                          negatives across all 5 runs
3  High      Resource     Low fault detection success rate
                          (20%), 80% false negatives
4  Medium    Application  High variability in TTD/TTM
                          (205s/260s std dev)
5  Medium    Network      Slow, variable TTD (mean 536.6s,
                          std dev 404.45s)
6  Medium    Resource     Hallucinations in 20% of runs
                          (max score 0.07)
7  Medium    Resource     Slow TTD (mean ~1364.5s, std dev
                          106s)
8  Low       Application  Zero output tokens recorded across
                          all runs
9  Low       Network      Limited diagnostic scope in run 5
                          (DNS focus only)
10 Low       Resource     Inconsistent token usage (input
                          mean 370.2, median 0; output 0)

=== SUPPORTING TABLES FOR ANALYSIS ===

Detection & Rates Table:
  {phase2.tables.detection_rates - full table}

TTD Timing Table:
  {phase2.tables.ttd - full table with mean, median, std, P95}

TTM Timing Table:
  {phase2.tables.ttm - full table}

Safety Summary Table:
  {phase2.tables.safety_summary - RAI, Security, PII, Halluc}

Token Usage Table:
  {phase2.tables.token_usage - input/output means, sums}

Action Correctness Table:
  {phase2.tables.action_correctness - per-category scores}

Scorecard Dimensions:
  {phase2.scorecard.dimensions - 7 normalized values}

Per-category Derived Rates:
  {phase1.categories[].derived - all computed rates}

Per-category Boolean Flags:
  {phase1.categories[].boolean - PII, hallucination flags}
```

**Source fields:**
- `phase2.tables.limitations` -> existing 10 items
- `phase2.tables.detection_rates` -> detection/mitigation/false-neg/false-pos per category
- `phase2.tables.ttd`, `phase2.tables.ttm` -> timing statistics
- `phase2.tables.safety_summary` -> RAI, Security, PII, Hallucination per category
- `phase2.tables.token_usage` -> input/output token stats
- `phase2.tables.action_correctness` -> correctness scores (with N/A gaps)
- `phase2.scorecard.dimensions[]` -> 7 normalized scorecard values
- `phase1.categories[].derived.*` -> rates per category
- `phase1.categories[].boolean.*` -> PII/hallucination flags

---

## Prompt Template

```
You are enriching and labeling limitations for an AI agent certification report.

EXISTING LIMITATIONS (10 items from Phase 2):
{existing_limitations_table}

SUPPORTING DATA (all Phase 1/2 tables):
{supporting_tables_block}

TASK:
1. LABEL each existing limitation with a classification:
   - "Data Quality" - instrumentation/recording issues (missing tokens, zero values)
   - "Detection Gap" - fault detection failures or weaknesses
   - "Latency" - slow detection or mitigation timing
   - "Coverage Gap" - missing instrumentation or N/A metrics
   - "Behavioral" - hallucination, narrow diagnostic scope, other agent behavior issues
   - null - if no label fits cleanly

2. ENRICH existing limitation descriptions:
   - Rewrite each description to be more detailed and contextual
   - Include specific numbers from the supporting tables
   - Add frequency in "X/Y runs (Z%)" format

3. ANALYZE the supporting tables to DISCOVER additional limitations:
   - Look for patterns NOT already covered by existing items
   - Examples of what to look for:
     * Action correctness N/A for 2 of 3 categories - is this flagged?
     * Cross-category detection latency patterns
     * Scorecard dimensions below threshold
     * Any metric anomalies visible in the tables
   - Each new item needs: severity, category, label, frequency, description

RULES:
- Keep ALL 10 existing limitations - do not remove any
- You MAY add 0-3 new limitations if the tables reveal gaps not already covered
- Severity must be "High", "Medium", or "Low"
- Sort final list by severity: High first, then Medium, then Low
- Number sequentially starting at 1
- Each description must reference specific numbers from the data
- Use ONLY data from the context - do not invent values

FORMAT:
Return a JSON array:
[
  {
    "index": 1,
    "severity": "High",
    "category": "Application",
    "label": "Detection Gap",
    "frequency": "4/5 runs (80%)",
    "limitation": "<enriched description with specific numbers>"
  },
  ...
]
```

---

## Expected Output (reference — enriched from certification_report.json)

```json
[
  {
    "index": 1,
    "severity": "High",
    "category": "Application",
    "label": "Detection Gap",
    "frequency": "4/5 runs (80%)",
    "limitation": "Low fault detection success rate indicated by only 20% detection (1 out of 5 runs), implying the agent frequently failed to detect container-kill faults leading to a high false negative rate of 80%."
  },
  {
    "index": 2,
    "severity": "High",
    "category": "Network",
    "label": "Detection Gap",
    "frequency": "5/5 runs (100%)",
    "limitation": "Agent completely failed to detect any faults, as indicated by a fault_detection_success_rate of 0.0 and false_negative_rate of 1.0 across all 5 runs."
  },
  {
    "index": 3,
    "severity": "High",
    "category": "Resource",
    "label": "Detection Gap",
    "frequency": "4/5 runs (80%)",
    "limitation": "Low fault detection success rate of 20%, indicating that the agent failed to detect 80% of disk-fill faults across runs, significantly affecting overall diagnostic performance."
  },
  {
    "index": 4,
    "severity": "Medium",
    "category": "Application",
    "label": "Latency",
    "frequency": "5/5 runs (100%)",
    "limitation": "High variability and generally long time to detect and mitigate faults, as shown by large standard deviations (205s detection std dev, 260s mitigation std dev) and wide range of detection times (min 60.77s, max 584.56s)."
  },
  {
    "index": 5,
    "severity": "Medium",
    "category": "Network",
    "label": "Latency",
    "frequency": "5/5 runs (100%)",
    "limitation": "The agent's time to detect faults was highly variable and generally slow, with a mean of 536.6 seconds and a high standard deviation of 404.45 seconds."
  },
  {
    "index": 6,
    "severity": "Medium",
    "category": "Resource",
    "label": "Behavioral",
    "frequency": "1/5 runs (20%)",
    "limitation": "Presence of hallucinations detected in 20% of runs (hallucination_detection rate 0.2) with a maximum hallucination score of 0.07, showing occasional generation of inaccurate or fabricated information."
  },
  {
    "index": 7,
    "severity": "Medium",
    "category": "Resource",
    "label": "Latency",
    "frequency": "5/5 runs (100%)",
    "limitation": "Relatively high and variable time to detect faults, ranging from 1241.5 to 1497.56 seconds (mean ~1364.5s) with considerable standard deviation (106s), leading to slower incident response."
  },
  {
    "index": 8,
    "severity": "Low",
    "category": "Application",
    "label": "Data Quality",
    "frequency": "5/5 runs (100%)",
    "limitation": "Zero output tokens recorded across all 5 runs despite the agent producing diagnostic narratives and remediation actions, indicating an instrumentation gap in output token capture."
  },
  {
    "index": 9,
    "severity": "Low",
    "category": "Network",
    "label": "Behavioral",
    "frequency": "1/5 runs (20%)",
    "limitation": "In run 5, the agent's diagnostic scope narrowed to DNS-related hypotheses only, failing to consider broader network fault causes despite ambiguous symptom signals."
  },
  {
    "index": 10,
    "severity": "Low",
    "category": "Resource",
    "label": "Data Quality",
    "frequency": "5/5 runs (100%)",
    "limitation": "Inconsistent token usage data with input token mean of 370.2 but median of 0, and zero output tokens across all runs, suggesting intermittent instrumentation failures."
  }
]
```

---

## Pydantic Schema

```python
from pydantic import BaseModel, Field
from typing import Literal


class EnrichedLimitation(BaseModel):
    """LLM output for a labeled and enriched limitation."""
    index:      int = Field(..., ge=1)
    severity:   Literal["High", "Medium", "Low"]
    category:   str = Field(..., min_length=1)
    label:      str | None = None      # "Data Quality", "Detection Gap", "Latency", "Coverage Gap", "Behavioral"
    frequency:  str = Field(..., min_length=1)   # "X/Y runs (Z%)" or "N/A"
    limitation: str = Field(..., min_length=1)


class LimitationsEnriched(BaseModel):
    """Envelope model for Call 5 output."""
    items:       list[EnrichedLimitation] = Field(..., min_length=10, max_length=13)
    source:      Literal["llm", "fallback"]
    model:       str | None = None
    tokens_used: int = Field(default=0, ge=0)
```

### Label Taxonomy

| Label | Applies To |
|-------|-----------|
| `"Data Quality"` | Instrumentation/recording issues (missing tokens, zero values) |
| `"Detection Gap"` | Fault detection failures or weaknesses |
| `"Latency"` | Slow detection or mitigation timing |
| `"Coverage Gap"` | Missing instrumentation or N/A metrics |
| `"Behavioral"` | Hallucination, narrow diagnostic scope, other agent behavior issues |
| `null` | If no label fits cleanly |

---

## Downstream Dependency: Call 5 -> Call 6

Phase 3E output feeds into Phase 3F (Call 6) as input context:

```
Phase 3E output (LimitationsEnriched)
    |
    v
Phase 3F input context (enriched_limitations field)
    - Allows Call 6 to reference limitation L-numbers
    - Ensures every High-severity limitation gets a recommendation
```

---

## Two-Level Validation

```
LLM Response (JSON string)
    |
    v
Level 1: json.loads() -> LimitationsEnriched.model_validate({"items": data, ...})
         Catches: missing fields, wrong types, empty strings,
           item count out of 10-13 range, invalid severity
    |
    v
Level 2: Verify label is in allowed set or null
         Verify frequency format matches "X/Y runs (Z%)" or "N/A"
         Verify severity sort order (High -> Medium -> Low)
         Verify all 10 original items preserved (index 1-10)
    |
    v
If both pass -> store in Phase3Output.limitations_enriched
If either fails -> retry (up to 3x) -> fallback
```

---

## Validation Rules

| Rule | Check |
|------|-------|
| Item count | 10-13 items |
| Original preserved | All 10 original items are preserved (not removed) |
| Severity validity | Each `severity` must be "High", "Medium", or "Low" |
| Severity sort | Items sorted: High first, then Medium, then Low |
| Sequential index | `index` values are 1..N sequential |
| Label validity | Each `label` must be "Data Quality", "Detection Gap", "Latency", "Coverage Gap", "Behavioral", or null |
| Frequency format | Each `frequency` matches `"X/Y runs (Z%)"` or `"N/A"` |
| Label consistency | Token-related items = "Data Quality", detection items = "Detection Gap", timing items = "Latency" |
| Specific numbers | Descriptions reference specific numbers from the data |
| New items justified | Any items with index > 10 represent genuine gaps not already covered |
| No invented data | Use ONLY data from the context |

---

## Fallback (if LLM fails)

Keep all 10 original items with deterministic labeling:
- Items mentioning "detection" or "false negative" -> label = "Detection Gap"
- Items mentioning "TTD", "TTM", "latency", "slow" -> label = "Latency"
- Items mentioning "token", "instrumentation", "zero" -> label = "Data Quality"
- Items mentioning "hallucination" -> label = "Behavioral"
- Items mentioning "scope", "diagnostic" -> label = "Behavioral"
- Frequency = deterministic from Phase 1 data

No new items added in fallback mode.

---

## Module Layout

```
engine/phase3/phase3e/
├── __init__.py                        # re-exports build_limitations()
├── limitation_builder.py              # prompt assembly, LLM call, validation, fallback
└── docs/
    └── limitation_requirements.md      # This document
```

### Entry Point

```python
def build_limitations(phase1: dict, phase2: dict, llm_client) -> dict:
    """
    Returns:
        {
            "limitations_enriched": {
                "items": [...],
                "source": "llm" | "fallback",
                "model": "gpt-4o",
                "tokens_used": 890
            }
        }
    """
```

---

## Notebook Validation (Cell 7)

```
Cell 7:  Run LLM Call 5 (Limitation Enrichment & Labeling)
         - Display enriched items with labels vs original Phase 2 items
         - Check: all 10 original items preserved?
         - Check: each item has a label (or null)?
         - Check: labels match item content? (token items = "Data Quality", etc.)
         - Check: any new items (index > 10) represent genuine gaps?
         - Check: descriptions reference specific numbers?
         - Check: frequency in "X/Y runs (Z%)" format?
         - Check: sorted by severity?
```
