"""pdf_renderer_node — converts the HTML report to PDF via Playwright."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from .parameters import GraphState

log = logging.getLogger(__name__)


async def _render_pdf(html_path: str, pdf_path: str) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"file://{html_path}", wait_until="networkidle")
        # Expand all <details> so every section is visible in the PDF
        await page.evaluate(
            "() => document.querySelectorAll('details').forEach(el => el.setAttribute('open', ''))"
        )
        # Force a synchronous reflow so Chromium recalculates all element heights
        # with sections expanded before computing page break positions.
        await page.evaluate("() => document.body.offsetHeight")
        await page.pdf(
            path=pdf_path,
            format="A4",
            margin={"top": "15mm", "bottom": "15mm", "left": "15mm", "right": "15mm"},
            print_background=True,
            display_header_footer=True,
            header_template=(
                '<div style="font-size:9px; font-family:sans-serif; color:#888; width:100%; '
                'text-align:right; padding-right:15mm;">Agent Certification Report</div>'
            ),
            footer_template=(
                '<div style="font-size:9px; font-family:sans-serif; color:#888; width:100%; '
                'text-align:center;"><span class="pageNumber"></span> / '
                '<span class="totalPages"></span></div>'
            ),
        )
        await browser.close()


def _run_in_thread(html_path: str, pdf_path: str) -> None:
    """Run the async Playwright render in a dedicated thread with its own event loop.

    This avoids the 'asyncio.run() cannot be called from a running event loop'
    error that occurs when pdf_renderer_node is invoked from within FastAPI's
    uvicorn event loop.
    """
    result: list[Exception] = []

    def _worker() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_render_pdf(html_path, pdf_path))
        except Exception as exc:
            result.append(exc)
        finally:
            loop.close()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join()
    if result:
        raise result[0]


def pdf_renderer_node(state: GraphState) -> GraphState:
    """Render HTML → PDF using Playwright headless Chromium."""
    if "pdf" not in state.get("formats", []):
        return state

    html_path = state.get("html_path", "")
    if not html_path or not Path(html_path).exists():
        errors = state.get("errors", []) + ["pdf_renderer_node: HTML file not found"]
        return {**state, "errors": errors}

    # Playwright requires an absolute path for file:// URLs
    html_abs = str(Path(html_path).resolve())
    pdf_path = str(Path(html_abs).with_suffix(".pdf"))
    verbose = state.get("verbose", False)

    if verbose:
        log.info("pdf_renderer_node: rendering %s → %s", html_path, pdf_path)

    try:
        _run_in_thread(html_abs, pdf_path)
        if verbose:
            log.info("pdf_renderer_node: PDF written to %s", pdf_path)
        return {**state, "pdf_path": pdf_path}
    except Exception as exc:
        log.error("pdf_renderer_node failed: %s", exc)
        errors = state.get("errors", []) + [f"PDF render failed: {exc}"]
        return {**state, "errors": errors}
