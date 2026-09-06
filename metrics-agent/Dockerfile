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
    && apt-get install -y --no-install-recommends dnsutils \
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

USER alloy

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
