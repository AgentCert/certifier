"""FastAPI application factory for cert-reporter."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .routes import router

_ROOT = Path(__file__).parent.parent
UI_DIR = _ROOT / "ui"
OUTPUT_DIR = _ROOT / "output"


def _load_env() -> None:
    """Load .env from the project root if present."""
    env_file = _ROOT / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(dotenv_path=env_file, override=False)
            logging.getLogger(__name__).info(".env loaded from %s", env_file)
        except ImportError:
            # Manual fallback parse
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val


def create_app() -> FastAPI:
    _load_env()

    app = FastAPI(
        title="cert-reporter API",
        description=(
            "Converts a structured AgentCert certification JSON document "
            "into polished HTML and PDF reports."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount generated reports as static files
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/reports-static", StaticFiles(directory=str(OUTPUT_DIR)), name="reports-static")

    # Register API routes under /api prefix
    app.include_router(router, prefix="/api")

    # Serve the demo UI at root
    @app.get("/", include_in_schema=False)
    def serve_ui():
        ui_path = UI_DIR / "index.html"
        if ui_path.exists():
            return FileResponse(str(ui_path), media_type="text/html")
        return {"message": "cert-reporter API. See /docs for API reference."}

    return app
