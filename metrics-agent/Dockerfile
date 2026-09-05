# metrics-agent: standalone Grafana Alloy scraper (ADR-010 Option B).
#
# Deliberately separate from backend/Dockerfile — this keeps the
# digest-pinned, pip-stripped backend runtime image (CR-2026-002/#3048,
# docker-image-scan/Trivy gate) untouched by this new third-party binary.
# This image gets its own pin/refresh discipline instead of piggybacking on
# the backend's.
#
# Pin: Grafana's official Alloy image, by content-hash digest — same
# convention as backend/Dockerfile's python:3.12.13-slim@sha256:... pin.
# Digest resolved 2026-09-05 via Docker Hub's registry API against the
# grafana/alloy:v1.4.3 manifest LIST (multi-arch; linux/amd64 is what Fly
# machines run) — `curl` against registry-1.docker.io, not a fabricated
# value. Follow the same quarterly refresh cadence as backend/Dockerfile
# once docs/runbooks/docker-image-pinning.md is extended to cover this
# image too (not done here).
FROM grafana/alloy:v1.4.3@sha256:06bdcbb51fc22b16c2e96b31cbdf9cb0972e3bdedfbac2cc55ed36743f7fb9aa AS alloy

# `dig` (bind-tools) is needed by discover-targets.sh to resolve Fly's
# per-machine DNS records (see README.md, "Fly .internal DNS problem").
# grafana/alloy ships on a minimal base; install just what's needed.
USER root
RUN apk add --no-cache bind-tools

COPY config.alloy /etc/alloy/config.alloy
COPY discover-targets.sh /usr/local/bin/discover-targets.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/discover-targets.sh /usr/local/bin/entrypoint.sh \
    && mkdir -p /etc/alloy \
    && touch /etc/alloy/targets.json \
    && chown -R alloy:alloy /etc/alloy 2>/dev/null || true

USER alloy

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
