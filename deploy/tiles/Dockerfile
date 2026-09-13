# Self-hosted basemap tiles for the Spinr admin dashboard.
#
# Replaces the third-party basemap chain (OpenFreeMap → Protomaps → Carto) with
# one service we own. Serves BOTH shapes the dashboard needs:
#
#   /styles/<id>/style.json        → MapLibre vector  (NEXT_PUBLIC_MAP_STYLE_URL)
#   /styles/<id>/{z}/{x}/{y}.png   → rasterised PNG   (NEXT_PUBLIC_RASTER_TILE_URL)
#   /data/v3/{z}/{x}/{y}.pbf       → raw vector tiles
#   /fonts/{fontstack}/{range}.pbf → glyphs
#
# The raster endpoint is why this is tileserver-gl and not a bare PMTiles file
# on object storage: admin-dashboard's no-WebGL fallback renderer
# (static-route-map.tsx) draws plain <img> raster tiles, and nothing that only
# serves vector can feed it. Raster rendering is headless GL — budget more RAM
# and CPU here than the OSRM service next door.
#
# Build once, ship the data in the image (same pattern as deploy/osrm): the
# tiles are immutable, so there is no Railway volume to manage. Rebuild to
# refresh the map.
#
# ⚠️ AUTHORED BUT NOT BUILT. The session that wrote this had no network access
# to Geofabrik, Docker Hub or GitHub, so no layer here has ever been executed.
# Build it locally and run deploy/tiles/smoke-test.sh against it BEFORE pointing
# Railway at it. Every external artifact below is a build arg precisely so you
# can correct a moved URL or a bumped tag without editing this file. README §5
# lists what breaks first and how each failure looks.

# Both image tags are declared here, before the first FROM, because only a
# global ARG is visible to a FROM line. An ARG declared later belongs to the
# stage it sits in, so putting TILESERVER_TAG next to the FROM that uses it
# would expand to an empty tag and fail the build with "invalid reference
# format" before a single layer ran.
ARG PLANETILER_TAG=latest
ARG TILESERVER_TAG=latest

# ── Stage 1: generate OpenMapTiles-schema vector tiles ────────────────────────
# Planetiler downloads the OSM extract itself (--download also fetches the
# Natural Earth basemap and water polygons the schema needs) and writes an
# .mbtiles. A province-sized area is minutes, not hours.
FROM ghcr.io/onthegomap/planetiler:${PLANETILER_TAG} AS tiles

# Geofabrik area name. Planetiler resolves this against its own Geofabrik index,
# so "saskatchewan" is enough — no URL needed. Add Alberta with
#   --build-arg AREA=alberta
# or build a combined region; see README §2.
ARG AREA=saskatchewan
# Planetiler sizes its heap from the container limit, but a build box with lots
# of RAM and a small Docker limit gets this wrong. Pin it explicitly.
ARG JAVA_OPTS="-Xmx4g"
ENV JAVA_TOOL_OPTIONS="${JAVA_OPTS}"
WORKDIR /planetiler
RUN java -jar /planetiler.jar \
      --download \
      --area="${AREA}" \
      --output=/planetiler/tiles.mbtiles \
      --force \
 && ls -lh /planetiler/tiles.mbtiles

# ── Stage 2: fetch the style + glyphs ─────────────────────────────────────────
# Kept separate from the runtime image so curl/jq/unzip never ship to production.
#
# The style is fetched rather than hand-written on purpose: it has to match the
# OpenMapTiles schema layer-for-layer and field-for-field, and a community style
# that is already tested against planetiler's output is far less likely to
# render blank than something written from memory.
FROM debian:bookworm-slim AS assets
# Tarball of a MapLibre GL style repo. Positron is the light, low-chrome look
# admin-dashboard already uses from Carto and OpenFreeMap, so self-hosting is
# not a visual change.
ARG STYLE_TARBALL_URL=https://github.com/openmaptiles/positron-gl-style/archive/refs/heads/master.tar.gz
# Pre-generated PBF glyph ranges for the fonts the style names.
ARG FONTS_ZIP_URL=https://github.com/openmaptiles/fonts/releases/download/v2.0/v2.0.zip
# The data id this style's vector source is rewritten to point at. Must match
# the key under "data" in config.json.
ARG DATA_ID=v3

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl jq unzip \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /assets
# The rewrite filter is a file, not an inline argument: a jq program spans
# several lines, and a line inside a RUN that does not end with a backslash
# ends the instruction — Docker would then read `.sources |= …` as a Dockerfile
# instruction and fail the build.
COPY rewrite-style.jq /rewrite-style.jq

# Fetch the style and rewrite it to reference only local assets.
#
# All explanation lives out here rather than inside the RUN. Docker does strip
# comment lines within a line-continuation block, but if that ever failed the
# joined single-line shell command would treat the first `#` as a shell comment
# and silently discard every remaining step — a build that succeeds having done
# half the work. Not a risk worth taking for comment locality.
#
# What the steps below do:
#  - Most style repos ship a sprite sheet next to style.json. Copying it lets
#    the sprite reference become a relative path tileserver-gl resolves locally.
#    Without one the key is dropped, which is a valid style that simply renders
#    no POI icons — better than leaving a third-party URL in a style whose whole
#    point is having no third parties in it.
#  - Every vector source points at a hosted MapTiler/OpenMapTiles endpoint out
#    of the box; each is rewritten to tileserver-gl's local mbtiles reference.
#    Glyphs become the relative form tileserver-gl expects (it prefixes its own
#    /fonts/ endpoint when serving the style).
#  - The leftover-URL guard checks only the three fields that actually cause a
#    fetch. A blanket grep over the whole file would also trip on a harmless URL
#    in .metadata and fail the build for no reason.
RUN set -eux; \
    mkdir -p /assets/styles/basemap /assets/fonts; \
    curl -fSL --retry 3 --retry-delay 2 -o /tmp/style.tar.gz "${STYLE_TARBALL_URL}"; \
    tar -xzf /tmp/style.tar.gz -C /tmp; \
    src="$(find /tmp -maxdepth 2 -name style.json -print -quit)"; \
    test -n "$src" || { echo "no style.json in ${STYLE_TARBALL_URL}" >&2; exit 1; }; \
    cp "$src" /assets/styles/basemap/style.json; \
    sprite_dir="$(dirname "$src")"; \
    has_sprite=false; \
    if ls "$sprite_dir"/sprite*.json >/dev/null 2>&1; then \
        cp "$sprite_dir"/sprite* /assets/styles/basemap/; has_sprite=true; \
    fi; \
    jq --arg data "${DATA_ID}" --argjson hasSprite "${has_sprite}" \
       -f /rewrite-style.jq \
       /assets/styles/basemap/style.json > /assets/styles/basemap/style.rewritten.json; \
    mv /assets/styles/basemap/style.rewritten.json /assets/styles/basemap/style.json; \
    leftover="$(jq -r '[(.sources | tostring), (.glyphs // ""), (.sprite // "")] | join(" ")' \
                  /assets/styles/basemap/style.json | grep -oiE 'https?://[^" ]*' || true)"; \
    test -z "$leftover" \
      || { echo "style still reaches a third party after rewrite: $leftover" >&2; exit 1; }; \
    jq -e '.sources | length > 0' /assets/styles/basemap/style.json > /dev/null; \
    jq -e --arg data "${DATA_ID}" \
      '[.sources[] | select(.url == ("mbtiles://{" + $data + "}"))] | length > 0' \
      /assets/styles/basemap/style.json > /dev/null \
      || { echo "no source was rewritten to mbtiles://{${DATA_ID}} — check the style's source types" >&2; exit 1; }; \
    echo "style rewritten:"; jq -c '{sources, glyphs, sprite}' /assets/styles/basemap/style.json

# Fetch the glyph ranges the style's fontstacks need.
#
# Comments kept outside the RUN for the reason given above.
#
#  - Font releases nest differently between versions (some zip a top-level
#    _output/, some the font dirs directly). Rather than assume a layout, every
#    directory that DIRECTLY contains .pbf ranges is copied — exactly the
#    /assets/fonts/<Font Name>/<range>.pbf shape tileserver-gl reads.
#  - A fontstack the style names but the zip lacks makes labels silently vanish
#    at runtime with no error anywhere, so the build asserts they are all
#    present rather than discovering it on the map.
RUN set -eux; \
    curl -fSL --retry 3 --retry-delay 2 -o /tmp/fonts.zip "${FONTS_ZIP_URL}"; \
    unzip -q /tmp/fonts.zip -d /tmp/fonts; \
    find /tmp/fonts -mindepth 1 -maxdepth 4 -type d -exec sh -c \
      'for d; do if ls "$d"/*.pbf >/dev/null 2>&1; then cp -R "$d" /assets/fonts/; fi; done' sh {} +; \
    test -n "$(ls -A /assets/fonts)" || { echo "no font dirs extracted from ${FONTS_ZIP_URL}" >&2; exit 1; }; \
    missing=""; \
    jq -r '[.layers[]?.layout?["text-font"]? // empty | .[]?] | unique | .[]' \
      /assets/styles/basemap/style.json > /tmp/wanted-fonts.txt 2>/dev/null || true; \
    while IFS= read -r want; do \
        [ -n "$want" ] || continue; \
        [ -d "/assets/fonts/$want" ] || missing="$missing '$want'"; \
    done < /tmp/wanted-fonts.txt; \
    test -z "$missing" \
      || { echo "style needs fontstacks the zip did not provide:$missing" >&2; \
           echo "available:" >&2; ls /assets/fonts >&2; exit 1; }; \
    echo "fonts:"; ls /assets/fonts | head -20

# ── Stage 3: serve ────────────────────────────────────────────────────────────
# tileserver-gl (NOT tileserver-gl-light — that build drops the renderer and
# with it every /styles/<id>/{z}/{x}/{y}.png endpoint, which is half the reason
# this service exists).
FROM maptiler/tileserver-gl:${TILESERVER_TAG}
WORKDIR /data
COPY --from=tiles   /planetiler/tiles.mbtiles /data/tiles.mbtiles
COPY --from=assets  /assets/styles            /data/styles
COPY --from=assets  /assets/fonts             /data/fonts
COPY config.json                              /data/config.json

ENV PORT=8080
EXPOSE 8080
# --public_url makes the served style.json advertise absolute tile URLs on the
# real hostname. Without it tileserver-gl derives them from the request, which
# breaks behind a proxy that rewrites Host — the style loads and every tile
# 404s, which looks exactly like a dead basemap. Set PUBLIC_URL on Railway to
# https://<your-domain>/ (trailing slash required).
CMD ["sh", "-c", "exec /usr/src/app/run.sh --config /data/config.json --port ${PORT:-8080} ${PUBLIC_URL:+--public_url \"$PUBLIC_URL\"}"]
