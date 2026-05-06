"""FastAPI application factory for cert-reporter."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import router

_ROOT = Path(__file__).resolve().parent.parent
_log = logging.getLogger(__name__)


def _load_env() -> None:
    """Load .env from the project root if present."""
    env_file = _ROOT / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(dotenv_path=env_file, override=False)
            _log.info(".env loaded from %s", env_file)
        except ImportError:
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Optionally connect to MongoDB + GridFS when MONGODB_CONNECTION_STRING is set.

    When running locally without MongoDB, ``app.state.gridfs_bucket`` is ``None``
    and routes fall back to serving files from the filesystem workspace.
    """
    mongo_uri = os.getenv("MONGODB_CONNECTION_STRING")
    _motor_client = None

    if mongo_uri:
        try:
            from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket
            _motor_client = AsyncIOMotorClient(mongo_uri)
            db_name = os.getenv("MONGODB_DATABASE", "agentcert")
            db = _motor_client[db_name]
            app.state.gridfs_bucket = AsyncIOMotorGridFSBucket(db, bucket_name="cert_reports")
            _log.info("cert-reporter: GridFS bucket initialised (db=%s)", db_name)
        except Exception as exc:
            _log.warning("cert-reporter: GridFS init failed (%s); falling back to filesystem", exc)
            app.state.gridfs_bucket = None
    else:
        app.state.gridfs_bucket = None

    yield

    if _motor_client is not None:
        _motor_client.close()


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
        lifespan=_lifespan,
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
