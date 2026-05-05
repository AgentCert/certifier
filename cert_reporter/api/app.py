"""FastAPI application factory for cert-reporter."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import router

_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    """Load .env from the project root if present."""
    env_file = _ROOT / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(dotenv_path=env_file, override=False)
            logging.getLogger(__name__).info(".env loaded from %s", env_file)
        except ImportError:
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
            "Generates HTML and PDF certification reports from AgentCert pipeline output. "
            "GET /api/certification/pdf returns a PDF file. "
            "GET /api/certification/html returns an HTML file."
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

    # Register API routes under /api prefix
    app.include_router(router, prefix="/api")

    return app
