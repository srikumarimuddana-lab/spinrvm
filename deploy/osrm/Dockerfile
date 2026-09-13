# Self-hosted OSRM map-matching server for Spinr billable road distance.
#
# Bakes the Saskatchewan OpenStreetMap extract into the image and preprocesses
# it with the MLD pipeline (partition + customize), which is what /match wants.
# The data is immutable in the image — no Railway volume needed; rebuild to
# refresh the map. The backend reaches this via OSRM_URL and calls
# GET /match/v1/driving/{lng,lat;...} (see backend/utils/route_distance.py).
#
# Build region is overridable:
#   docker build --build-arg REGION_URL=<geofabrik .osm.pbf> -t spinr-osrm .
# Default is the province extract. Use a wider one (e.g. canada-latest) only if
# you actually serve outside SK — it costs build time + RAM.
#
# Multiple provinces: OSRM loads exactly ONE graph per process, so a second
# province is not a second file — the extracts must be merged into one .osm.pbf
# before osrm-extract runs. EXTRA_REGION_URLS does that:
#   docker build \
#     --build-arg EXTRA_REGION_URLS=https://download.geofabrik.de/north-america/canada/alberta-latest.osm.pbf \
#     -t spinr-osrm .
# Merging SK+AB is far cheaper than canada-latest (see README §1).
#
# BILLING NOTE: widening the region changes billable distance for any trip whose
# GPS trace enters the newly-covered area. Today such a trace returns NoMatch and
# the backend falls back to Google Roads, then haversine; once covered, OSRM
# map-matches it and the road-following distance is legitimately *higher* than
# the straight-line fallback. That is the documented billing model working as
# intended, not a regression — but it is a fare change, so roll it out
# deliberately. See docs/change-log/2026-09-13-self-hosted-basemap-and-osrm-regions.md.

ARG OSRM_TAG=v5.27.1

# ── Stage 1: fetch the extract(s), merging when more than one is requested ────
# Deliberately a plain Debian stage rather than the osrm-backend image: the merge
# needs osmium-tool, which the OSRM image does not ship, and installing it here
# keeps it out of both the preprocessing and runtime layers.
FROM debian:bookworm-slim AS fetch
# Saskatchewan extract from Geofabrik (~40 MB). Override for a different region.
ARG REGION_URL=https://download.geofabrik.de/north-america/canada/saskatchewan-latest.osm.pbf
# Space-separated additional .osm.pbf URLs to merge into REGION_URL. Empty by
# default, so the single-region build is byte-for-byte the graph it always was.
ARG EXTRA_REGION_URLS=""
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl osmium-tool \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /data
# `osmium merge` requires inputs sorted by (type, id) — Geofabrik extracts are —
# and writes each object once, so the strip of overlap Geofabrik includes either
# side of a provincial boundary does not produce duplicate ways.
RUN set -eux; \
    count=0; parts=""; \
    for url in ${REGION_URL} ${EXTRA_REGION_URLS}; do \
        count=$((count + 1)); \
        curl -fSL --retry 3 --retry-delay 2 -o "/data/part-${count}.osm.pbf" "$url"; \
        parts="${parts} /data/part-${count}.osm.pbf"; \
    done; \
    if [ "$count" -eq 0 ]; then echo "no REGION_URL given" >&2; exit 1; fi; \
    if [ "$count" -eq 1 ]; then \
        mv /data/part-1.osm.pbf /data/region.osm.pbf; \
    else \
        osmium merge ${parts} -o /data/region.osm.pbf; \
        rm -f ${parts}; \
    fi; \
    ls -lh /data/region.osm.pbf

# ── Stage 2: preprocess into the MLD graph OSRM serves ────────────────────────
FROM ghcr.io/project-osrm/osrm-backend:${OSRM_TAG} AS build
WORKDIR /data
COPY --from=fetch /data/region.osm.pbf /data/region.osm.pbf
# car.lua = the "driving" profile that the /match/v1/driving path selects.
RUN osrm-extract -p /opt/car.lua /data/region.osm.pbf \
 && osrm-partition /data/region.osrm \
 && osrm-customize /data/region.osrm \
 && rm -f /data/region.osm.pbf

FROM ghcr.io/project-osrm/osrm-backend:${OSRM_TAG}
WORKDIR /data
COPY --from=build /data/region.osrm* /data/
# Railway injects PORT; default to 5000 locally.
ENV PORT=5000
EXPOSE 5000
# --algorithm mld MUST match the partition+customize preprocessing above.
# --ip :: binds IPv6 so Railway private networking (*.railway.internal) works;
# it also serves IPv4, so public access is unaffected.
# Raise --max-matching-size if you ever send >100 coords per /match request.
CMD ["sh", "-c", "exec osrm-routed --algorithm mld --ip :: --port ${PORT:-5000} --max-matching-size 100 /data/region.osrm"]
