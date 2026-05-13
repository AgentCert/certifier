# =============================================================================
# Certifier — Dockerfile
#
# Multi-stage build:
#   builder  – installs Python dependencies into a venv
#   runtime  – copies only the venv + app source; no build toolchain in the
#              final image
#
# Build:
#   docker build -t certifier .
#
# Run (standalone, MongoDB must be reachable via MONGODB_CONNECTION_STRING):
#   docker run --env-file .env -p ${API_PORT:-8000}:${API_PORT:-8000} certifier
# =============================================================================


# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

# System libraries required by some Python packages:
#   portaudio19-dev  → PyAudio
#   libxml2-dev + libxslt-dev → lxml (C extension)
#   build-essential  → generic C/C++ build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        portaudio19-dev \
        libxml2-dev \
        libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

# Create a virtual environment so we can copy it cleanly into the runtime stage
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY requirements.txt .

# Upgrade pip first to avoid resolver quirks with older pip
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Install the chromium browser used by cert_reporter's pdf_renderer (playwright).
# Pin the cache to /opt so the runtime stage can copy it in, and so the
# non-root user in the runtime stage can read it.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN playwright install chromium && chmod -R a+rX /opt/playwright-browsers


# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Runtime system libraries:
#   libportaudio2 / libxml2 / libxslt1.1 → carried over from previous build
#   The chromium libs are required by playwright's bundled browser for PDF
#   rendering (cert_reporter pipeline). Without these, `pw.chromium.launch()`
#   fails with a missing-shared-library error at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libportaudio2 \
        libxml2 \
        libxslt1.1 \
        libnss3 \
        libnspr4 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libdbus-1-3 \
        libxcb1 \
        libxkbcommon0 \
        libx11-6 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2 \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Bring the playwright-downloaded chromium binaries across from the builder
# stage so the runtime image can render PDFs without re-downloading anything.
COPY --from=builder /opt/playwright-browsers /opt/playwright-browsers
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers

# Copy the venv from the builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create a non-root user for security
RUN useradd --create-home --shell /bin/bash agentcert
USER agentcert

WORKDIR /app

# Copy application source.
# .dockerignore excludes __pycache__, .env, data/, workspace/, etc.
COPY --chown=agentcert:agentcert . .

# PYTHONPATH must include /app so that all top-level packages
# (main, utils, fault_analyzer, metrics_extractor, aggregator, cert_builder)
# are importable without a setup.py install.
ENV PYTHONPATH=/app

# Workspace directories are created at runtime by the lifespan handler,
# but we pre-create them here so bind-mounts work correctly on startup.
RUN mkdir -p /app/workspace/cert

# API_PORT controls which port uvicorn listens on.
# Default matches settings.py default; override at runtime with -e API_PORT=<n>.
ARG API_PORT=8000
EXPOSE ${API_PORT}

# Run via the __main__ entrypoint in main/main.py so that uvicorn reads
# host/port from settings.py (which resolves API_HOST / API_PORT from env).
# This avoids hardcoding the port here — exec form with a shell wrapper
# preserves SIGTERM forwarding.
CMD ["python", "-m", "main.main"]
