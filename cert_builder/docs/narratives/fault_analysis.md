# Phase 3D: Fault Category Analysis — Requirements

## Overview

Phase 3D generates per-category analytical synthesis for Section 10. For each fault category, the LLM reads the category's metrics, assessment blocks, and known limitations to produce:

1. A **heading detail line** — structured summary of key metrics (deterministic format, but included in the LLM output for context coherence)
2. A **per-category analytical summary** — 2-4 sentence synthesis highlighting the category's strengths, weaknesses, and notable patterns that go beyond what the individual assessment blocks say

The analytical summary is what makes this an LLM call rather than a deterministic operation.

**LLM Call**: 4 of 6 (JSON output — per-category object with detail line + analysis)
**Dependencies**: None — this call is independent of all other Phase 3 calls.

---

## Target in Report

| Target | Path |
|--------|------|
| `certification_report.json` | `sections[9]` (fault_category_analysis) -> `content[]` (heading blocks with `detail` field + analytical text blocks) |
| HTML | Sections 10.1, 10.2, 10.3 -> heading detail bar + category narrative |

---

## Input Context Assembly (per category)

```
FAULT CATEGORY ANALYSIS CONTEXT: {category_label}

Category:    {label}
Fault:       {faults_tested[0]}
Runs:        {total_runs}

=== KEY METRICS ===
Detection rate:       {detection_rate}%
Mitigation rate:      {mitigation_rate}%
False negative rate:  {false_neg}%
False positive rate:  {false_pos}%
Reasoning score:      {reasoning_score.mean}/10
Response quality:     {response_quality.mean}/10
Hallucination mean:   {hallucination.mean}
Hallucination max:    {hallucination.max}
Action correctness:   {action_correctness.mean or "N/A"}
TTD median:           {ttd_median}s
TTM median:           {ttm_median}s
RAI compliance:       {rai_rate}%
Security compliance:  {security_rate}%
PII detected:         {pii_any}

=== LLM COUNCIL ASSESSMENTS (4 blocks) ===
Agent Summary:
  "{agent_summary.consensus_summary}"
  Confidence: {confidence}, Agreement: {agreement}

Response & Reasoning Quality:
  Rating: {severity_label}
  "{reasoning.consensus_summary}"

Security Compliance:
  Rating: {severity_label}
  "{security.consensus_summary}"

RAI Compliance:
  Rating: {severity_label}
  "{rai.consensus_summary}"

=== CATEGORY LIMITATIONS ===
{list of limitations for this category from Phase 2 table}

=== CATEGORY RECOMMENDATIONS ===
{list of recommendations for this category from Phase 2 table}
```

**Source fields (per category):**
- `phase1.categories[N].label` -> category name
- `phase1.categories[N].faults_tested` -> fault names
- `phase1.categories[N].total_runs` -> run count
- `phase1.categories[N].numeric.*` -> all numeric metrics
- `phase1.categories[N].derived.*` -> all derived rates
- `phase1.categories[N].boolean.*` -> PII, hallucination detected flags
- `phase1.categories[N].textual.*` -> all 4 assessment consensus texts with ratings
- `phase2.tables.limitations.rows` (filtered to this category)
- `phase2.tables.recommendations.rows` (filtered to this category)

---

## Prompt Template

```
You are analyzing a specific fault category for an AI agent certification report.

CATEGORY CONTEXT:
{category_context_block}

TASK:
1. Generate the heading DETAIL LINE using this exact format:
   {fault} | {runs} runs | Detection: {rate}% | Mitigation: {rate}% | Reasoning: {score}/10 | Response Quality: {score}/10

2. Generate a CATEGORY ANALYSIS (2-4 sentences) that synthesizes:
   - The category's overall performance profile (what it does well, what it doesn't)
   - The most notable pattern or contrast (e.g., "despite 0% detection, 100% mitigation")
   - Any unique characteristics compared to other categories (if known)
   - Connection between the numeric metrics and the LLM Council's qualitative assessment

RULES:
- The detail line is a strict format - use the exact numbers from the context
- The analysis should NOT repeat what the individual assessment blocks say verbatim
- Instead, it should synthesize ACROSS the 4 assessments + metrics
- Reference specific numbers from the context
- Keep it concise: 2-4 sentences, professional tone

FORMAT:
Return JSON:
{
  "detail": "<pipe-delimited detail line>",
  "analysis": "<2-4 sentence synthesis paragraph>"
}
```

---

## Expected Output (per category)

```json
{
  "Application": {
    "title": "Application Faults",
    "detail": "container-kill | 5 runs | Detection: 20% | Mitigation: 100% | Reasoning: 8.42/10 | Response Quality: 8.42/10",
    "analysis": "The agent demonstrated strong diagnostic depth and reasoning quality (8.42/10) when handling container-kill faults, with the LLM Council unanimously rating all qualitative dimensions as Strong. However, its proactive fault detection remains weak at only 20%, suggesting the agent excels at responding to known faults but struggles to identify them before symptoms escalate. This is the only category with measurable action correctness (1.0), indicating that when faults are engaged, remediation actions are consistently correct."
  },
  "Network": {
    "title": "Network Faults",
    "detail": "pod-dns-error | 5 runs | Detection: 0% | Mitigation: 100% | Reasoning: 8.43/10 | Response Quality: 8.43/10",
    "analysis": "Network faults represent the most striking gap between detection and remediation: the agent failed to explicitly detect any of the 5 pod-dns-error injections (0% detection rate, 100% false negatives) yet achieved 100% mitigation, suggesting it acts on symptoms rather than root causes. Despite this, reasoning quality scored 8.43/10 with unanimous Strong ratings, indicating the agent's diagnostic narratives are well-structured even when formal detection signals are absent. Action correctness was not instrumented for this category."
  },
  "Resource": {
    "title": "Resource Faults",
    "detail": "disk-fill | 5 runs | Detection: 20% | Mitigation: 100% | Reasoning: 8.6/10 | Response Quality: 8.6/10",
    "analysis": "Resource faults showed the highest reasoning scores (8.60/10) but the slowest response times, with median TTD of 1,367s and TTM of 1,602s - roughly 3x slower than application faults. This is also the only category where hallucination was detected (1 run scored 0.07), though the LLM Council still rated all dimensions as Strong. The extended detection latency combined with low detection rate (20%) suggests disk-fill scenarios present the most challenging diagnostic environment for the agent."
  }
}
```

---

## Pydantic Schema

```python
from pydantic import BaseModel, Field
from typing import Literal
from schema.certification_schema import HeadingBlock, TextBlock


class FaultCategoryAnalysis(BaseModel):
    """Raw LLM output for a single fault category."""
    title:    str = Field(..., min_length=1)
    detail:   str = Field(..., min_length=1)
    analysis: str = Field(..., min_length=1)

    def to_heading_block(self) -> HeadingBlock:
        """Convert to certified HeadingBlock for Section 10 content."""
        return HeadingBlock(type="heading", title=self.title, detail=self.detail)

    def to_analysis_block(self) -> TextBlock:
        """Convert to certified TextBlock for Section 10 content."""
        return TextBlock(type="text", body=self.analysis)


class FaultCategoryAnalysisResult(BaseModel):
    """Envelope model for Call 4 output."""
    categories:  dict[str, FaultCategoryAnalysis]
    source:      Literal["llm", "fallback"]
    model:       str | None = None
    tokens_used: int = Field(default=0, ge=0)

    def to_content_blocks(self) -> list[HeadingBlock | TextBlock]:
        """Returns alternating HeadingBlock + TextBlock for Section 10."""
        blocks = []
        for cat in self.categories.values():
            blocks.append(cat.to_heading_block())
            blocks.append(cat.to_analysis_block())
        return blocks
```

### Certified Schema Mapping

| Phase 3D Output | Target | Certified Type | Converter |
|---|---|---|---|
| `FaultCategoryAnalysis.title` | `sections[9].content[N]` | `HeadingBlock.title` | `.to_heading_block()` |
| `FaultCategoryAnalysis.detail` | `sections[9].content[N]` | `HeadingBlock.detail` (pipe-delimited) | `.to_heading_block()` |
| `FaultCategoryAnalysis.analysis` | `sections[9].content[N+1]` | `TextBlock.body` (2-4 sentences) | `.to_analysis_block()` |

---

## Formatting Rules for Detail Line

| Field | Rule |
|-------|------|
| Detection / mitigation rates | Multiply by 100, show as integer percent (e.g., `20%`, `0%`, `100%`) |
| Reasoning / response quality | Show raw float (e.g., `8.42`, `8.6`), append `/10` |
| Fault name | Use exact string from `faults_tested[0]` (e.g., `container-kill`, not `Container Kill`) |

---

## Two-Level Validation

```
LLM Response (JSON string)
    |
    v
Level 1: json.loads() -> FaultCategoryAnalysisResult.model_validate(data)
         Catches: missing categories, wrong types, empty title/detail/analysis
    |
    v
Level 2: .to_content_blocks() -> each HeadingBlock / TextBlock validated
         Catches: heading title too short, body too short
    |
    v
If both pass -> store in Phase3Output.fault_category_analysis
If either fails -> retry (up to 3x) -> fallback
```

---

## Validation Rules

| Rule | Check |
|------|-------|
| Category count | Must produce one entry per category (3 in current data) |
| Detail format | Each `detail` line must follow the pipe-delimited format exactly |
| Analysis length | Each `analysis` must be 2-4 sentences |
| Number accuracy | Analysis must reference at least 1 specific number from the category's metrics |
| No verbatim copy | Analysis must NOT copy assessment block text verbatim (>50% overlap = fail) |
| Detail accuracy | Detail line numbers must match Phase 1 source values exactly |

---

## Fallback (if LLM fails)

- **Detail line**: deterministic string format from Phase 1 numeric data (cannot fail)
- **Analysis**: template-based:
  > "The {category} category tested {fault} across {runs} runs with a detection rate of {rate}% and mitigation rate of {rate}%. The LLM Council rated all qualitative dimensions as {rating} with {confidence} confidence."

---

## Module Layout

```
engine/phase3/phase3d/
├── __init__.py                       # re-exports build_fault_analysis()
├── fault_analysis_builder.py         # prompt assembly, LLM call, validation, fallback
└── docs/
    └── fault_analysis_requirements.md   # This document
```

### Entry Point

```python
def build_fault_analysis(phase1: dict, phase2: dict, llm_client) -> dict:
    """
    Returns:
        {
            "fault_category_analysis": {
                "categories": {
                    "Application": {"title": "...", "detail": "...", "analysis": "..."},
                    "Network": {"title": "...", "detail": "...", "analysis": "..."},
                    "Resource": {"title": "...", "detail": "...", "analysis": "..."}
                },
                "source": "llm" | "fallback",
                "model": "gpt-4o",
                "tokens_used": 567
            }
        }
    """
```

---

## Notebook Validation (Cell 6)

```
Cell 6:  Run LLM Call 4 (Fault Category Analysis)
         - Display heading detail lines per category
         - Display per-category analysis paragraphs
         - Cross-check detail line values against Phase 1 numeric data
         - Check: analysis paragraphs are 2-4 sentences?
         - Check: analysis references at least 1 specific number?
         - Check: analysis does NOT copy assessment block text verbatim?
```
