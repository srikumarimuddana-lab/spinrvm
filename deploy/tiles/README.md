# Self-hosted basemap tiles

The admin dashboard's maps currently render off a chain of third-party
basemaps — OpenFreeMap → Protomaps (if keyed) → Carto — and fail over between
them when one is slow (`basemapChain()` in
`admin-dashboard/src/lib/map/maplibre-base.ts`). That chain works, but the
"Basemap slow to load — trying another provider…" banner admins see is the
first hop timing out, every time, on somebody else's donation-funded CDN.

This service replaces the chain with one we own, and serves **both** shapes the
dashboard needs:

| Endpoint | Consumed by |
|---|---|
| `/styles/basemap/style.json` | MapLibre vector path → `NEXT_PUBLIC_MAP_STYLE_URL` |
| `/styles/basemap/{z}/{x}/{y}.png` | no-WebGL raster fallback → `NEXT_PUBLIC_RASTER_TILE_URL` |
| `/data/v3/{z}/{x}/{y}.pbf` | raw vector tiles (what the style points at) |
| `/fonts/{fontstack}/{range}.pbf` | label glyphs |

The raster endpoint is why this is **tileserver-gl** and not a bare `.pmtiles`
file on object storage. `static-route-map.tsx` — the renderer that survives an
ad blocker stubbing WebGL — draws plain `<img>` raster tiles, and nothing that
serves only vector can feed it.

> ### ⚠️ Read this before deploying
> **This image has never been built.** It was authored in an environment with no
> network access to Geofabrik, Docker Hub or GitHub, so no layer here has been
> executed and no tag or release URL below has been confirmed to still exist.
> The build logic was simulated step-by-step against sample inputs, and the
> Web-Mercator tile maths in `smoke-test.sh` is cross-checked against the
> `project()` function in `static-route-map.tsx` — but that is not the same as a
> green build.
>
> **Build it locally and run `smoke-test.sh` against it before pointing Railway
> at it.** Every external artifact is a `--build-arg` precisely so you can fix a
> moved URL or bumped tag without editing the Dockerfile. §5 lists what breaks
> first.

---

## 1. Build the data

`planetiler` downloads the OpenStreetMap extract itself and generates
OpenMapTiles-schema vector tiles; `tileserver-gl` then serves and rasterises
them. Both happen at image build time, so the tiles are immutable in the image
and there is no Railway volume to manage — rebuild to refresh the map, exactly
like `deploy/osrm`.

```bash
cd deploy/tiles
docker build -t spinr-tiles .
docker run --rm -p 8080:8080 spinr-tiles

# in another shell
TILES_URL=http://localhost:8080 ./smoke-test.sh
```

Do not skip the local run. A tile server that boots cleanly and serves an empty
map is the normal failure here, and the smoke test is what distinguishes them.

## 2. Build arguments

| Arg | Default | Notes |
|---|---|---|
| `AREA` | `saskatchewan` | Geofabrik area name. Planetiler resolves it against its own index — no URL needed. |
| `JAVA_OPTS` | `-Xmx4g` | Planetiler heap. Raise for a bigger area; lower if your builder has less RAM. |
| `STYLE_TARBALL_URL` | openmaptiles/positron-gl-style `master` | Any MapLibre GL style repo tarball. Positron matches the light, low-chrome look the dashboard already gets from Carto, so self-hosting is not a visual change. |
| `FONTS_ZIP_URL` | openmaptiles/fonts `v2.0` | Pre-generated PBF glyph ranges. |
| `DATA_ID` | `v3` | Safe to override — `config.json` is re-keyed to match at build time, and the build asserts the style's source resolves against it. |
| `PLANETILER_TAG` / `TILESERVER_TAG` | `latest` | **Pin these after your first successful build — see below.** |

### Pin the image tags after the first successful build

Both default to the mutable `latest`, unlike `deploy/osrm` which pins a real
release. That is a consequence of how this was authored, not a preference: the
environment had no registry access, so no specific tag could be confirmed to
exist, and a wrong pin fails the build outright where `latest` at least
resolves.

Leaving them is a real hazard once this is in service. A routine "rebuild to
refresh the map" would pull whatever `latest` points at that day, and a changed
CLI or file layout (if `/usr/src/app/run.sh` moved, say) fails the build for
reasons unrelated to the map refresh — with nothing recorded about what the
working build used. So on the build that works:

```bash
docker image inspect ghcr.io/onthegomap/planetiler:latest --format '{{index .RepoDigests 0}}'
docker image inspect maptiler/tileserver-gl:latest      --format '{{index .RepoDigests 0}}'
```

and set the two ARG defaults to those versions (or digests) in the Dockerfile.

**Alberta as well as Saskatchewan?** Unlike OSRM, this is not a merge problem —
planetiler takes one area per build, so either build a second service for AB, or
use a wider single area (`--build-arg AREA=canada`, much bigger and slower). If
you are already merging provinces for OSRM (see `deploy/osrm/README.md` §1),
note that the two services are independent: widening one does **not** widen the
other, and only the OSRM one affects billing.

## 3. Deploy on Railway

- New service → this repo, root directory `deploy/tiles`, Dockerfile builder.
- Railway injects `PORT`; the container binds it.
- Set **`PUBLIC_URL`** to `https://<your-domain>/` (trailing slash required).
  Without it tileserver-gl derives tile URLs from the request, which breaks
  behind a proxy that rewrites `Host`: the style loads and every tile 404s,
  which looks exactly like a dead basemap.
- Healthcheck is `/styles/basemap/style.json` — more informative than `/health`
  because it only passes once the config parsed *and* the style resolved.
- Give it more RAM/CPU than the OSRM service. Rasterising is headless GL and is
  the expensive part; vector-only serving is cheap.

Unlike OSRM, this service is called by **browsers**, not the backend, so it
needs a public domain — `*.railway.internal` will not work.

## 4. Wire the admin dashboard

Set on the Vercel project (both are optional and independent — set one, both, or
neither, and anything unset keeps today's third-party default):

```
NEXT_PUBLIC_MAP_STYLE_URL=https://maps.spinr.ca/styles/basemap/style.json
NEXT_PUBLIC_RASTER_TILE_URL=https://maps.spinr.ca/styles/basemap/{z}/{x}/{y}.png
```

- `NEXT_PUBLIC_MAP_STYLE_URL` becomes the **first hop** of `basemapChain()`. The
  existing providers stay behind it as fallbacks, so if this service goes down
  the maps degrade to OpenFreeMap/Carto rather than going blank.
- `NEXT_PUBLIC_RASTER_TILE_URL` retargets the no-WebGL renderer. `{z}`/`{x}`/`{y}`
  are substituted; anything else in the string is left alone.
- Optional `NEXT_PUBLIC_MAP_STYLE_URL_DARK` for a dark style, if you build one.
  Unset, the dark theme falls back to the light self-hosted style.

These are `NEXT_PUBLIC_*`, so they are **inlined at build time** — changing them
in Vercel requires a redeploy, not just a restart.

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Raster tile 404s, vector fine | You are on `tileserver-gl-light`, which has no renderer. Use the full `maptiler/tileserver-gl`. |
| Style loads, every tile 404s | `PUBLIC_URL` unset behind a proxy. Set it to the public origin with a trailing slash. |
| Map renders in curl but is blank in the browser, no failed requests | Almost always CORS. The dashboard is on a different origin; the smoke test's CORS check catches this and curl alone never will. |
| Vector tile is ~a few hundred bytes | An empty tile — the extract does not cover that area. Check `AREA`. |
| Labels missing everywhere, no errors | The fontstack the style names is not in the fonts zip. The build asserts this now, so a fresh build fails loudly instead; an older image may not. |
| Build fails at the style step with "no source was rewritten" | The style's vector source is not typed `vector`, or the tarball layout changed. Adjust `rewrite-style.jq` — it is a standalone file you can run against a downloaded style to debug: `jq --arg data v3 --argjson hasSprite true -f rewrite-style.jq style.json`. |
| Build fails with "style still reaches a third party after rewrite" | Working as intended — the rewrite missed a source, glyph or sprite URL. The message names the exact field. |
| Build fails at the fonts step | `FONTS_ZIP_URL` moved. Point it at a current release. |
| Build OOMs during planetiler | Raise `JAVA_OPTS` if the builder has headroom, or use a smaller `AREA`. |
| Build fails with `Unable to access jarfile /planetiler.jar` or `no main manifest attribute, in /app/libs/planetiler-*.jar` | The planetiler image is built by **Jib** (see its `planetiler-dist/pom.xml`), not from a Dockerfile, so it has no executable jar and no launcher script — only `/app/resources`, `/app/classes`, `/app/libs/*.jar` and an `ENTRYPOINT` a build-time `RUN` cannot invoke. The Dockerfile reconstructs that entrypoint (`java -cp '/app/resources:/app/classes:/app/libs/*' com.onthegomap.planetiler.Main`); the quotes are load-bearing, since `/app/libs/*` is a **Java** classpath wildcard the shell must not glob. Picking any single jar out of `/app/libs` cannot work — none of them carries a `Main-Class`. |

## 6. What this replaces, and what it does not

It removes the third-party dependency for **admin-dashboard basemaps only**.

It does **not** touch:

- **OSRM** (`deploy/osrm`) — routing and map-matching, a completely separate
  service and dataset. This one draws pictures; that one computes billable
  distance. Neither affects the other.
- **The public `/track/<token>` page**, which switched to the Google Maps JS API
  (see `admin-dashboard/.env.example`).
- **rider-app / driver-app**, which use their own map stacks.

Nothing about billing, fares, ride state or driver data goes near this service.
Its total blast radius is which pixels appear behind the pins.
