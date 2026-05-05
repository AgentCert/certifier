"""FastAPI routes for the cert-reporter API."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from .models import GenerateRequest, GenerateResponse, HealthResponse, ReportItem

log = logging.getLogger(__name__)

# Project root on sys.path so pipeline imports work
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

OUTPUT_DIR = _ROOT / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(json_content: dict[str, Any], formats: list[str], enrich_llm: bool,
         model: str, provider: str, temperature: float,
         mode: str = "static") -> GenerateResponse:
    """Write JSON to a temp file, run the pipeline, return a GenerateResponse."""
    if mode == "agentic":
        from pipeline.agentic_graph import run_agentic_pipeline as _run_pipeline
    else:
        from pipeline.graph import run_pipeline as _run_pipeline

    # Write input JSON to a temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as fh:
        json.dump(json_content, fh)
        tmp_path = fh.name

    t0 = time.monotonic()
    try:
        state = _run_pipeline(
            input_path=tmp_path,
            output_dir=str(OUTPUT_DIR),
            formats=formats,
            enrich_llm=enrich_llm,
            model=model,
            provider=provider,
            temperature=temperature,
            verbose=False,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    elapsed = time.monotonic() - t0
    meta = state.get("meta", {})
    run_id = meta.get("certification_run_id", "")
    agent_id = meta.get("agent_id", "")
    date = meta.get("certification_date", "")
    if run_id:
        doc_id = f"cert-{run_id}"
    elif agent_id:
        doc_id = f"cert-{agent_id}-{date}" if date else f"cert-{agent_id}"
    else:
        doc_id = "cert-report"

    html_path = state.get("html_path", "")
    pdf_path = state.get("pdf_path", "")

    token_usage = state.get("token_usage")
    tu_dict = None
    if token_usage and hasattr(token_usage, "input_tokens"):
        tu_dict = {
            "input_tokens": token_usage.input_tokens,
            "output_tokens": token_usage.output_tokens,
            "total": token_usage.total,
        }

    return GenerateResponse(
        doc_id=doc_id,
        html_url=f"/api/reports/{Path(html_path).name}" if html_path else None,
        pdf_url=f"/api/reports/{Path(pdf_path).name}" if pdf_path else None,
        errors=state.get("errors", []),
        token_usage=tu_dict,
        duration_seconds=round(elapsed, 2),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse, tags=["meta"])
def health():
    return HealthResponse()


@router.post("/generate", response_model=GenerateResponse, tags=["reports"])
def generate_from_json(req: GenerateRequest):
    """
    Generate HTML and/or PDF report from a certification JSON document body.
    """
    try:
        return _run(
            json_content=req.json_content,
            formats=req.formats,
            enrich_llm=req.enrich_llm,
            model=req.model,
            provider=req.provider,
            temperature=req.temperature,
            mode=req.mode,
        )
    except Exception as exc:
        log.exception("generate_from_json failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/generate/upload", response_model=GenerateResponse, tags=["reports"])
async def generate_from_upload(
    file: UploadFile = File(..., description="Certification JSON file"),
    formats: str = Form(default="html,pdf", description="Comma-separated: html,pdf"),
    enrich_llm: bool = Form(default=False),
    model: str = Form(default="gpt-4.1-mini"),
    provider: str = Form(default="openai"),
    temperature: float = Form(default=0.4),
    mode: str = Form(default="static", description="Pipeline mode: static | agentic"),
):
    """
    Generate report from an uploaded JSON file (multipart/form-data).
    Used by the demo UI.
    """
    raw = await file.read()
    try:
        json_content = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

    fmt_list = [f.strip().lower() for f in formats.split(",") if f.strip()]
    try:
        return _run(
            json_content=json_content,
            formats=fmt_list,
            enrich_llm=enrich_llm,
            model=model,
            provider=provider,
            temperature=temperature,
            mode=mode,
        )
    except Exception as exc:
        log.exception("generate_from_upload failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/reports", response_model=list[ReportItem], tags=["reports"])
def list_reports():
    """List all generated reports in the output directory."""
    items: dict[str, ReportItem] = {}
    for f in sorted(OUTPUT_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        stem = f.stem
        if stem not in items:
            items[stem] = ReportItem(doc_id=stem)
        item = items[stem]
        size_kb = f.stat().st_size / 1024
        if f.suffix == ".html":
            item.html_url = f"/api/reports/{f.name}"
            item.size_kb = round(size_kb, 1)
        elif f.suffix == ".pdf":
            item.pdf_url = f"/api/reports/{f.name}"
    return list(items.values())


@router.get("/reports/{filename}", tags=["reports"])
def serve_report(filename: str):
    """Serve a generated report file (HTML or PDF)."""
    path = OUTPUT_DIR / filename
    if not path.exists() or path.suffix not in {".html", ".pdf"}:
        raise HTTPException(status_code=404, detail="Report not found")
    media = "text/html" if path.suffix == ".html" else "application/pdf"
    return FileResponse(str(path), media_type=media, filename=filename)
