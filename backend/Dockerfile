# B-P2-8: builder stage pinned to a specific Python minor version, matching
# the runtime stage below. Previously this was `python:3.12-slim` (any 3.12.x),
# which meant a Docker Hub re-tag of `3.12-slim` could change the build
# environment between runs without any visible PR diff.
#
# NEXT STEP — content-hash pin: add `@sha256:<digest>` after the tag to make
# the base image bit-for-bit reproducible. This requires a `docker pull` of
# the current upstream image, which is a manual step performed at the deploy
# host (Railway / local dev). See `docs/runbooks/docker-image-pinning.md`
# for the procedure. The digest should be refreshed quarterly or when a
# Trivy/Grype scan flags the current base.
FROM python:3.12.9-slim

WORKDIR /build

# Apply OS security patches before installing anything else.
# This ensures the builder layer starts from a patched base, reducing
# the surface area reported by Trivy / Grype image scans.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip setuptools

# B-P1-10: install from the SHA-256 hash-pinned lockfile, not the floor-pin
# source-of-truth. `--require-hashes` makes pip refuse any wheel/sdist whose
# digest doesn't match the lockfile, so a compromised PyPI mirror or a
# typosquat package can't slip into the container. Regenerate via
# `cd backend && pip-compile --generate-hashes ... requirements.txt` —
# see docs/runbooks/dependency-update.md.
COPY requirements-locked.txt .
RUN pip install --no-cache-dir --prefix=/install --require-hashes -r requirements-locked.txt


# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12.9-slim AS runtime

# Apply OS security patches to the runtime image as well.
# The builder's patched packages don't transfer to the runtime stage
# (only /install is copied), so the runtime layer must be patched independently.
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip setuptools

# ── Security: run as non-root ──────────────────────────────────────────────────
RUN groupadd --gid 1001 spinr \
    && useradd --uid 1001 --gid spinr --shell /bin/bash --create-home spinr

WORKDIR /app

RUN mkdir -p /app/logs && chmod 777 /app/logs

COPY requirements-locked.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements-locked.txt

COPY --chown=spinr:spinr . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

USER spinr

EXPOSE 8000

# ── Healthcheck ────────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/health')" || exit 1

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${UVICORN_WORKERS:-4}"]
