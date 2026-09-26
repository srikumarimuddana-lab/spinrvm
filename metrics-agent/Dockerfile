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
# Log store + UI (README "Logs & Grafana"): Loki, Vector and Grafana binaries
# are copied out of their official images into this one. Pinned by exact
# version TAG only — NOT yet by digest, unlike the Alloy pin below: the
# session that added them had no Docker registry access to resolve
# digests. Resolve and add @sha256 pins (same method as the Alloy note)
# before relying on this image long-term.
#   - Loki/Vector binaries are statically linked (Go / distroless-static).
#   - Grafana comes from the -ubuntu variant because its sqlite driver is
#     cgo/glibc-linked; the default Alpine (musl) build won't run on
#     Alloy's Ubuntu base.
FROM grafana/loki:3.4.2 AS loki
FROM timberio/vector:0.45.0-distroless-static AS vector
FROM grafana/grafana:11.6.0-ubuntu AS grafana
# Local metrics store (README "Metrics store"). The -scratch variant is used
# because a scratch image can only run a statically linked binary, which is
# what makes it safe to copy onto this Ubuntu base. Pinned by digest,
# resolved 2026-09-25 from Docker Hub's API for tag v1.152.0-scratch (latest
# stable single-node release at the time).
FROM victoriametrics/victoria-metrics:v1.152.0-scratch@sha256:e3711c384913332699be75965b1f55fc214111301ab8edd138f18caa85e013a9 AS vm

FROM grafana/alloy:v1.4.3@sha256:06bdcbb51fc22b16c2e96b31cbdf9cb0972e3bdedfbac2cc55ed36743f7fb9aa AS alloy

# `dig` is needed by discover-targets.sh to resolve Fly's per-machine DNS
# records (see README.md, "Fly .internal DNS problem"). The original PR
# assumed grafana/alloy shipped on an Alpine base (apk) — it doesn't:
# grafana/alloy's own Dockerfile (github.com/grafana/alloy/Dockerfile) builds
# FROM public.ecr.aws/ubuntu/ubuntu:noble, which uses apt-get. `apk add` fails
# outright on this image ("apk: not found") — confirmed via a real `fly deploy`
# build log, 2026-09-05. `dnsutils` is Ubuntu/Debian's package providing `dig`
# (Alpine's equivalent package is `bind-tools`, which does not exist here).
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends dnsutils util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY config.alloy /etc/alloy/config.alloy
COPY discover-targets.sh /usr/local/bin/discover-targets.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
# Strip any CRLF line endings before chmod/exec. A shell script checked out
# on Windows can get \r\n endings from git's line-ending conversion, which
# corrupts the #!/bin/sh shebang line (the kernel looks for an interpreter
# literally named "/bin/sh\r" and fails with "No such file or directory" --
# a confusing error since the file plainly exists). Confirmed via a real
# `fly deploy` from a Windows machine, 2026-09-05: the image built fine but
# every machine start failed with exactly that error. Normalizing here in
# the build makes this immune to the committer's/checkout's line-ending
# behavior entirely, rather than relying on .gitattributes alone.
RUN sed -i 's/\r$//' /usr/local/bin/discover-targets.sh /usr/local/bin/entrypoint.sh \
    && chmod +x /usr/local/bin/discover-targets.sh /usr/local/bin/entrypoint.sh \
    && mkdir -p /etc/alloy \
    && touch /etc/alloy/targets.json \
    && chown -R alloy:alloy /etc/alloy 2>/dev/null || true

# Grafana is the only process here reachable from outside the machine (via
# `fly proxy`), so it gets its own unprivileged user and can't read the
# Loki/Vector data under /data owned by `alloy`. Created before the COPY so
# the Grafana tree can be chowned to it: the upstream image owns it as UID
# 472, which doesn't exist here, so readability would otherwise depend on
# upstream's permission bits.
RUN useradd --system --no-create-home --shell /usr/sbin/nologin grafana

COPY --from=loki /usr/bin/loki /usr/local/bin/loki
COPY --from=vector /usr/local/bin/vector /usr/local/bin/vector
COPY --from=vm /victoria-metrics-prod /usr/local/bin/victoria-metrics
COPY alloy-fragments /etc/alloy/fragments
COPY --from=grafana --chown=grafana:grafana /usr/share/grafana /usr/share/grafana
COPY loki.yaml /etc/loki/loki.yaml
COPY vector.toml /etc/vector/vector.toml
COPY grafana/provisioning /etc/grafana/provisioning
COPY grafana/provisioning-optional /etc/grafana/provisioning-optional

# The entrypoint starts as root ONLY to chown the /data volume (Fly mounts
# it root-owned) and assemble Grafana's provisioning; every long-running
# process, Alloy included, is then started as an unprivileged user via
# `setpriv` (util-linux). Alloy still runs as `alloy`, exactly as before.
USER root

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
