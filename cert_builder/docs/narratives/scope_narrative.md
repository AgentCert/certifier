# Phase 3A: Scope Narrative — Requirements

## Overview

Phase 3A generates a 3-5 sentence executive scope paragraph for Section 1 that describes what was tested, how, and at what scale. This narrative appears in the `intro` field of the executive_summary section and inside a `scope-narrative` div in the HTML.

**LLM Call**: 1 of 6 (plain text output, no JSON wrapper)
**Dependencies**: None — this call is independent of all other Phase 3 calls.

---

## Target in Report

| Target | Path |
|--------|------|
| `certification_report.json` | `sections[0]` (executive_summary) → `intro` field |
| HTML | Section 1.2 → `<div class="scope-narrative">` |

---

## Input Context Assembly

All data comes from **Phase 1 meta + categories_summary**:

```
┌─────────────────────────────────────────────────────┐
│  SCOPE CONTEXT                                      │
├─────────────────────────────────────────────────────┤
│  Agent:             {meta.agent_name}               │
│  Agent ID:          {meta.agent_id}                 │
│  Date:              {meta.certification_date}       │
│  Categories:        3                               │
│    - Application:   container-kill  (5 runs)        │
│    - Network:       pod-dns-error   (5 runs)        │
│    - Resource:      disk-fill       (5 runs)        │
│  Total Runs:        15                              │
│  Total Faults:      3                               │
│  Runs per Fault:    5                               │
│  Evaluation Method: Multi-judge LLM Council         │
│                     k=3 judges + meta-reconciliation│
└─────────────────────────────────────────────────────┘
```

**Source fields:**
- `phase1.meta.agent_name` → "Flash Agent"
- `phase1.meta.agent_id` → "flash-001-abc123"
- `phase1.meta.certification_date` → "2026-03-08"
- `phase1.meta.total_runs` → 15
- `phase1.meta.total_faults_tested` → 3
- `phase1.meta.total_fault_categories` → 3
- `phase1.meta.runs_per_fault` → 5
- `phase1.meta.categories_summary[]` → label, fault, runs

---

## Prompt Template

```
You are writing the executive scope paragraph for an AI agent certification report.

CONTEXT:
{scope_context_block}

TASK:
Write a 3-5 sentence paragraph that covers:
1. What agent was tested and its purpose
2. What fault types and categories were covered (name them)
3. How many runs were executed and the evaluation methodology
4. What capabilities were evaluated (detection, diagnosis, remediation, compliance)

RULES:
- Use ONLY the numbers provided in the context — do not invent or round values
- Bold the agent name, fault category count, fault names, and total run count
- Professional, technical tone
- Do NOT include the certification date in the narrative
- Do NOT add opinions, judgments, or findings — this is scope only

OUTPUT: Return ONLY the paragraph text, no JSON wrapper.
```

---

## Expected Output (reference from HTML)

> This certification evaluates the **Flash Agent** across a structured fault-injection campaign designed to measure resilience, diagnostic quality, and safety compliance under realistic failure conditions. The experiment targeted **3 distinct fault categories** — application faults, network faults, and resource faults — each exercised by a representative fault type (**container-kill**, **pod-dns-error**, **disk-fill**). A total of **15 independent runs** were executed to establish statistically grounded performance baselines. Each run subjected the agent to a controlled Kubernetes fault scenario and evaluated its ability to **detect**, **diagnose**, and **remediate** the injected fault while adhering to responsible AI and security compliance standards. The evaluation employs a multi-judge consensus approach with high inter-judge agreement to ensure reliability.

---

## Pydantic Schema

```python
from pydantic import BaseModel, Field
from typing import Literal

class ScopeNarrative(BaseModel):
    """Envelope model for Call 1 output."""
    text:        str = Field(..., min_length=1)
    source:      Literal["llm", "fallback"]
    model:       str | None = None
    tokens_used: int = Field(default=0, ge=0)

    def to_section_intro(self) -> str:
        """Returns the text to set as sections[0].intro"""
        return self.text
```

### Certified Schema Mapping

| Phase 3A Output | Target | Certified Type |
|---|---|---|
| `ScopeNarrative.text` | `sections[0].intro` | `str` (Section.intro field) |

No `FindingItem`, `FindingsBlock`, or other complex certified types — Call 1 produces plain text only.

---

## Validation Rules

| Rule | Check |
|------|-------|
| Non-empty | `text` must be non-empty string |
| Agent name present | Must contain `meta.agent_name` (e.g., "Flash Agent") |
| Fault category mention | Must mention at least one fault category name |
| Sentence count | 2-6 sentences (split on `. `) |
| No invented numbers | Must NOT contain numbers not derivable from the input context |

---

## Fallback (if LLM fails)

Template string:

> "{agent_name} was evaluated across {n_categories} fault categories ({category_list}) with {total_runs} total runs across {total_faults} fault types ({fault_list}). Each fault was tested with {runs_per_fault} independent runs. The evaluation used a multi-judge LLM Council with k=3 independent judges and meta-reconciliation."

---

## Module Layout

```
engine/phase3/phase3a/
├── __init__.py               # re-exports build_scope_narrative()
├── scope_narrative_builder.py  # prompt assembly, LLM call, validation, fallback
└── docs/
    └── scope_narrative_requirements.md   # This document
```

### Entry Point

```python
def build_scope_narrative(phase1: dict, llm_client) -> dict:
    """
    Returns:
        {
            "scope_narrative": {
                "text": "...",
                "source": "llm" | "fallback",
                "model": "gpt-4o",
                "tokens_used": 123
            }
        }
    """
```

---

## Notebook Validation (Cell 3)

```
Cell 3:  Run LLM Call 1 (Scope Narrative)
         - Display generated text
         - Check: mentions agent name?
         - Check: mentions fault categories?
         - Check: 2-6 sentences?
         - Check: no invented numbers?
```
