# orchid-api — FastAPI + LangGraph agent backend
#
# Build context: orchid-api/
#   docker build -t orchid-api .
#
# Multi-stage: install deps → slim runtime

# ── Stage 1: build & install ───────────────────────────────────
FROM python:3.13-slim AS builder

WORKDIR /app

# System deps for building wheels
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

# Install orchid-api (pulls orchid from PyPI as a dependency)
COPY pyproject.toml pyproject.toml
COPY orchid_api orchid_api
RUN pip install --no-cache-dir --prefix=/install .

# ── Stage 2: runtime ──────────────────────────────────────────
FROM python:3.13-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY orchid_api orchid_api

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "orchid_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
