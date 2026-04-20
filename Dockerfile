# =============================================================================
# AgentCert — Dockerfile
#
# Multi-stage build:
#   builder  – installs Python dependencies into a venv
#   runtime  – copies only the venv + app source; no build toolchain in the
#              final image
#
# Build:
#   docker build -t agentcert .
#
# Run (standalone, MongoDB must be reachable via MONGODB_CONNECTION_STRING):
#   docker run --env-file .env -p ${API_PORT:-8000}:${API_PORT:-8000} agentcert
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


# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Only the runtime system libraries are needed here (not -dev variants)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libportaudio2 \
        libxml2 \
        libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

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
