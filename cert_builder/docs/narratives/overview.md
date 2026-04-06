# Phase 3: Generate Narratives & Synthesize — Orchestration Overview

## Overview

Phase 3 takes the deterministic outputs from Phase 1 (parsed context) and Phase 2 (computed content) and uses LLM reasoning to generate narrative text, synthesize cross-category patterns, and consolidate findings that cannot be produced by threshold rules alone.

**Why LLM?** Phase 2 produces raw, per-category, per-metric findings (13 items). The certification report needs synthesized, cross-cutting insights with nuanced severity classification. A human analyst would read the tables, charts, and assessments and write a coherent narrative — Phase 3 automates that analyst role.

```
phase1_parsed_context.json  ──┐
                               │
phase2_computed_content.json ──┼──► Phase 3 (6 LLM calls) ──► phase3_narratives.json
                               │
hardcoded_content.yaml ────────┘
```

---

## Sub-Phase Structure

Each LLM call is implemented as an independent sub-phase with its own requirements document, prompt template, Pydantic schema, validation rules, and fallback logic.

| Sub-Phase | LLM Call | Purpose | Requirements Doc |
|-----------|----------|---------|------------------|
| **phase3a** | Call 1 | Scope Narrative | `phase3a/docs/scope_narrative_requirements.md` |
| **phase3b** | Call 2 | Key Findings Synthesis | `phase3b/docs/key_findings_requirements.md` |
| **phase3c** | Call 3 | Qualitative Synthesis (7 dimensions) | `phase3c/docs/qualitative_requirements.md` |
| **phase3d** | Call 4 | Fault Category Analysis | `phase3d/docs/fault_analysis_requirements.md` |
| **phase3e** | Call 5 | Limitation Enrichment & Labeling | `phase3e/docs/limitation_requirements.md` |
| **phase3f** | Call 6 | Recommendation Enrichment & Consolidation | `phase3f/docs/recommendation_requirements.md` |

---

## Input Files

| File | What Phase 3 Uses From It |
|------|--------------------------|
| `phase1_parsed_context.json` | `meta` (agent identity, scope), `categories[].textual` (LLM Council consensus summaries), `categories[].numeric` (scores, timing), `categories[].derived` (rates), `categories[].boolean` (PII, hallucination flags) |
| `phase2_computed_content.json` | `scorecard.dimensions` (7 normalized values), `findings` (13 raw threshold findings), `tables.limitations` (10 ranked items), `tables.recommendations` (10 prioritized items), `assessments` (12 assessment blocks), `tables.safety_summary`, `tables.detection_rates` |

---

## Output: `phase3_narratives.json`

```json
{
  "scope_narrative":          { ... },   // Phase 3A — Call 1
  "key_findings":             { ... },   // Phase 3B — Call 2
  "qualitative_findings":     { ... },   // Phase 3C — Call 3 (7 sub-sections)
  "fault_category_analysis":  { ... },   // Phase 3D — Call 4 (detail line + analysis per category)
  "limitations_enriched":     { ... },   // Phase 3E — Call 5 (label + enrich + discover)
  "recommendations_enriched": { ... },   // Phase 3F — Call 6 (label + enrich + merge + discover)
  "fallbacks_used": false,
  "errors": []
}
```

---

## Gap Analysis: What Phase 2 Has vs What the Report Needs

| Report Section | What the Report Shows | Phase 2 Has | Gap (Phase 3 Must Produce) |
|---|---|---|---|
| 1. Executive Summary | Scope narrative paragraph | Identity/scope/categories cards | **Narrative paragraph** (Phase 3A) |
| 3. Scorecard Snapshot | 6 synthesized findings with `headline -- detail` format, severity = concern/good/note | 13 raw per-metric findings, severity = concern/good only | **Synthesized key findings** (Phase 3B) |
| 4. Qualitative Findings | 7 sub-sections (one per evaluation dimension) each with 1-3 finding items | Per-category assessment blocks + tables | **Cross-category qualitative finding items** (Phase 3C) |
| 10. Fault Category Analysis | Per-category heading with detail line + 2-4 sentence analytical synthesis | Assessment blocks only, no heading detail lines or synthesis | **Heading detail lines + per-category analysis** (Phase 3D) |
| 11. Known Limitations | 10 items with severity, category, frequency, label badge, enriched description | 10 items without labels or detailed descriptions | **Labeled, enriched & extended limitations** (Phase 3E) |
| 12. Recommendations | 8 consolidated items with "Cross-cutting" categories, labels, enriched descriptions | 10 per-category items (3+3+4) | **Labeled, enriched & consolidated recommendations** (Phase 3F) |

---

## Dependency Graph

```
Phase 1 + Phase 2 Input
    │
    ├──► Phase 3A (Scope Narrative)           ──┐
    ├──► Phase 3B (Key Findings)              ──┤
    ├──► Phase 3C (Qualitative Synthesis)     ──┤
    ├──► Phase 3D (Fault Category Analysis)   ──┼──► Assembler ──► phase3_narratives.json
    ├──► Phase 3E (Limitation Enrichment)     ──┤
    │         │                                 │
    │         ▼                                 │
    └──► Phase 3F (Recommendation Enrichment) ──┘
```

**Parallelism**: Calls 1-5 (Phase 3A-3E) can run in parallel. Call 6 (Phase 3F) depends on Call 5 output (enriched limitations feed into recommendation context).

---

## Pydantic Schema Enforcement — Binding to `certification_schema.py`

Phase 3 outputs MUST be validated against, and constructable into, the existing `certification_schema.py` Pydantic v2 models. This ensures every LLM-generated narrative can be assembled into a valid `CertificationReport` without schema violations.

### Canonical Schema Reference

**File**: `schema/certification_schema.py`
**Root model**: `CertificationReport` (Pydantic v2, `extra="forbid"`)

Key models Phase 3 must produce data for:

| Certified Schema Type | Fields | Phase 3 Mapping |
|---|---|---|
| `FindingSeverity` (Enum) | `concern`, `good`, `note` | Used by Phase 3B (key findings), Phase 3C (qualitative findings) |
| `FindingItem` | `severity: FindingSeverity`, `text: str` | Phase 3B output — `headline` + `detail` merged into single `text`: `"**{headline}** — {detail}"` |
| `FindingsBlock` | `type: "findings"`, `items: list[FindingItem]` | Phase 3B → `sections[2].content[3]`, Phase 3C → `sections[3].content[N]` |
| `HeadingBlock` | `type: "heading"`, `title: str`, `detail: str \| None` | Phase 3D → fault category headings |
| `TextBlock` | `type: "text"`, `body: str`, `style: TextStyle \| None` | Phase 3A → scope narrative, Phase 3D → analysis paragraphs |
| `Section` | `id, number, part, title, intro, content` | Phase 3A populates `intro` of section 1; other phases populate content blocks |
| `Header` | `scorecard: list[ScorecardDimension]`, `findings: list[FindingItem]` | Phase 3B output maps to `header.findings` (Phase 4 copies it) |

### Critical Formatting Contract: `FindingItem.text`

The certified schema's `FindingItem` has a SINGLE `text` field. Phase 3 LLM output uses `headline` + `detail` for easier prompt engineering. The merge formula is:

```python
FindingItem.text = f"**{headline}** — {detail}"
```

This applies to Phase 3B (`KeyFinding`) and Phase 3C (`QualitativeFinding`).

### Two-Level Validation Flow

```
LLM Response (JSON string)
    │
    ▼
Level 1: json.loads() → Phase 3 intermediate Pydantic model
         (e.g., KeyFindingsSynthesis.model_validate(data))
         ✓ Catches: missing fields, wrong types, empty strings,
           count out of range (min_length/max_length on lists)
    │
    ▼
Level 2: .to_certified() → Certified schema model
         (e.g., .to_findings_block() → FindingsBlock.model_validate())
         ✓ Catches: severity not in FindingSeverity enum,
           text too short (min_length=1), row count mismatch
    │
    ▼
If both pass → store in Phase3Output
If either fails → retry (up to 3x) → fallback if all retries fail
```

### Phase 3 → Certified Block Type Mapping

| Phase 3 Output | Target Section | Certified Block Type |
|---|---|---|
| `scope_narrative.text` (3A) | `sections[0].intro` | `str` (Section.intro field) |
| `key_findings.items[]` (3B) | `sections[2].content[3]` | `FindingsBlock(items=[FindingItem(...)])` |
| `key_findings.items[]` (3B) | `header.findings` | `list[FindingItem]` |
| `qualitative_findings.{dim}[]` (3C) | `sections[3].content[N]` | `FindingsBlock(items=[FindingItem(...)])` |
| `fault_category_analysis.categories[].title/detail` (3D) | `sections[9].content[N]` | `HeadingBlock(type="heading", title, detail)` |
| `fault_category_analysis.categories[].analysis` (3D) | `sections[9].content[N+1]` | `TextBlock(type="text", body)` |
| `limitations_enriched.items[]` (3E) | `sections[10].content[]` | Limitation blocks (severity, category, label, frequency, description) |
| `recommendations_enriched.items[]` (3F) | `sections[11].content[]` | Recommendation blocks (priority, category, label, description) |

### Post-Assembly Validation (Phase 4 responsibility)

```python
from schema.certification_schema import CertificationReport

report = CertificationReport.model_validate(assembled_dict)
# If this passes, the entire report is structurally valid.
# CertificationReport uses extra="forbid", so no stray fields allowed.
```

---

## Complete Pydantic Schema Hierarchy

```
Phase3Output:
  scope_narrative:          ScopeNarrative              (Phase 3A)
  key_findings:             KeyFindingsSynthesis         (Phase 3B)
  qualitative_findings:     QualitativeSynthesis         (Phase 3C)
  fault_category_analysis:  FaultCategoryAnalysisResult  (Phase 3D)
  limitations_enriched:     LimitationsEnriched          (Phase 3E)
  recommendations_enriched: RecommendationsEnriched      (Phase 3F)
  fallbacks_used:           bool
  errors:                   list[ErrorRecord]

ErrorRecord:
  call:      str        # which LLM call failed
  error:     str        # error message
  retries:   int        # how many retries attempted
  fallback:  bool       # whether fallback was used

──────────────────────────────────────────────

ScopeNarrative (3A):
  text:         str
  source:       Literal["llm", "fallback"]
  model:        str | None
  tokens_used:  int
  ► .to_section_intro() → str

──────────────────────────────────────────────

KeyFinding (3B — INTERMEDIATE):
  severity:     FindingSeverity          ← imported from certification_schema
  headline:     str   # max 60 chars
  detail:       str
  ► .to_finding_item() → FindingItem    ← certified schema type

KeyFindingsSynthesis (3B):
  items:        list[KeyFinding]          # 5-7 items
  source, model, tokens_used
  ► .to_findings_block() → FindingsBlock
  ► .to_header_findings() → list[FindingItem]

──────────────────────────────────────────────

QualitativeFinding (3C — INTERMEDIATE):
  severity:     FindingSeverity          ← imported
  headline:     str   # max 50 chars
  detail:       str
  ► .to_finding_item() → FindingItem

QualitativeSynthesis (3C):
  detection:          list[QualitativeFinding]    # 1-3 items
  mitigation:         list[QualitativeFinding]    # 1-3 items
  action_correctness: list[QualitativeFinding]    # 1-2 items
  reasoning:          list[QualitativeFinding]    # 1-3 items
  safety:             list[QualitativeFinding]    # 1-2 items
  hallucination:      list[QualitativeFinding]    # 1-2 items
  security:           list[QualitativeFinding]    # 1-2 items
  source, model, tokens_used
  ► .to_findings_blocks() → dict[str, FindingsBlock]

──────────────────────────────────────────────

FaultCategoryAnalysis (3D — INTERMEDIATE):
  title:     str       # "{Label} Faults"
  detail:    str       # pipe-delimited summary line
  analysis:  str       # 2-4 sentence per-category synthesis
  ► .to_heading_block() → HeadingBlock
  ► .to_analysis_block() → TextBlock

FaultCategoryAnalysisResult (3D):
  categories:  dict[str, FaultCategoryAnalysis]
  source, model, tokens_used
  ► .to_content_blocks() → list[HeadingBlock | TextBlock]

──────────────────────────────────────────────

EnrichedLimitation (3E):
  index:      int
  severity:   Literal["High", "Medium", "Low"]
  category:   str
  label:      str | None    # "Data Quality", "Detection Gap", "Latency", "Coverage Gap", "Behavioral"
  frequency:  str           # "X/Y runs (Z%)"
  limitation: str

LimitationsEnriched (3E):
  items:        list[EnrichedLimitation]    # 10-13 items
  source, model, tokens_used

──────────────────────────────────────────────

EnrichedRecommendation (3F):
  index:           int
  priority:        Literal["Critical", "High", "Medium", "Low"]
  category:        str
  label:           str | None    # "Detection", "Latency", "Data Quality", "Behavioral", "Coverage"
  recommendation:  str

RecommendationsEnriched (3F):
  items:        list[EnrichedRecommendation]     # 6-10 items
  source, model, tokens_used
```

---

## Data Flow Diagram

```
PHASE 1 OUTPUT                    PHASE 2 OUTPUT
──────────────                    ──────────────
meta ─────────────────────┐
  agent_name              │
  agent_id                ├──────────────► Phase 3A: Scope Narrative
  certification_date      │                ► ScopeNarrative
  categories_summary[]    │
  total_runs, etc.        │
                          │
                          │       scorecard.dimensions[]
                          │       findings[] (13 raw)
categories[].numeric ─────┼──────────────► Phase 3B: Key Findings Synthesis
  reasoning_score.mean    │                ► KeyFindingsSynthesis
  hallucination_score.*   │
  time_to_detect.*        │
categories[].derived ─────┤
  detection/mitigation %  │
  false_neg/pos %         │
  rai/security rates      │
                          │
                          │
categories[].textual ─────┤
  agent_summary           │
  reasoning consensus     ├──────────────► Phase 3C: Qualitative Synthesis
  rai consensus           │                ► QualitativeSynthesis (7 sub-sections)
  security consensus      │
categories[].boolean ─────┤       tables.safety_summary
  pii_detected_any        │       scorecard.dimensions[]
  hallucination_det_any   │
categories[].numeric ─────┤
  all timing + scores     │
categories[].derived ─────┤
  all rates               │
                          │
                          │
categories[].numeric ─────┤
  reasoning_score.mean    │
  response_quality.mean   │
categories[].derived ─────┼──────────────► Phase 3D: Fault Category Analysis
  detection/mitigation %  │                ► FaultCategoryAnalysisResult
categories[].faults_tested│
categories[].total_runs   │
categories[].textual ─────┤       tables.limitations (filtered)
  all 4 assessments       │       tables.recommendations (filtered)
                          │
                          │       tables.limitations
                          │       + ALL Phase 1/2 tables
                          ├──────────────► Phase 3E: Limitation Enrichment
                          │                ► LimitationsEnriched
                          │                        │
                          │       tables.recommendations
                          │       + ALL Phase 1/2 tables
                          │       + Phase 3E output (enriched limitations)
                          └──────────────► Phase 3F: Recommendation Enrichment
                                           ► RecommendationsEnriched
```

---

## Error Handling Strategy

```
For each LLM call (Phase 3A through 3F):
  1. Assemble prompt context from Phase 1 + Phase 2
  2. Send request to Azure OpenAI (model: gpt-4o)
  3. If success:
     a. Parse response as JSON (3B-3F) or plain text (3A)
     b. Level 1 Validation: validate against Phase 3 intermediate Pydantic schema
     c. Level 2 Validation: convert to certified schema types via .to_certified()
     d. If EITHER level fails → treat as failure, go to step 4
     e. Record source = "llm"
  4. If failure:
     a. Retry up to 3 times with exponential backoff (1s, 2s, 4s)
     b. If all retries fail → use deterministic fallback
     c. Fallback output MUST ALSO pass two-level validation
     d. Record source = "fallback"
     e. Set fallbacks_used = true
     f. Log error in errors[]
  5. After all 6 calls complete:
     a. Construct Phase3Output and validate
     b. Write phase3_narratives.json
```

---

## Notebook Validation Plan

```
Cell 1:   Load phase1_parsed_context.json and phase2_computed_content.json
          Confirm both files load and have expected top-level keys

Cell 2:   Display assembled prompt context for each LLM call (BEFORE sending)
          - Show scope context block (Phase 3A)
          - Show findings synthesis context (Phase 3B)
          - Show qualitative context (Phase 3C)
          Verify each context block has all required data fields

Cell 3:   Run Phase 3A (Scope Narrative) — see phase3a/docs/
Cell 4:   Run Phase 3B (Key Findings Synthesis) — see phase3b/docs/
Cell 5:   Run Phase 3C (Qualitative Synthesis) — see phase3c/docs/
Cell 6:   Run Phase 3D (Fault Category Analysis) — see phase3d/docs/
Cell 7:   Run Phase 3E (Limitation Enrichment) — see phase3e/docs/
Cell 8:   Run Phase 3F (Recommendation Enrichment) — see phase3f/docs/

Cell 9:   Test fallback mode — run with LLM disabled
          - Verify all 6 calls produce fallback output
          - Verify fallbacks_used = true
          - Verify output still passes two-level Pydantic validation
          - Verify .to_certified() converters produce valid blocks

Cell 10:  Certified schema round-trip validation
          - Import CertificationReport from certification_schema
          - Convert all Phase 3 outputs to certified types
          - Construct a mock sections list using Phase 3 + Phase 2 data
          - Validate with CertificationReport.model_validate()
          - Report any validation errors

Cell 11:  Write phase3_narratives.json
          - Display file size
          - Display all top-level keys

Cell 12:  Display token usage and cost estimate
          - Total tokens across all LLM calls
          - Cost estimate at $X per 1M tokens
```

---

## Module Layout

```
engine/phase3/
├── __init__.py
├── assembler.py              # Runs all calls, merges into Phase3Output, validates
├── schemas.py                # All Phase 3 Pydantic models (imports from certification_schema)
│                              #   - Intermediate: KeyFinding, QualitativeFinding, etc.
│                              #   - Envelopes: Phase3Output, ScopeNarrative, etc.
│                              #   - .to_certified() converters
├── phase3a/
│   ├── __init__.py
│   ├── scope_narrative_builder.py
│   └── docs/
│       └── scope_narrative_requirements.md
├── phase3b/
│   ├── __init__.py
│   ├── key_findings_builder.py
│   └── docs/
│       └── key_findings_requirements.md
├── phase3c/
│   ├── __init__.py
│   ├── qualitative_builder.py
│   └── docs/
│       └── qualitative_requirements.md
├── phase3d/
│   ├── __init__.py
│   ├── fault_analysis_builder.py
│   └── docs/
│       └── fault_analysis_requirements.md
├── phase3e/
│   ├── __init__.py
│   ├── limitation_builder.py
│   └── docs/
│       └── limitation_requirements.md
├── phase3f/
│   ├── __init__.py
│   ├── recommendation_builder.py
│   └── docs/
│       └── recommendation_requirements.md
└── docs/
    └── phase3_requirements.md   # This document (orchestration overview)
```

---

## Summary

| Sub-Phase | Call | Type | Input Sources | Output | Items |
|-----------|------|------|---------------|--------|-------|
| 3A | 1 | LLM | Phase 1 meta | Scope narrative paragraph | 1 paragraph |
| 3B | 2 | LLM | Phase 2 findings + scorecard + Phase 1 metrics | Synthesized key findings | 5-7 items |
| 3C | 3 | LLM | Phase 1 textual/numeric/derived/boolean + Phase 2 scorecard + safety table | Qualitative findings (7 sub-sections) | 8-16 items |
| 3D | 4 | LLM | Phase 1 all per-category data + Phase 2 assessments/limitations/recommendations | Fault category analysis (detail line + synthesis) | 1 per category |
| 3E | 5 | LLM | Phase 2 limitations + ALL Phase 1/2 tables | Enriched & labeled limitations (+ new discoveries) | 10-13 items |
| 3F | 6 | LLM | Phase 2 recommendations + ALL Phase 1/2 tables + Phase 3E output | Enriched, labeled & consolidated recommendations (+ new discoveries) | 6-10 items |

---

## Cross-Validation Matrix

| Phase 3 Output | Validate Against | What to Check |
|---|---|---|
| scope_narrative.text (3A) | HTML Section 1.2 `scope-narrative` div | Covers agent name, fault types, run count, methodology |
| key_findings.items (3B) | certification_report.json `scorecard_snapshot.content[3]` | Count (~6), severity mix, headline+detail format |
| qualitative_findings.reasoning (3C) | certification_report.json `qualitative_findings.content[1]` | Finding count (2-3), severity, headline format |
| qualitative_findings.safety (3C) | certification_report.json `qualitative_findings.content[3]` | Finding count (2-3), mentions both RAI and security |
| qualitative_findings.hallucination (3C) | certification_report.json `qualitative_findings.content[6]` | References 14/15 and 0.07, count (1-2) |
| fault_category_analysis.categories (3D) | certification_report.json `fault_category_analysis.content` | Detail line format, correct percentages, analysis 2-4 sentences |
| limitations_enriched.items (3E) | certification_report.json `limitations.content[]` | Count (10-13), each has label, severity-sorted |
| recommendations_enriched.items (3F) | certification_report.json `recommendations.content[]` | Count (6-10), has Cross-cutting items, sorted by priority |

---

## Appendix A: Post-Validation Gap Analysis

After writing the requirements above, a comprehensive cross-validation was performed against `certification_report.json` (all 12 sections) and `phase2_computed_content.json`. Below are the gaps found and how to address them.

### Gaps Identified

#### Gap 1: Missing Section Intros (Phase 2E backfill)

Phase 2E `hardcoded.section_intros` covers 7 of 12 sections. The following section intros exist in the report but are NOT in Phase 2E:

| Section | Report Intro Text | Action |
|---|---|---|
| 3. Scorecard Snapshot | "Overall certification scorecard with radar visualization and key findings from the evaluation." | Add to `hardcoded_content.yaml` under `section_intros.scorecard` |
| 4. Qualitative Findings | "Cross-category consensus from the LLM Council on reasoning quality, safety compliance, and hallucination." | Add to `hardcoded_content.yaml` under `section_intros.qualitative` |
| 5. Detection & Response | Derived from `definitions.ttd` (shortened version) | Add to `hardcoded_content.yaml` under `section_intros.detection` |
| 6. Accuracy & Efficiency | "This section evaluates the accuracy of the agent's remediation actions and the efficiency of its diagnostic process." | Add to `hardcoded_content.yaml` under `section_intros.accuracy` |

**Resolution**: Backfill Phase 2E `hardcoded_content.yaml` with these 4 additional `section_intros` entries. This is a Phase 2E patch, not a Phase 3 concern.

#### Gap 2: Limitation Frequency Formatting

Phase 2 stores frequency as integer (e.g., `4`). The report shows `"4/5 (80%)"` format.

**Resolution**: Now handled by Phase 3E (Limitation Enrichment). The `EnrichedLimitation.frequency` field outputs the formatted string directly. No longer a Phase 4 concern.

#### Gap 3: Token Usage Computed Text

Report Section 9 has two text blocks not produced by any phase:
1. `"Total tokens consumed across 15 runs: 4,376"` — computed sum
2. `"Data quality note: Zero values detected for: ..."` — data quality audit

**Resolution**: Add these as deterministic computations in the assembler (Phase 4). They require no LLM reasoning.

#### Gap 4: Table Column Transformations

Several report tables have different column names, additional computed columns, or reordered columns compared to Phase 2 tables.

**Resolution**: Table enrichment/transformation is a deterministic mapping task for Phase 4. Phase 3 does NOT handle this.

#### Gap 5: `header.findings` Duplication

The report's top-level `header` object contains a `findings` array identical to `sections[2].content[3].items`.

**Resolution**: Phase 4 copies `key_findings.items` to both `header.findings` and `sections[2].content[3]`.

#### Gap 6: Scorecard Dimension Name Mapping

Phase 2 uses `"Normalized TTD"`, report uses `"Detection Speed"`, etc.

**Resolution**: Static name mapping in Phase 4.

### Gap Classification Summary

| Gap | Owner | Type | Effort |
|-----|-------|------|--------|
| Missing section intros | Phase 2E (backfill YAML) | Hardcoded content | Small |
| Limitation frequency format | Phase 3E | Now part of enriched output | Resolved |
| Token usage text blocks | Phase 4 assembler | Deterministic computation | Small |
| Table column transforms | Phase 4 assembler | Deterministic mapping | Medium |
| header.findings duplication | Phase 4 assembler | Copy | Trivial |
| Scorecard name mapping | Phase 4 assembler | Static mapping | Trivial |

**Key insight**: All gaps are either Phase 2E backfills or Phase 4 assembler tasks. No additional LLM calls are needed beyond the 6 sub-phases defined above.

---

## Appendix B: Section Coverage Matrix (Complete)

Every report section mapped to its data sources across all phases:

| Section | `intro` Source | Content Sources | Phase 3 Contribution |
|---|---|---|---|
| 1. Exec Summary | **Phase 3A** | Phase 2F cards | Scope narrative |
| 2. Methodology | Phase 2E `section_intros.methodology` | Phase 2E methodology_bullets + Phase 2B judge_models | None |
| 3. Scorecard | Phase 2E `section_intros.scorecard` (GAP→backfill) | Phase 2A scorecard + Phase 2C radar + **Phase 3B** | Key findings synthesis |
| 4. Qualitative | Phase 2E `section_intros.qualitative` (GAP→backfill) | **Phase 3C** + Phase 2B safety_summary (transformed) | Qualitative finding items |
| 5. Detection | Phase 2E `section_intros.detection` (GAP→backfill) | Phase 2C charts + Phase 2B tables + Phase 2E definitions | None |
| 6. Accuracy | Phase 2E `section_intros.accuracy` (GAP→backfill) | Phase 2C heatmap + Phase 2B action_correctness + Phase 2E na_explanation | None |
| 7. Reasoning | Phase 2E `section_intros.reasoning` | Phase 2C charts + Phase 2B tables + Phase 2E definitions | None |
| 8. Safety | Phase 2E `section_intros.safety` | Phase 2C chart + Phase 2B rai/security tables (transformed) | None |
| 9. Resources | Phase 2E `section_intros.token_usage` | Phase 2C chart + Phase 2B token_usage (transformed) + computed totals | None |
| 10. Fault Analysis | Phase 2E `section_intros.fault_analysis` | **Phase 3D** headers + analysis + Phase 2D assessments | Fault category analysis |
| 11. Limitations | Phase 2E `section_intros.limitations` | Phase 2B limitations + **Phase 3E** enriched items | Enriched limitations |
| 12. Recommendations | Phase 2E `section_intros.recommendations` | **Phase 3F** enriched, consolidated recommendations | Enriched recommendations |
