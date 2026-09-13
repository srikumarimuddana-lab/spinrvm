#!/usr/bin/env bash
# Verify a self-hosted Spinr tile server actually serves everything the admin
# dashboard asks of it. Each check is independent and named after the thing that
# breaks if it fails, so a partial deploy tells you exactly which layer is wrong
# rather than just "the map is blank".
#
# Usage:
#   TILES_URL=https://maps.spinr.ca            deploy/tiles/smoke-test.sh
#   TILES_URL=http://localhost:8080            deploy/tiles/smoke-test.sh
#
# Optional:
#   STYLE_ID=basemap   # must match the key under "styles" in config.json
#   DATA_ID=v3         # must match the key under "data" in config.json
#   ORIGIN=https://admin-spinr.spinr.ca   # origin used for the CORS check
#
# Tile coordinates are COMPUTED from a Saskatoon lat/lng below rather than
# hardcoded — a wrong hardcoded tile requests the wrong square of the planet and
# looks identical to a dead tile server.

set -euo pipefail
: "${TILES_URL:?set TILES_URL to your tile server base URL, no trailing slash}"
base="${TILES_URL%/}"
style_id="${STYLE_ID:-basemap}"
data_id="${DATA_ID:-v3}"
origin="${ORIGIN:-https://admin-spinr.spinr.ca}"

pass() { printf '  \033[0;32m✅ %s\033[0m\n' "$1"; }
fail() { printf '  \033[0;31m❌ %s\033[0m\n' "$1"; exit 1; }
warn() { printf '  \033[0;33m⚠️  %s\033[0m\n' "$1"; }

# Saskatoon — Spinr's default map centre (DEFAULT_CENTER in maplibre-base.ts).
# z12 puts it comfortably inside a tile rather than on a boundary, so a
# half-degree of imprecision cannot flip the result to a neighbouring tile.
lat=52.1332; lng=-106.6700; z=12
read -r tx ty <<EOF
$(awk -v lat="$lat" -v lng="$lng" -v z="$z" 'BEGIN{
  pi = atan2(0, -1); n = 2 ^ z;
  x = int((lng + 180.0) / 360.0 * n);
  r = lat * pi / 180.0;
  y = int((1.0 - log((sin(r) / cos(r)) + (1.0 / cos(r))) / pi) / 2.0 * n);
  printf "%d %d", x, y;
}')
EOF
echo "Saskatoon ($lat, $lng) at z$z → tile $z/$tx/$ty"
echo

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# 1) Config parsed and the style is being served at all.
echo "→ /styles/$style_id/style.json   (config + style loaded?)"
if ! curl -fsS "$base/styles/$style_id/style.json" -o "$tmp/style.json"; then
  fail "no style at /styles/$style_id/ — check STYLE_ID matches config.json's \"styles\" key"
fi
if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$tmp/style.json" 2>/dev/null; then
  fail "style.json is not valid JSON"
fi
pass "style served"

# 2) The style must not reach a third party. If the build-time rewrite silently
#    failed, the map still renders — off someone else's CDN — which defeats the
#    entire point of self-hosting and would fail quietly forever.
echo "→ style sources                  (self-hosted, or still third-party?)"
foreign="$(python3 - "$tmp/style.json" "$base" <<'PY'
import json, sys
from urllib.parse import urlparse
style = json.load(open(sys.argv[1]))
own = urlparse(sys.argv[2]).netloc
bad = []
def check(label, value):
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        if urlparse(value).netloc != own:
            bad.append(f"{label}={value}")
for name, src in (style.get("sources") or {}).items():
    check(f"sources.{name}.url", src.get("url"))
    for t in src.get("tiles") or []:
        check(f"sources.{name}.tiles[]", t)
check("glyphs", style.get("glyphs"))
check("sprite", style.get("sprite"))
print("; ".join(bad))
PY
)"
if [ -n "$foreign" ]; then
  fail "style still points off-host: $foreign"
fi
pass "every source, glyph and sprite URL is self-hosted"

# 3) Vector tiles — what MapLibre actually renders (NEXT_PUBLIC_MAP_STYLE_URL).
echo "→ /data/$data_id/$z/$tx/$ty.pbf        (vector tiles present?)"
if ! curl -fsS "$base/data/$data_id/$z/$tx/$ty.pbf" -o "$tmp/tile.pbf"; then
  fail "vector tile 404 — check DATA_ID matches config.json, and that the mbtiles covers Saskatoon"
fi
pbf_size=$(wc -c < "$tmp/tile.pbf")
echo "  size: ${pbf_size} B"
if [ "$pbf_size" -lt 1000 ]; then
  fail "tile is only ${pbf_size} B — that is an empty tile, so the extract does not cover Saskatchewan"
fi
pass "vector tiles cover Saskatoon"

# 4) Raster tiles — the no-WebGL fallback renderer (NEXT_PUBLIC_RASTER_TILE_URL).
#    This is the check that distinguishes tileserver-gl from tileserver-gl-light:
#    the light build serves 3) fine and has no raster endpoint at all.
echo "→ /styles/$style_id/$z/$tx/$ty.png     (rasteriser alive?)"
if ! curl -fsS "$base/styles/$style_id/$z/$tx/$ty.png" -o "$tmp/tile.png"; then
  fail "raster tile failed — you are probably running tileserver-gl-light, which cannot rasterise. Use the full tileserver-gl image."
fi
if [ "$(head -c 4 "$tmp/tile.png" | od -An -tx1 | tr -d ' \n')" != "89504e47" ]; then
  fail "response is not a PNG — got: $(head -c 80 "$tmp/tile.png")"
fi
png_size=$(wc -c < "$tmp/tile.png")
echo "  size: ${png_size} B"
if [ "$png_size" -lt 2000 ]; then
  warn "only ${png_size} B — a real city tile is usually far larger. Open it and confirm it is not blank."
else
  pass "raster tiles render"
fi

# 5) Glyphs — a missing fontstack loses every label silently, no error anywhere.
echo "→ /fonts/...                     (glyphs served?)"
wanted="$(python3 - "$tmp/style.json" <<'PY'
import json, sys
style = json.load(open(sys.argv[1]))
fonts = []
for layer in style.get("layers") or []:
    for f in (layer.get("layout") or {}).get("text-font") or []:
        if f not in fonts:
            fonts.append(f)
print(fonts[0] if fonts else "")
PY
)"
if [ -z "$wanted" ]; then
  warn "style declares no text-font — labels are off, nothing to check"
else
  # Fontstack names contain spaces ("Noto Sans Regular"), so the path segment
  # has to be percent-encoded or curl builds a malformed URL.
  encoded="$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1], safe=""))' "$wanted")"
  if curl -fsS -o /dev/null "$base/fonts/$encoded/0-255.pbf"; then
    pass "glyphs served for '$wanted'"
  else
    fail "no glyphs for '$wanted' — labels will silently not render"
  fi
fi

# 6) CORS — the dashboard is on a different origin from the tile server, so
#    without this every tile request is blocked by the browser while curl (which
#    ignores CORS) reports everything healthy.
echo "→ CORS                           (reachable from $origin?)"
acao="$(curl -fsS -I -H "Origin: $origin" "$base/styles/$style_id/style.json" \
        | tr -d '\r' | awk -F': ' 'tolower($1)=="access-control-allow-origin"{print $2}')"
if [ -z "$acao" ]; then
  fail "no Access-Control-Allow-Origin header — the browser will block every tile even though curl succeeds. Put the tile server behind a proxy that adds it, or serve it same-origin."
fi
echo "  Access-Control-Allow-Origin: $acao"
if [ "$acao" = "*" ] || [ "$acao" = "$origin" ]; then
  pass "CORS allows the dashboard origin"
else
  fail "CORS allows '$acao' but the dashboard is '$origin'"
fi

echo
printf '\033[0;32mAll checks passed.\033[0m Wire it up with:\n'
echo "  NEXT_PUBLIC_MAP_STYLE_URL=$base/styles/$style_id/style.json"
echo "  NEXT_PUBLIC_RASTER_TILE_URL=$base/styles/$style_id/{z}/{x}/{y}.png"
