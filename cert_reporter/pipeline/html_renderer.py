"""html_renderer_node — assembles the HTML report using Jinja2 templates.

Updated for the canonical certification framework format:
  meta / header / sections (content blocks) / footer
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .parameters import ChartResult, GraphState

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
STATIC_DIR = Path(__file__).parent.parent / "static"


def _get_jinja_env():
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # Custom filters
    env.filters["score_class"] = _score_class
    env.filters["cert_class"] = _cert_class
    env.filters["fmt_num"] = _fmt_num
    env.filters["status_class"] = _status_class
    env.filters["severity_class"] = _severity_class
    env.filters["tag_class"] = _tag_class
    env.filters["replace_underscore"] = lambda s: str(s).replace("_", " ").title()
    env.filters["md"] = _md
    return env


def _score_class(score) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return ""
    if s >= 90:
        return "excellent"
    if s >= 75:
        return "good"
    if s >= 60:
        return "adequate"
    return "poor"


def _cert_class(level: str) -> str:
    _MAP = {
        "gold":        "cert-gold",
        "silver":      "cert-silver",
        "bronze":      "cert-bronze",
        "platinum":    "cert-gold",
        "not certified": "cert-none",
        "none":        "cert-none",
        "failed":      "cert-none",
    }
    return _MAP.get(str(level).strip().lower(), "cert-none")


def _fmt_num(value) -> str:
    if value is None:
        return "—"
    try:
        f = float(value)
        if f == int(f):
            return f"{int(f):,}"
        return f"{f:,.3f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def _status_class(status: str) -> str:
    s = str(status).strip().upper()
    if s.startswith(("PASS", "OK", "SUCCESS", "COMPLIANT", "VALID", "PERFECT", "CLEAN", "STRONG")):
        return "status-pass"
    if s.startswith(("WARN", "CAUTION", "REVIEW", "PARTIAL", "ADVISORY", "MODERATE", "MINOR")):
        return "status-warn"
    if s.startswith(("FAIL", "ERROR", "CRITICAL", "INVALID", "REJECT", "NOT CERT", "SIGNIFICANT")):
        return "status-fail"
    return ""


def _tag_class(value: str) -> str:
    """Return a CSS tag class for status/rating values in table cells."""
    s = str(value).strip().upper()
    if s in ("PASS", "OK", "SUCCESS", "EXCELLENT", "COMPLIANT", "VALID",
             "PERFECT", "CLEAN", "STRONG", "GOLD"):
        return "tag-excellent"
    if s in ("GOOD", "SILVER"):
        return "tag-good"
    if s in ("WARN", "WARNING", "CAUTION", "REVIEW", "PARTIAL", "ADVISORY",
             "MODERATE", "MINOR", "ADEQUATE", "BRONZE", "NEEDS IMPROVEMENT"):
        return "tag-warn"
    if s in ("FAIL", "FAILED", "ERROR", "CRITICAL", "INVALID", "REJECT",
             "NOT CERTIFIED", "WEAK", "BAD", "POOR", "SIGNIFICANT"):
        return "tag-bad"
    return ""


def _md(text: str) -> str:
    """Convert a small subset of markdown to safe HTML.

    Handles: **bold**, *italic*, `code`, blank-line paragraphs, and
    line-break preservation. Input is plain text (not yet HTML-escaped),
    so we escape first, then apply markdown transforms.
    """
    import re
    from markupsafe import Markup, escape

    if not text:
        return ""
    # HTML-escape first so angle brackets etc. are safe
    s = str(escape(text))
    # **bold**
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    # *italic* (not preceded/followed by another *)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", s)
    # `code`
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    # Blank line → paragraph break
    s = re.sub(r"\n{2,}", "</p><p>", s)
    # Single newline → <br>
    s = s.replace("\n", "<br>")
    return Markup(f"<p>{s}</p>")


def _severity_class(severity: str) -> str:
    s = str(severity).strip().lower()
    if s in ("concern", "critical", "high"):
        return "finding-concern"
    if s in ("good", "pass", "success"):
        return "finding-good"
    return "finding-note"


def _read_css() -> str:
    css_path = STATIC_DIR / "report.css"
    if css_path.exists():
        return css_path.read_text(encoding="utf-8")
    return ""


def _group_fault_blocks(content: list) -> list:
    """Merge heading + consecutive assessment blocks into a single fault_group block.

    This mirrors the design layout where one fault-card per category wraps all
    its sub-assessments as nested fault-block items, rather than rendering each
    assessment as a separate card.

    Any heading NOT followed by assessments is left as a plain heading block.
    All other block types pass through unchanged.
    """
    result: list = []
    i = 0
    while i < len(content):
        block = content[i]
        btype = block.get("type", "")
        if btype == "heading":
            j = i + 1
            assessments: list = []
            while j < len(content) and content[j].get("type") == "assessment":
                assessments.append(content[j])
                j += 1
            if assessments:
                result.append({
                    "type": "fault_group",
                    "title": block.get("title", ""),
                    "detail": block.get("detail"),
                    "assessments": assessments,
                })
                i = j
                continue
        result.append(block)
        i += 1
    return result


def _effective_sections(state: GraphState) -> list[dict[str, Any]]:
    """Return enriched sections if available, otherwise raw sections."""
    enriched = state.get("enriched_sections", {})
    sections = state.get("sections", [])
    if not enriched:
        result = []
        for section in sections:
            sec = dict(section)
            sec["content"] = _group_fault_blocks(sec.get("content") or [])
            result.append(sec)
        return result
    result = []
    for section in sections:
        sid = section.get("id", "")
        sec = dict(enriched.get(sid, section))
        sec["content"] = _group_fault_blocks(sec.get("content") or [])
        result.append(sec)
    return result


def _make_doc_id(state: GraphState) -> str:
    """Build a document ID from meta fields (filesystem-safe)."""
    import re
    meta = state.get("meta", {})
    run_id = meta.get("certification_run_id", "")
    if run_id:
        raw = f"cert-{run_id}"
    else:
        agent_id = meta.get("agent_id", "")
        date = meta.get("certification_date", "")
        if agent_id:
            raw = f"cert-{agent_id}-{date}" if date else f"cert-{agent_id}"
        else:
            raw = "cert-report"
    # Sanitise for filesystem: replace spaces and special chars
    return re.sub(r"[^a-zA-Z0-9._-]", "_", raw).strip("_")


def html_renderer_node(state: GraphState) -> GraphState:
    """Render the complete HTML report and write it to the output directory."""
    if "html" not in state.get("formats", []) and "pdf" not in state.get("formats", []):
        return state

    verbose = state.get("verbose", False)
    if verbose:
        log.info("html_renderer_node: assembling HTML report")

    output_dir = Path(state.get("output_dir", "."))
    output_dir.mkdir(parents=True, exist_ok=True)

    doc_id = _make_doc_id(state)
    html_path = output_dir / f"{doc_id}.html"

    try:
        env = _get_jinja_env()
        template = env.get_template("base.html")

        context = {
            "meta": state.get("meta", {}),
            "header": state.get("header", {}),
            "sections": _effective_sections(state),
            "charts": state.get("chart_results", {}),
            "footer": state.get("footer", ""),
            "css": _read_css(),
            "token_usage": state.get("token_usage"),
        }

        html_content = template.render(**context)
        html_path.write_text(html_content, encoding="utf-8")

        if verbose:
            log.info("html_renderer_node: HTML written to %s", html_path)

        return {**state, "html_path": str(html_path)}
    except Exception as exc:
        log.error("html_renderer_node failed: %s", exc)
        errors = state.get("errors", []) + [f"HTML render failed: {exc}"]
        return {**state, "errors": errors}
