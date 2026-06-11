"""Unit tests for cert_reporter.prompts.enrichment prompt templates."""

from __future__ import annotations

from cert_reporter.prompts import enrichment


class TestSystemPrompt:
    def test_contains_role_and_constraints(self):
        assert "technical writer" in enrichment.SYSTEM_PROMPT
        assert "Return only the improved text" in enrichment.SYSTEM_PROMPT


class TestTemplatesFormat:
    def test_section_intro(self):
        out = enrichment.SECTION_INTRO_PROMPT.format(
            section_title="Overview", text="raw intro"
        )
        assert "Overview" in out
        assert "raw intro" in out

    def test_narrative_block(self):
        out = enrichment.NARRATIVE_BLOCK_PROMPT.format(
            block_title="Reliability", assessment="PASS", text="body"
        )
        assert "Reliability" in out
        assert "PASS" in out
        assert "body" in out

    def test_qualitative_findings(self):
        out = enrichment.QUALITATIVE_FINDINGS_PROMPT.format(
            subsection_title="Safety", text="findings text"
        )
        assert "Safety" in out
        assert "findings text" in out

    def test_exec_summary(self):
        out = enrichment.EXEC_SUMMARY_PROMPT.format(
            agent_name="A", cert_level="Gold", score=92, text="summary body"
        )
        assert "A" in out
        assert "Gold" in out
        assert "92" in out
        assert "summary body" in out
