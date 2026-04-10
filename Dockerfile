# Dockerfile
# Multi-stage build — keeps the image lean for Render free tier

FROM python:3.11-slim AS base

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── App source ────────────────────────────────────────────────────────────
COPY webhook/     ./webhook/
COPY llm/         ./llm/
COPY executor/    ./executor/
COPY approvals/   ./approvals/

# ── Runtime config ────────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV WEBHOOK_PORT=8000

EXPOSE 8000

# Default: run the FastAPI webhook server
CMD ["uvicorn", "webhook.main:app", "--host", "0.0.0.0", "--port", "8000"]
