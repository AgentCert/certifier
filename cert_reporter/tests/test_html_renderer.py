"""Unit tests for cert_reporter.pipeline.html_renderer.

The pure Jinja filters and helper functions are tested directly. The
full node render uses the real templates that ship with the package
(deterministic, no network) and writes to tmp_path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from markupsafe import Markup

import cert_reporter.pipeline.html_renderer as hr
from cert_reporter.pipeline.html_renderer import (
    _block_has_template,
    _cert_class,
    _effective_sections,
    _fmt_num,
    _group_fault_blocks,
    _make_doc_id,
    _md,
    _md_inline,
    _resolve_block_type,
    _score_class,
    _severity_class,
    _status_class,
    _strip_md,
    _tag_class,
    html_renderer_node,
)


class TestScoreClass:
    @pytest.mark.parametrize(
        "score,expected",
        [(95, "excellent"), (90, "excellent"), (80, "good"), (75, "good"),
         (65, "adequate"), (60, "adequate"), (10, "poor")],
    )
    def test_thresholds(self, score, expected):
        assert _score_class(score) == expected

    def test_non_numeric_returns_empty(self):
        assert _score_class("n/a") == ""
        assert _score_class(None) == ""


class TestCertClass:
    @pytest.mark.parametrize(
        "level,expected",
        [("gold", "cert-gold"), ("Platinum", "cert-gold"), ("silver", "cert-silver"),
         ("bronze", "cert-bronze"), ("not certified", "cert-none"),
         ("failed", "cert-none"), ("whatever", "cert-none")],
    )
    def test_map(self, level, expected):
        assert _cert_class(level) == expected


class TestFmtNum:
    def test_none(self):
        assert _fmt_num(None) == "—"

    def test_integer_thousands(self):
        assert _fmt_num(12000) == "12,000"

    def test_float_trims_trailing_zeros(self):
        assert _fmt_num(1.5000) == "1.5"

    def test_non_numeric_string(self):
        assert _fmt_num("abc") == "abc"


class TestStatusClass:
    @pytest.mark.parametrize(
        "status,expected",
        [("PASS", "status-pass"), ("success", "status-pass"),
         ("WARN", "status-warn"), ("partial", "status-warn"),
         ("FAIL", "status-fail"), ("critical", "status-fail"),
         ("mystery", "")],
    )
    def test_map(self, status, expected):
        assert _status_class(status) == expected


class TestTagClass:
    def test_excellent(self):
        assert _tag_class("PASS") == "tag-excellent"
        assert _tag_class("gold") == "tag-excellent"

    def test_good(self):
        assert _tag_class("SILVER") == "tag-good"

    def test_warn(self):
        assert _tag_class("bronze") == "tag-warn"
        assert _tag_class("UNKNOWN") == "tag-warn"

    def test_bad(self):
        assert _tag_class("FAILED") == "tag-bad"

    def test_unknown_value(self):
        assert _tag_class("xyz") == ""


class TestSeverityClass:
    def test_concern(self):
        assert _severity_class("critical") == "finding-concern"

    def test_good(self):
        assert _severity_class("pass") == "finding-good"

    def test_default_note(self):
        assert _severity_class("info") == "finding-note"


class TestMarkdownFilters:
    def test_md_empty(self):
        assert _md("") == ""

    def test_md_bold_italic_code(self):
        out = str(_md("**b** *i* `c`"))
        assert "<strong>b</strong>" in out
        assert "<em>i</em>" in out
        assert "<code>c</code>" in out
        assert out.startswith("<p>") and out.endswith("</p>")

    def test_md_escapes_html(self):
        out = str(_md("<script>"))
        assert "&lt;script&gt;" in out

    def test_md_paragraph_and_linebreaks(self):
        out = str(_md("a\n\nb\nc"))
        assert "</p><p>" in out
        assert "<br>" in out

    def test_md_inline_no_paragraph(self):
        out = str(_md_inline("**x**"))
        assert out == "<strong>x</strong>"
        assert "<p>" not in out

    def test_md_inline_empty(self):
        assert _md_inline("") == Markup("")

    def test_strip_md(self):
        assert _strip_md("**bold** *x*") == "bold x"


class TestBlockTypeResolution:
    def test_alias_applied(self):
        assert _resolve_block_type("scope_metrics") == "scope_stats"

    def test_no_alias_passthrough(self):
        assert _resolve_block_type("text") == "text"

    def test_empty(self):
        assert _resolve_block_type("") == ""

    def test_block_has_template_known(self):
        # `text` partial ships with the package
        assert _block_has_template("text") is True

    def test_block_has_template_unknown(self):
        assert _block_has_template("does_not_exist") is False

    def test_block_has_template_empty(self):
        assert _block_has_template("") is False


class TestGroupFaultBlocks:
    def test_heading_followed_by_assessments_groups(self):
        content = [
            {"type": "heading", "title": "Cat A", "detail": "d"},
            {"type": "assessment", "x": 1},
            {"type": "assessment", "x": 2},
            {"type": "text", "text": "z"},
        ]
        out = _group_fault_blocks(content)
        assert out[0]["type"] == "fault_group"
        assert out[0]["title"] == "Cat A"
        assert len(out[0]["assessments"]) == 2
        assert out[1]["type"] == "text"

    def test_lone_heading_passes_through(self):
        content = [{"type": "heading", "title": "H"}, {"type": "text"}]
        out = _group_fault_blocks(content)
        assert out[0]["type"] == "heading"

    def test_empty(self):
        assert _group_fault_blocks([]) == []


class TestEffectiveSections:
    def test_raw_sections_when_no_enrichment(self):
        state = {
            "sections": [{"id": "s", "content": [{"type": "text"}]}],
            "enriched_sections": {},
        }
        out = _effective_sections(state)
        assert out[0]["id"] == "s"

    def test_uses_enriched_when_present(self):
        state = {
            "sections": [{"id": "s", "content": []}],
            "enriched_sections": {"s": {"id": "s", "content": [], "extra": "enriched"}},
        }
        out = _effective_sections(state)
        assert out[0]["extra"] == "enriched"


class TestMakeDocId:
    def test_uses_agent_name_date_and_run_id(self):
        meta = {
            "meta": {
                "agent_name": "Flash Agent",
                "certification_date": "2026-08-13",
                "certification_run_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            }
        }
        assert _make_doc_id(meta) == "flash_agent_2026-08-13_a1b2c3d4"

    def test_agent_name_and_date_only(self):
        meta = {"meta": {"agent_name": "Flash Agent", "certification_date": "2026-08-13"}}
        assert _make_doc_id(meta) == "flash_agent_2026-08-13"

    def test_agent_name_only(self):
        assert _make_doc_id({"meta": {"agent_name": "Flash Agent"}}) == "flash_agent"

    def test_falls_back_to_run_id_without_agent_name(self):
        assert _make_doc_id({"meta": {"certification_run_id": "RUN 1"}}) == "cert-run_1"

    def test_falls_back_to_agent_id_and_date_without_agent_name(self):
        meta = {"meta": {"agent_id": "a1", "certification_date": "2026-01-01"}}
        assert _make_doc_id(meta) == "cert-a1-2026-01-01"

    def test_falls_back_to_agent_id_only(self):
        assert _make_doc_id({"meta": {"agent_id": "a1"}}) == "cert-a1"

    def test_fallback_to_cert_report(self):
        assert _make_doc_id({"meta": {}}) == "cert-report"


class TestHtmlRendererNode:
    def _state(self, tmp_path, **over):
        st = {
            "formats": ["html"],
            "output_dir": str(tmp_path),
            "meta": {"agent_name": "TestAgent", "certification_run_id": "r1",
                     "agent_id": "a1", "certification_date": "2026-01-01"},
            "header": {"findings": [{"severity": "concern", "text": "issue!"}]},
            "sections": [{"id": "s1", "title": "Sec One", "content": [
                {"type": "text", "text": "Hello **world**"},
            ]}],
            "chart_results": {},
            "footer": "my footer",
        }
        st.update(over)
        return st

    def test_skips_when_no_html_or_pdf_format(self, tmp_path):
        state = self._state(tmp_path, formats=["json"])
        out = html_renderer_node(state)
        assert "html_path" not in out

    def test_renders_real_html_file(self, tmp_path):
        out = html_renderer_node(self._state(tmp_path))
        assert out.get("errors", []) == []
        html_path = out["html_path"]
        assert Path(html_path).exists()
        content = Path(html_path).read_text(encoding="utf-8")
        assert "TestAgent" in content
        assert "my footer" in content
        assert "issue!" in content
        # doc id derived from run id
        assert Path(html_path).name == "cert-r1.html"

    def test_render_failure_records_error(self, tmp_path, monkeypatch):
        def boom():
            raise RuntimeError("jinja kaput")

        monkeypatch.setattr(hr, "_get_jinja_env", boom)
        out = html_renderer_node(self._state(tmp_path))
        assert any("HTML render failed" in e for e in out["errors"])
