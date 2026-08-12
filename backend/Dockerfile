# B-P2-8: base image pinned to a specific Python minor version. Previously
# this was `python:3.12-slim` (any 3.12.x), which meant a Docker Hub re-tag
# of `3.12-slim` could change the build environment between runs without
# any visible PR diff.
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
#
# CR-2026-003 (issue #3300): this file used to have a separate "builder"
# stage here — installing to `--prefix=/install` with `gcc`/`libpq-dev`
# present, intended to be copied into the runtime stage below via
# `COPY --from=builder`. That copy was never added; nothing ever consumed
# the builder's output. BuildKit does not build stages nothing depends on,
# so it was skipped on every real build — confirmed against a CI build log
# (no `libpq-dev`/`--prefix=/install` traces, and exactly the two
# `pip install` calls the runtime stage below makes on its own). Removed
# rather than wired up: wiring it up would have added a layer copy for no
# speed gain (the stage was already free) and introduced real risk, since
# `--prefix=/install` does not lay out identically to a normal
# site-packages install.
FROM python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS runtime

# Apply OS security patches to the runtime image.
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

# ── Remove pip from the runtime image (CR-2026-002 / #3048) ───────────────────
# pip ships vendored copies of its own dependencies under pip/_vendor, and its
# vendor.txt declares `msgpack==1.1.2` and `setuptools==70.3.0` — precisely the
# two HIGH findings the Trivy image scan has been reporting for weeks
# (GHSA-6v7p-g79w-8964, CVE-2025-47273). They were repeatedly written off as a
# tool false positive; a CI diagnostic on 2026-08-02 (job 91442197631) proved
# otherwise. The application's own packages are already patched and are NOT the
# source of either finding:
#     pip list  →  msgpack 1.2.1   setuptools 83.0.0
#     on disk   →  msgpack-1.2.1.dist-info, setuptools-83.0.0.dist-info  (only)
# The vulnerable code exists solely inside pip's vendor tree.
#
# Nothing at runtime needs pip: CMD is uvicorn and the healthcheck uses urllib.
# Removing it deletes the vulnerable code instead of suppressing the finding,
# and drops a package manager from a production image — hardening that stands
# on its own merits regardless of these two CVEs.
#
# The guard matters: the paths below are tied to python3.12 from the pinned
# base image. If a future base bump moves them, the rm silently no-ops and the
# CVEs quietly return. Asserting pip is really gone turns that into a loud
# build failure instead.
RUN rm -rf /usr/local/lib/python3.12/site-packages/pip \
           /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
           /usr/local/lib/python3.12/ensurepip \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12 \
    && if python -c "import pip" 2>/dev/null; then \
         echo "ERROR: pip is still importable after removal. The site-packages path has probably moved with the base image — update the paths in this step rather than deleting the check." >&2; \
         exit 1; \
       fi \
    && echo "pip removed from runtime image (CR-2026-002)"

COPY --chown=spinr:spinr . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

USER spinr

EXPOSE 8000

# ── Healthcheck ────────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/health')" || exit 1

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${UVICORN_WORKERS:-4}"]
