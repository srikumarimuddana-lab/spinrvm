# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.12.9-slim AS builder

WORKDIR /build

# Install compilation dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create a virtual environment and add it to PATH
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
# Install packages directly into the venv
RUN pip install --no-cache-dir -r requirements.txt


# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12.9-slim AS runtime

# Install required RUNTIME system libraries (matching the builder stage)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# ── Security: run as non-root ──────────────────────────────────────────────────
RUN groupadd --gid 1001 spinr \
    && useradd --uid 1001 --gid spinr --shell /bin/bash --create-home spinr

WORKDIR /app

# Copy the completely self-contained virtual environment from the builder
COPY --from=builder --chown=spinr:spinr /opt/venv /opt/venv

# Copy application source
COPY --chown=spinr:spinr . .

# Ensure the app uses the virtual environment
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

USER spinr

EXPOSE 8000

# ── Healthcheck ────────────────────────────────────────────────────────────────
# IMPORTANT: Ensure 'server:app' actually has a GET /health route handling requests!
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/health')" || exit 1

# Start the server directly using the venv's uvicorn binary
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
