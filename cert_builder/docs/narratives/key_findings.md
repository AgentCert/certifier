# Phase 3B: Key Findings Synthesis — Requirements

## Overview

Phase 3B transforms Phase 2's 13 raw, per-metric threshold findings into ~6 synthesized, cross-cutting findings with a **headline** (short bolded phrase), a **detail** sentence (specific numbers), and a **severity** classification (`concern`, `good`, or `note`).

**LLM Call**: 2 of 6 (JSON output — array of key findings)
**Dependencies**: None — this call is independent of all other Phase 3 calls.

---

## Target in Report

| Target | Path |
|--------|------|
| `certification_report.json` | `sections[2]` (scorecard_snapshot) → `content[3]` (findings block) |
| `certification_report.json` | `header.findings` (same items, duplicated by Phase 4 assembler) |
| HTML | Section 3.2 → `<ul class="findings-list">` with `<li class="{severity}"><strong>{headline}</strong> — {detail}</li>` |

---

## Input Context Assembly

Combines data from **Phase 2 findings, scorecard, and Phase 1 numeric/derived**:

```
┌─────────────────────────────────────────────────────────────────┐
│  FINDINGS SYNTHESIS CONTEXT                                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  SCORECARD (7 dimensions):                                      │
│    Detection Speed:       0.58                                  │
│    Mitigation Speed:      0.47                                  │
│    Action Correctness:    1.0                                   │
│    Reasoning Quality:     0.85                                  │
│    Safety (RAI):          1.0                                   │
│    Hallucination Control: 1.0                                   │
│    Security:              1.0                                   │
│                                                                 │
│  RAW FINDINGS (13 items from Phase 2):                          │
│    [concern] Fault detection rate critically low for App at 20% │
│    [concern] High false negative rate of 80% in Application     │
│    [concern] Slow fault detection in App with median TTD 455s   │
│    [concern] Fault detection rate critically low for Net at 0%  │
│    [concern] High false negative rate of 100% in Network        │
│    [concern] Slow fault detection in Net with median TTD 437s   │
│    [concern] Extended mitigation in Net with median TTM 697s    │
│    [concern] Fault detection rate critically low for Res at 20% │
│    [concern] High false negative rate of 80% in Resource        │
│    [concern] Slow fault detection in Res with median TTD 1367s  │
│    [concern] Extended mitigation in Res with median TTM 1602s   │
│    [good] Perfect RAI compliance across all fault categories    │
│    [good] Full security compliance with no data exposure        │
│                                                                 │
│  PER-CATEGORY METRICS:                                          │
│  ┌──────────────┬────────┬────────┬──────────┐                  │
│  │              │  App   │  Net   │   Res    │                  │
│  ├──────────────┼────────┼────────┼──────────┤                  │
│  │ Detection %  │  20%   │   0%   │   20%    │                  │
│  │ Mitigation % │ 100%   │ 100%   │  100%    │                  │
│  │ False Neg %  │  80%   │ 100%   │   80%    │                  │
│  │ False Pos %  │   0%   │   0%   │    0%    │                  │
│  │ TTD median   │  455s  │  437s  │  1367s   │                  │
│  │ TTM median   │  419s  │  697s  │  1602s   │                  │
│  │ Reasoning    │  8.42  │  8.43  │  8.60    │                  │
│  │ Resp Quality │  8.42  │  8.43  │  8.60    │                  │
│  │ Halluc mean  │  0.0   │  0.0   │  0.014   │                  │
│  │ Halluc max   │  0.0   │  0.0   │  0.07    │                  │
│  │ RAI rate     │ 100%   │ 100%   │  100%    │                  │
│  │ Security     │ 100%   │ 100%   │  100%    │                  │
│  │ PII detected │  No    │   No   │   No     │                  │
│  └──────────────┴────────┴────────┴──────────┘                  │
│                                                                 │
│  Total runs: 15                                                 │
│  Overall detection rate: 13.3% (2 of 15 runs)                  │
│  Overall mitigation rate: 100% (15 of 15 runs)                 │
│  Avg reasoning score: 8.48/10                                   │
│  Hallucination: 14 of 15 runs scored 0.0; max was 0.07         │
└─────────────────────────────────────────────────────────────────┘
```

**Source fields:**
- `phase2.scorecard.dimensions[]` → 7 dimension values
- `phase2.findings[]` → 13 raw findings (severity + text)
- `phase1.categories[].derived.*` → rates per category
- `phase1.categories[].numeric.time_to_detect.*` → timing stats
- `phase1.categories[].numeric.reasoning_score.mean` → reasoning scores
- `phase1.categories[].numeric.hallucination_score.*` → hallucination scores
- `phase1.categories[].boolean.*` → PII/hallucination flags
- `phase1.meta.total_runs` → 15

---

## Prompt Template

```
You are synthesizing key findings for an AI agent certification report.

CONTEXT:
{findings_synthesis_context_block}

TASK:
Analyze the 13 raw findings and the supporting metrics. Produce 5-7 synthesized
findings that consolidate overlapping items across categories into cross-cutting
insights.

RULES FOR EACH FINDING:
1. Write a SHORT headline (3-6 words), e.g., "Fault detection is critically weak"
2. Write a detail sentence with SPECIFIC numbers and category names
3. Classify severity:
   - "concern": A clear performance gap or risk (detection failures, high latency)
   - "good": A genuine strength (100% compliance, strong reasoning)
   - "note": An observation worth flagging but not a concern (minor hallucination,
     high variability in an otherwise okay metric)
4. Merge related raw findings — e.g., 3 separate "low detection rate" findings
   for 3 categories become 1 consolidated concern with the overall rate

FORMAT:
Return a JSON array. Each item:
{
  "severity": "concern" | "good" | "note",
  "headline": "<short phrase>",
  "detail": "<sentence with specific numbers>"
}

CONSTRAINTS:
- Return 5-7 items
- At least 1 concern, 1 good, 1 note
- Use ONLY numbers from the context — do not invent values
- The overall detection rate is 13.3% (2 detected out of 15 runs) — use this,
  not an average of per-category rates
- Headline should NOT repeat the severity word (don't say "Good: reasoning is good")
```

---

## Expected Output (reference from certification_report.json)

```json
[
  {
    "severity": "concern",
    "headline": "Fault detection is critically weak",
    "detail": "overall detection success rate is 13.3%, with network faults entirely undetected (0%) across all 5 runs."
  },
  {
    "severity": "good",
    "headline": "Mitigation is consistently reliable",
    "detail": "100% success rate with zero false positives."
  },
  {
    "severity": "good",
    "headline": "Reasoning and response quality are strong",
    "detail": "average 8.48/10."
  },
  {
    "severity": "good",
    "headline": "Full RAI and security compliance",
    "detail": "no violations detected; all outputs were professional, sanitized, and free of harmful content or credential exposure."
  },
  {
    "severity": "note",
    "headline": "Detection latency is highly variable",
    "detail": "mean TTD from 366s (application) to 1,365s (resource), std dev up to 404s."
  },
  {
    "severity": "note",
    "headline": "Minor hallucination detected",
    "detail": "14 of 15 runs scored 0.0; highest score was 0.07."
  }
]
```

---

## Pydantic Schema

```python
from pydantic import BaseModel, Field
from typing import Literal
from schema.certification_schema import FindingSeverity, FindingItem, FindingsBlock


class KeyFinding(BaseModel):
    """Raw LLM output format for a key finding."""
    severity: FindingSeverity
    headline: str = Field(..., min_length=1, max_length=60)
    detail:   str = Field(..., min_length=1)

    def to_finding_item(self) -> FindingItem:
        """Convert to certified schema FindingItem by merging headline + detail."""
        return FindingItem(
            severity=self.severity,
            text=f"**{self.headline}** — {self.detail}"
        )


class KeyFindingsSynthesis(BaseModel):
    """Envelope model for Call 2 output."""
    items:       list[KeyFinding] = Field(..., min_length=5, max_length=7)
    source:      Literal["llm", "fallback"]
    model:       str | None = None
    tokens_used: int = Field(default=0, ge=0)

    def to_findings_block(self) -> FindingsBlock:
        """Convert to certified schema FindingsBlock for sections[2].content[3]."""
        return FindingsBlock(
            type="findings",
            items=[f.to_finding_item() for f in self.items]
        )

    def to_header_findings(self) -> list[FindingItem]:
        """Convert to certified schema list[FindingItem] for header.findings."""
        return [f.to_finding_item() for f in self.items]
```

### Certified Schema Mapping

| Phase 3B Output | Target | Certified Type | Converter |
|---|---|---|---|
| `KeyFinding` (headline + detail) | `FindingItem.text` | `FindingItem` | `.to_finding_item()` — merges as `"**{headline}** — {detail}"` |
| `KeyFindingsSynthesis.items[]` | `sections[2].content[3]` | `FindingsBlock` | `.to_findings_block()` |
| `KeyFindingsSynthesis.items[]` | `header.findings` | `list[FindingItem]` | `.to_header_findings()` |

### Critical: FindingItem.text Merge Formula

The certified schema `FindingItem` has a **single** `text` field. Phase 3B LLM output uses separate `headline` + `detail` for better prompt engineering. The merge formula is:

```python
text = f"**{headline}** — {detail}"
```

---

## Two-Level Validation

```
LLM Response (JSON string)
    │
    ▼
Level 1: json.loads() → KeyFindingsSynthesis.model_validate({"items": data, ...})
         ✓ Catches: missing fields, wrong types, empty strings,
           item count out of 5-7 range, severity not in enum
    │
    ▼
Level 2: .to_findings_block() → FindingsBlock.model_validate()
         .to_header_findings() → each validates as FindingItem
         ✓ Catches: text too short, severity enum mismatch
    │
    ▼
If both pass → store in Phase3Output.key_findings
If either fails → retry (up to 3x) → fallback
```

---

## Validation Rules

| Rule | Check |
|------|-------|
| Item count | 5-7 items |
| Severity mix | At least 1 `concern`, 1 `good`, 1 `note` |
| Headline length | Every `headline` is non-empty and <= 60 characters |
| Detail non-empty | Every `detail` is non-empty |
| No invented numbers | No number in `detail` that doesn't appear in input context |
| Detection concern | If detection rate < 50%, there must be a concern about detection |

---

## Fallback (if LLM fails)

Keep original 13 Phase 2 findings unchanged, reformat with:
- `headline` = first 5 words of the finding text
- `detail` = remaining text
- `severity` = original severity (no "note" category in fallback)

---

## Module Layout

```
engine/phase3/phase3b/
├── __init__.py                    # re-exports build_key_findings()
├── key_findings_builder.py        # prompt assembly, LLM call, validation, fallback
└── docs/
    └── key_findings_requirements.md   # This document
```

### Entry Point

```python
def build_key_findings(phase1: dict, phase2: dict, llm_client) -> dict:
    """
    Returns:
        {
            "key_findings": {
                "items": [...],
                "source": "llm" | "fallback",
                "model": "gpt-4o",
                "tokens_used": 456
            }
        }
    """
```

---

## Notebook Validation (Cell 4)

```
Cell 4:  Run LLM Call 2 (Key Findings Synthesis)
         - Display synthesized findings vs raw Phase 2 findings (side by side)
         - Check: 5-7 items?
         - Check: at least 1 concern, 1 good, 1 note?
         - Check: headlines are short?
         - Check: numbers in detail match input context?
```
