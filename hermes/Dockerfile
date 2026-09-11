FROM python:3.12-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime ───────────────────────────────────────────────────
FROM python:3.12-slim

RUN groupadd -g 1001 hermes && \
    useradd -u 1001 -g hermes -s /bin/false hermes

COPY --from=builder /install /usr/local

WORKDIR /app
COPY app/ ./app/

RUN python -c "import fastapi; import uvicorn; import httpx; print('deps ok')"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8585

EXPOSE 8585

USER hermes

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen(f'http://localhost:{__import__(\"os\").environ.get(\"PORT\",\"8585\")}/health')"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8585} --workers 2"]
