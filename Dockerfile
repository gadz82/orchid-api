# orchid-api — FastAPI + LangGraph agent backend
#
# Build context: repository root (../)
#   docker build -f orchid-api/Dockerfile -t orchid-api .
#
# Multi-stage: install deps → slim runtime

# ── Stage 1: build & install ───────────────────────────────────
FROM python:3.13-slim AS builder

WORKDIR /app

# System deps for building wheels
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

# Install orchid library first (better layer caching)
COPY orchid/pyproject.toml orchid/pyproject.toml
COPY orchid/orchid orchid/orchid
RUN pip install --no-cache-dir --prefix=/install ./orchid[all-storage]

# Install orchid-api
COPY orchid-api/pyproject.toml orchid-api/pyproject.toml
COPY orchid-api/orchid_api orchid-api/orchid_api
RUN pip install --no-cache-dir --prefix=/install ./orchid-api

# ── Stage 2: runtime ──────────────────────────────────────────
FROM python:3.13-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy consumer projects + examples (needed for runtime class resolution)
COPY docebo docebo
COPY examples examples

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "orchid_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
