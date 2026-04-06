# Phase 3F: Recommendation Enrichment & Consolidation — Requirements

## Overview

Phase 3F takes the existing 10 Phase 2 recommendations, **merges** cross-category duplicates into consolidated items, **labels** each with a classification tag, **enriches** descriptions with specific numbers, and **analyzes tables + Call 5 output** to discover additional recommendations addressing uncovered limitations.

**LLM Call**: 6 of 6 (JSON output — array of enriched recommendation items)
**Dependencies**: Depends on **Phase 3E output** (enriched limitations from Call 5) — Call 5 must complete before Call 6 starts.

---

## Target in Report

| Target | Path |
|--------|------|
| `certification_report.json` | `sections[11]` (recommendations) → `content[]` (recommendation blocks with priority, category, label, description) |
| HTML | Section 12 → styled recommendation blocks with `[Priority]`, `(Category)`, label badge |

---

## Input Context Assembly

```
┌───────────────────────────────────────────────────────────────────────┐
│  RECOMMENDATION ENRICHMENT CONTEXT                                     │
├───────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  === EXISTING RECOMMENDATIONS (Phase 2, 10 items) ===                 │
│                                                                       │
│  APPLICATION (3 items):                                               │
│  R1 [Critical] Enhance fault detection for container-kill             │
│  R2 [High]     Reduce TTD/TTM through optimized telemetry             │
│  R3 [Medium]   Review output generation for traceability              │
│                                                                       │
│  NETWORK (3 items):                                                   │
│  R4 [Critical] Enhance fault detection to eliminate false negs        │
│  R5 [High]     Reduce detection latency and variability               │
│  R6 [Medium]   Broaden diagnostic hypothesis generation               │
│                                                                       │
│  RESOURCE (4 items):                                                  │
│  R7 [Critical] Improve fault detection for disk-fill                  │
│  R8 [High]     Implement hallucination mitigation                     │
│  R9 [High]     Optimize detection to reduce latency                   │
│  R10 [Medium]  Fix output token instrumentation                       │
│                                                                       │
│  === SUPPORTING TABLES FOR ANALYSIS ===                               │
│                                                                       │
│  Detection & Rates Table:                                             │
│    {phase2.tables.detection_rates — full table}                       │
│                                                                       │
│  TTD Timing Table:                                                    │
│    {phase2.tables.ttd — full table with mean, median, std, P95}       │
│                                                                       │
│  TTM Timing Table:                                                    │
│    {phase2.tables.ttm — full table}                                   │
│                                                                       │
│  Safety Summary Table:                                                │
│    {phase2.tables.safety_summary — RAI, Security, PII, Halluc}        │
│                                                                       │
│  Token Usage Table:                                                   │
│    {phase2.tables.token_usage — input/output means, sums}             │
│                                                                       │
│  Action Correctness Table:                                            │
│    {phase2.tables.action_correctness — per-category scores}           │
│                                                                       │
│  Scorecard Dimensions:                                                │
│    {phase2.scorecard.dimensions — 7 normalized values}                │
│                                                                       │
│  Known Limitations (from Call 5 output):                              │
│    {limitations_enriched — the enriched limitation list}              │
│                                                                       │
│  MERGE PATTERNS IDENTIFIED:                                           │
│  - R1, R4, R7 are all "improve fault detection" (Critical)            │
│  - R2, R5, R9 are all "reduce detection latency" (High)               │
│  - R3, R10 are both about output tokens (Medium)                      │
│  - R6 is unique to Network (Medium)                                   │
│  - R8 is unique to Resource (High)                                    │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

**Source fields:**
- `phase2.tables.recommendations` → existing 10 items
- `phase2.tables.detection_rates`, `ttd`, `ttm`, `safety_summary`, `token_usage`, `action_correctness` → all tables
- `phase2.scorecard.dimensions[]` → 7 normalized values
- `phase3.limitations_enriched` → enriched limitations from Call 5 (to ensure recommendations address discovered limitations)

---

## Prompt Template

```
You are enriching, labeling, and consolidating recommendations for an
AI agent certification report.

EXISTING RECOMMENDATIONS (10 items from 3 fault categories):
{existing_recommendations_table}

SUPPORTING DATA (all Phase 1/2 tables):
{supporting_tables_block}

KNOWN LIMITATIONS (enriched, from Call 5):
{enriched_limitations}

TASK:
1. MERGE recommendations that address the same underlying issue across
   categories into ONE item with category = "Cross-cutting"
   - Keep the highest priority from merged items
   - Rewrite merged text to be category-agnostic
   - Note which categories are affected (e.g., "Affected: Application, Network, Resource")

2. LABEL each recommendation:
   - "Detection" — improving fault detection rates or reducing false negatives
   - "Latency" — reducing detection or mitigation time
   - "Data Quality" — fixing instrumentation, token recording, data gaps
   - "Behavioral" — improving reasoning scope, reducing hallucination
   - "Coverage" — expanding instrumentation or metric coverage
   - null — if no label fits

3. ENRICH descriptions:
   - Rewrite each description with specific numbers from the supporting data
   - Reference the limitation it addresses (by L-number if applicable)
   - Include actionable detail (what to change, what the target should be)

4. ANALYZE tables and limitations to DISCOVER additional recommendations:
   - Look for limitations that have no corresponding recommendation
   - Look for scorecard dimensions below threshold with no remediation plan
   - Each new item needs: priority, category, label, description

RULES:
- Merge cross-category duplicates (target: fewer items than input 10)
- You MAY add 0-2 new recommendations from table analysis
- Final count: 6-10 items
- Priority must be "Critical", "High", "Medium", or "Low"
- Sort by priority: Critical > High > Medium > Low
- Number sequentially starting at 1
- Merged text should NOT reference a specific fault type if category is "Cross-cutting"
- Use ONLY data from the context — do not invent values

FORMAT:
Return a JSON array:
[
  {
    "index": 1,
    "priority": "Critical",
    "category": "Cross-cutting",
    "label": "Detection",
    "recommendation": "<enriched, consolidated description with specific numbers>"
  },
  ...
]
```

---

## Expected Output (reference — enriched from certification_report.json, 8 items)

```json
[
  {
    "index": 1,
    "priority": "Critical",
    "category": "Cross-cutting",
    "label": "Detection",
    "recommendation": "Improve fault detection algorithms across all categories by enhancing telemetry signal integration, lowering anomaly thresholds, and adding cross-metric correlation. Current detection rates (0-20%) are critically below the 60% minimum threshold. Affected: Application, Network, Resource. Addresses: L1, L2, L3."
  },
  {
    "index": 2,
    "priority": "High",
    "category": "Application",
    "label": "Latency",
    "recommendation": "Implement mechanisms to reduce time to detect and mitigate faults, such as optimizing telemetry data collection frequency, accelerating decision workflows, and pre-emptive detection models. Current mean TTD is 366s with 205s std dev."
  },
  {
    "index": 3,
    "priority": "High",
    "category": "Network",
    "label": "Latency",
    "recommendation": "Optimize detection workflows to reduce detection latency and variability by streamlining data collection and processing pipelines. Current mean TTD is 536.6s with 404.5s std dev — highest variability across all categories."
  },
  {
    "index": 4,
    "priority": "High",
    "category": "Resource",
    "label": "Behavioral",
    "recommendation": "Implement stricter hallucination mitigation techniques, such as verification against reliable sources or confidence thresholding. Current: 1/5 runs flagged with max hallucination score of 0.07. Addresses: L6."
  },
  {
    "index": 5,
    "priority": "High",
    "category": "Cross-cutting",
    "label": "Data Quality",
    "recommendation": "Fix output token instrumentation across all fault categories and input token capture for Network faults. Current telemetry reports 0 output tokens despite the agent producing diagnostic narratives and remediation actions. Addresses: L8, L10."
  },
  {
    "index": 6,
    "priority": "Medium",
    "category": "Application",
    "label": "Data Quality",
    "recommendation": "Review agent output generation components to ensure detailed, traceable remediation action logs and explanations are produced and captured as output tokens, improving transparency and auditability."
  },
  {
    "index": 7,
    "priority": "Medium",
    "category": "Network",
    "label": "Behavioral",
    "recommendation": "Broaden diagnostic hypothesis generation to systematically consider a wider range of potential root causes beyond DNS issues, especially under low-confidence conditions or ambiguous symptoms. Addresses: L9."
  },
  {
    "index": 8,
    "priority": "Medium",
    "category": "Resource",
    "label": "Data Quality",
    "recommendation": "Investigate and fix the cause of zero output tokens despite non-zero input tokens in some runs to ensure consistent and complete response generation and evidence documentation."
  }
]
```

---

## Pydantic Schema

```python
from pydantic import BaseModel, Field
from typing import Literal


class EnrichedRecommendation(BaseModel):
    """LLM output for a labeled, enriched, consolidated recommendation."""
    index:          int = Field(..., ge=1)
    priority:       Literal["Critical", "High", "Medium", "Low"]
    category:       str = Field(..., min_length=1)
    label:          str | None = None  # "Detection", "Latency", "Data Quality", "Behavioral", "Coverage"
    recommendation: str = Field(..., min_length=1)


class RecommendationsEnriched(BaseModel):
    """Envelope model for Call 6 output."""
    items:       list[EnrichedRecommendation] = Field(..., min_length=6, max_length=10)
    source:      Literal["llm", "fallback"]
    model:       str | None = None
    tokens_used: int = Field(default=0, ge=0)
```

### Label Taxonomy

| Label | Applies To |
|-------|-----------|
| `"Detection"` | Improving fault detection rates or reducing false negatives |
| `"Latency"` | Reducing detection or mitigation time |
| `"Data Quality"` | Fixing instrumentation, token recording, data gaps |
| `"Behavioral"` | Improving reasoning scope, reducing hallucination |
| `"Coverage"` | Expanding instrumentation or metric coverage |
| `null` | If no label fits cleanly |

---

## Dependency: Call 5 → Call 6

Call 6 receives the **enriched limitations from Call 5** as part of its input context. This allows the LLM to:
- Reference specific limitation numbers (L1, L2, ...) in recommendations
- Identify limitations without corresponding recommendations
- Ensure every High-severity limitation has a remediation plan

```
Phase 3E output (limitations_enriched)
    │
    ▼
Phase 3F input context (enriched_limitations field)
```

---

## Two-Level Validation

```
LLM Response (JSON string)
    │
    ▼
Level 1: json.loads() → RecommendationsEnriched.model_validate({"items": data, ...})
         ✓ Catches: missing fields, wrong types, empty strings,
           item count out of 6-10 range, invalid priority
    │
    ▼
Level 2: Verify label is in allowed set or null
         Verify at least 1 "Cross-cutting" item exists
         Verify priority sort order (Critical → High → Medium → Low)
         Verify merged text doesn't reference specific fault types for Cross-cutting items
    │
    ▼
If both pass → store in Phase3Output.recommendations_enriched
If either fails → retry (up to 3x) → fallback
```

---

## Validation Rules

| Rule | Check |
|------|-------|
| Item count | 6-10 items |
| Cross-cutting exists | At least 1 item with `category = "Cross-cutting"` |
| Priority sort | Items sorted: Critical > High > Medium > Low |
| Priority validity | Each `priority` must be "Critical", "High", "Medium", or "Low" |
| Sequential index | `index` values are 1..N sequential |
| Label validity | Each `label` must be "Detection", "Latency", "Data Quality", "Behavioral", "Coverage", or null |
| Cross-cutting text | Merged text should NOT reference a specific fault type if category is "Cross-cutting" |
| Minimum detail | Each recommendation must be at least 1 sentence |
| Limitation coverage | Every High-severity limitation from Call 5 should have a corresponding recommendation |
| No invented data | Use ONLY data from the context |

---

## Fallback (if LLM fails)

Group existing 10 items by keyword similarity (overlap > 50%):
- R1+R4+R7 → merge with "Cross-cutting", priority=Critical, label="Detection"
- R2+R5+R9 → merge with "Cross-cutting", priority=High, label="Latency"
- R3+R10 → merge with "Cross-cutting", priority=Medium, label="Data Quality"
- R6 → keep as Network, label="Behavioral"
- R8 → keep as Resource, label="Behavioral"

No new items added in fallback mode.

---

## Module Layout

```
engine/phase3/phase3f/
├── __init__.py                          # re-exports build_recommendations()
├── recommendation_builder.py            # prompt assembly, LLM call, validation, fallback
└── docs/
    └── recommendation_requirements.md    # This document
```

### Entry Point

```python
def build_recommendations(phase1: dict, phase2: dict, limitations_enriched: dict, llm_client) -> dict:
    """
    Args:
        limitations_enriched: Output from Phase 3E (Call 5) —
                              used as input context for recommendation enrichment.

    Returns:
        {
            "recommendations_enriched": {
                "items": [...],
                "source": "llm" | "fallback",
                "model": "gpt-4o",
                "tokens_used": 1023
            }
        }
    """
```

---

## Notebook Validation (Cell 8)

```
Cell 8:  Run LLM Call 6 (Recommendation Enrichment & Consolidation)
         - Display enriched items with labels vs original 10
         - Check: 6-10 items?
         - Check: at least 1 "Cross-cutting" item?
         - Check: each item has a label?
         - Check: sorted by priority?
         - Check: merged text is category-agnostic for Cross-cutting items?
         - Check: every High-severity limitation has a corresponding recommendation?
```
