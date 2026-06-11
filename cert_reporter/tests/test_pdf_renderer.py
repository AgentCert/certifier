"""Unit tests for cert_reporter.pipeline.pdf_renderer.

Playwright is never invoked: the threaded render is monkeypatched. We test
the orchestration / state-transition logic only.
"""

from __future__ import annotations

from pathlib import Path

import cert_reporter.pipeline.pdf_renderer as pdf
from cert_reporter.pipeline.pdf_renderer import _run_in_thread, pdf_renderer_node


class TestPdfRendererNode:
    def test_skips_when_pdf_not_requested(self):
        state = {"formats": ["html"]}
        assert pdf_renderer_node(state) is state

    def test_missing_html_file_errors(self, tmp_path):
        state = {"formats": ["pdf"], "html_path": str(tmp_path / "missing.html")}
        out = pdf_renderer_node(state)
        assert any("HTML file not found" in e for e in out["errors"])

    def test_empty_html_path_errors(self):
        out = pdf_renderer_node({"formats": ["pdf"], "html_path": ""})
        assert any("HTML file not found" in e for e in out["errors"])

    def test_happy_path_sets_pdf_path(self, tmp_path, monkeypatch):
        html = tmp_path / "report.html"
        html.write_text("<html></html>", encoding="utf-8")

        calls = {}

        def fake_run(html_abs, pdf_path):
            calls["html_abs"] = html_abs
            calls["pdf_path"] = pdf_path
            Path(pdf_path).write_bytes(b"%PDF-1.4")

        monkeypatch.setattr(pdf, "_run_in_thread", fake_run)
        out = pdf_renderer_node({"formats": ["pdf"], "html_path": str(html), "verbose": True})

        expected_pdf = str(html.resolve().with_suffix(".pdf"))
        assert out["pdf_path"] == expected_pdf
        assert calls["pdf_path"] == expected_pdf
        # passed an absolute path to the renderer
        assert calls["html_abs"] == str(html.resolve())

    def test_render_exception_records_error(self, tmp_path, monkeypatch):
        html = tmp_path / "report.html"
        html.write_text("<html></html>", encoding="utf-8")

        def boom(html_abs, pdf_path):
            raise RuntimeError("chromium crashed")

        monkeypatch.setattr(pdf, "_run_in_thread", boom)
        out = pdf_renderer_node({"formats": ["pdf"], "html_path": str(html)})
        assert any("PDF render failed" in e for e in out["errors"])
        assert "chromium crashed" in out["errors"][-1]


class TestRunInThread:
    def test_propagates_worker_exception(self, monkeypatch):
        async def boom(html_path, pdf_path):
            raise ValueError("render boom")

        monkeypatch.setattr(pdf, "_render_pdf", boom)
        try:
            _run_in_thread("a.html", "a.pdf")
        except ValueError as e:
            assert "render boom" in str(e)
        else:
            raise AssertionError("expected ValueError to propagate")

    def test_success_runs_coroutine(self, monkeypatch):
        ran = {}

        async def fake(html_path, pdf_path):
            ran["html"] = html_path
            ran["pdf"] = pdf_path

        monkeypatch.setattr(pdf, "_render_pdf", fake)
        _run_in_thread("in.html", "out.pdf")
        assert ran == {"html": "in.html", "pdf": "out.pdf"}
