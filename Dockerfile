# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.12.9-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12.9-slim AS runtime

RUN groupadd --gid 1001 spinr \
    && useradd --uid 1001 --gid spinr --shell /bin/bash --create-home spinr

WORKDIR /app

COPY --from=builder /install /usr/local

# Copy backend source into /app so `uvicorn server:app` works
COPY --chown=spinr:spinr backend/ .

# Make sure server.py is importable regardless of cwd the runtime uses
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

USER spinr

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/health')" || exit 1

CMD ["sh", "-c", "cd /app && python -m uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
