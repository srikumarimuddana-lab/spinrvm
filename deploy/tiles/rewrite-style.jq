# Rewrite a community MapLibre GL style so it references only assets this tile
# server hosts itself. Applied at image build time by deploy/tiles/Dockerfile.
#
# Lives in its own file rather than inline in the Dockerfile because a jq
# program spans multiple lines, and a line inside a RUN that does not end with
# a backslash terminates the instruction — Docker would then try to parse
# `.sources |= ...` as a Dockerfile instruction and fail the build.
#
# Inputs:
#   --arg     data       the config.json "data" key to point vector sources at
#   --argjson hasSprite  whether a sprite sheet was found next to the style
#
# Run it over a style with:
#   jq --arg data v3 --argjson hasSprite true -f rewrite-style.jq style.json

# Every vector source ships pointing at a hosted MapTiler/OpenMapTiles endpoint.
# Replace each wholesale — not just the url — so no stale key (an API token, a
# tiles[] array) survives alongside the local reference.
.sources |= with_entries(
  if (.value.type == "vector")
  then .value = { "type": "vector", "url": ("mbtiles://{" + $data + "}") }
  else . end
)

# tileserver-gl serves glyphs from its own /fonts/ endpoint and prefixes this
# relative form with the public URL when it hands out the style.
| .glyphs = "{fontstack}/{range}.pbf"

# A sprite sheet copied next to the style resolves relatively. With none, drop
# the key entirely: a style without a sprite is valid and simply renders no POI
# icons, which beats leaving a third-party URL in a style whose whole purpose is
# having no third parties in it.
| if $hasSprite then .sprite = "sprite" else del(.sprite) end
