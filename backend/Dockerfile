# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.12.9-slim AS builder

WORKDIR /build

# Apply OS security patches before installing anything else.
# This ensures the builder layer starts from a patched base, reducing
# the surface area reported by Trivy / Grype image scans.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12.9-slim AS runtime

# Apply OS security patches to the runtime image as well.
# The builder's patched packages don't transfer to the runtime stage
# (only /install is copied), so the runtime layer must be patched independently.
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

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

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${UVICORN_WORKERS:-4}"]
