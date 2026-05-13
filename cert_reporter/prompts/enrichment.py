"""LLM enrichment prompts for the cert-reporter pipeline."""

from __future__ import annotations

SYSTEM_PROMPT = """\
You are a professional technical writer specialising in AI system evaluation and certification reports.
Your task is to refine narrative text extracted from a structured certification document.
Preserve all factual content, scores, and data references exactly.
Improve sentence flow, clarity, and professional tone.
Do not add new claims, opinions, or information not present in the source text.
Return only the improved text — no preamble, no explanation.
"""

SECTION_INTRO_PROMPT = """\
Original section introduction for "{section_title}":
---
{text}
---
Rewrite the above as a polished two-to-four sentence introduction paragraph.
"""

NARRATIVE_BLOCK_PROMPT = """\
Original narrative block titled "{block_title}" (assessment: {assessment}):
---
{text}
---
Rewrite the above for clarity and professional tone. Keep all factual details intact.
"""

QUALITATIVE_FINDINGS_PROMPT = """\
Original qualitative findings sub-section "{subsection_title}":
---
{text}
---
Rewrite the above as a flowing paragraph with strong transitions.
Keep all specific metrics, scores, and named categories unchanged.
"""

EXEC_SUMMARY_PROMPT = """\
Below is the executive summary narrative from an AI certification report.
Agent: {agent_name} | Level: {cert_level} | Score: {score}
---
{text}
---
Rewrite the narrative as a single concise executive paragraph (4-6 sentences)
that highlights the certification outcome, key strengths, and top area for improvement.
"""
