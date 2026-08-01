# B-P2-8: builder stage pinned to a specific Python minor version, matching
# the runtime stage below. Previously this was `python:3.12-slim` (any 3.12.x),
# which meant a Docker Hub re-tag of `3.12-slim` could change the build
# environment between runs without any visible PR diff.
#
# B-P2-8 half 2 — content-hash pin: the `@sha256:` suffix makes the base
# image bit-for-bit reproducible. Any Docker Hub re-tag or compromised mirror
# must also rotate this digest, leaving an auditable PR diff. Digest captured
# 2026-08-01 from the manifest list (all platforms; Docker resolves to the
# correct arch). ACTION_ITEMS.md C6 (2026-08-01 refresh): the prior pin
# (python:3.12.9-slim, digest sha256:48a11b7...) was 94 days past the
# quarterly cadence AND, verified directly against the registry, that
# tag's digest had not moved at all since capture — Docker Hub does not
# rebuild a fixed patch-version tag once newer patches ship, so re-pinning
# the same tag would not have closed any CVE gap. Bumped to the current
# latest 3.12.x patch (3.12.13-slim) instead, cross-verified against the
# floating `python:3.12-slim` alias resolving to the same digest.
# Refresh quarterly or on Trivy/Grype alert:
# see `docs/runbooks/docker-image-pinning.md`.
FROM python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

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
FROM python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS runtime

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
