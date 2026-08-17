# metrics-agent: standalone Grafana Alloy scraper (ADR-010 Option B).
#
# Deliberately separate from backend/Dockerfile — this keeps the
# digest-pinned, pip-stripped backend runtime image (CR-2026-002/#3048,
# docker-image-scan/Trivy gate) untouched by this new third-party binary.
# This image gets its own pin/refresh discipline instead of piggybacking on
# the backend's.
#
# Pin: Grafana's official Alloy image, by tag only for now. This session has
# no registry access to pull `grafana/alloy:v1.4.3`'s real manifest digest,
# so a digest pin is NOT fabricated here (a made-up sha256 would silently
# fail every build). TODO (human, before first deploy): resolve and add the
# real `@sha256:...` digest — `docker pull grafana/alloy:v1.4.3 && docker
# inspect --format='{{index .RepoDigests 0}}' grafana/alloy:v1.4.3` — then
# follow the same quarterly refresh cadence as backend/Dockerfile
# (docs/runbooks/docker-image-pinning.md, which should be extended to cover
# this image too as a follow-up, not done in this PR).
FROM grafana/alloy:v1.4.3 AS alloy

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
