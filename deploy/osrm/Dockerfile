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

ARG OSRM_TAG=v5.27.1

FROM ghcr.io/project-osrm/osrm-backend:${OSRM_TAG} AS build
# Saskatchewan extract from Geofabrik (~40 MB). Override for a wider region.
ARG REGION_URL=https://download.geofabrik.de/north-america/canada/saskatchewan-latest.osm.pbf
WORKDIR /data
# ADD fetches the remote file at build time (follows redirects, no curl needed).
ADD ${REGION_URL} /data/region.osm.pbf
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
