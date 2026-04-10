# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.12.9-slim AS builder

WORKDIR /build

# Install only what pip needs to compile wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12.9-slim AS runtime

# ── Security: run as non-root ──────────────────────────────────────────────────
RUN groupadd --gid 1001 spinr \
    && useradd --uid 1001 --gid spinr --shell /bin/bash --create-home spinr

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source — owned by the non-root user
COPY --chown=spinr:spinr . .

USER spinr

EXPOSE 8000

# ── Healthcheck ────────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/health')" || exit 1

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
