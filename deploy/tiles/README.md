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

> ### Status: built and serving as of 2026-09-14
> This image now builds and runs on Railway (`TilesServer`), serving vector
> tiles, raster `.png`, glyphs and the style. It took four fixes to get there —
> the planetiler invocation (Jib layout, not a jar), the tileserver-gl start
> command in **two** places that had drifted apart, and the OSM extract plumbing.
> §5 records each failure and its signature; read it before changing the build,
> because three of the four failed **green** or looked unrelated to the cause.
>
> Still true: every external artifact is a `--build-arg` so a moved URL or bumped
> tag is a one-line fix, and `PLANETILER_TAG`/`TILESERVER_TAG` are still unpinned
> `latest` — pin them (see below) before relying on reproducible rebuilds.

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
| `REGION_URL` | Geofabrik Saskatchewan `.osm.pbf` | The base extract to build tiles from. |
| `EXTRA_REGION_URLS` | Geofabrik **Alberta** `.osm.pbf` | Space-separated extra `.osm.pbf` URLs merged into `REGION_URL` with `osmium merge`. Planetiler accepts only one input file, so a second province is a merge — not a second `--area`. **Cannot be set from Railway** — see the warning below. |
| `JAVA_OPTS` | `-Xmx8g` | Planetiler heap. Raise for a bigger area; lower if your builder has less RAM. |
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

**Alberta as well as Saskatchewan?** Already on — `EXTRA_REGION_URLS` **defaults**
to the Alberta extract, so the tileset covers SK+AB out of the box. Change the
coverage by editing that ARG in the `Dockerfile`, or locally with:

```bash
docker build \
  --build-arg EXTRA_REGION_URLS=https://download.geofabrik.de/north-america/canada/manitoba-latest.osm.pbf \
  -t spinr-tiles .
```

> ### ⚠️ Do not try to set coverage from Railway
> **Railway does not pass service variables to `docker build` as build args.**
> Setting `EXTRA_REGION_URLS` on the service and rebuilding produces a **green
> build** whose log reads `for url in <saskatchewan only> ;` — the `ARG` keeps
> its default and the extra province is silently dropped. Verified on the
> 2026-09-14 build of this service. That is why coverage lives in the Dockerfile,
> in git, rather than in a dashboard field: a tileset missing a province is
> indistinguishable from a working one until someone looks at that province.

Planetiler accepts exactly one OSM input file (*"Currently only one OSM input
file is supported"*, `Planetiler.java`), so the `osm` stage merges them with
`osmium merge` first and passes the result as `--osm_path`. That override also
retires `--area`: the OpenMapTiles profile ignores it outright once `osm_path`
is set, which is why the `AREA` build arg no longer exists — a knob that is
silently ignored is worse than no knob.

A second tile service for Alberta is **not** a workable alternative, unlike
OSRM: the dashboard points at a single `NEXT_PUBLIC_MAP_STYLE_URL`, so one
tileset has to cover everything you want drawn.

The two services stay independent, though — widening one does **not** widen the
other, and only the OSRM one affects recorded trip distance.

## 3. Deploy on Railway

- New service → this repo, root directory `deploy/tiles`, Dockerfile builder.
- Railway injects `PORT`; the container binds it.
- Set **`PUBLIC_URL`** to `https://<your-domain>/` (trailing slash required).
  Without it tileserver-gl derives tile URLs from the request, which breaks
  behind a proxy that rewrites `Host`: the style loads and every tile 404s,
  which looks exactly like a dead basemap.
- Healthcheck is `/styles/basemap/style.json` — more informative than `/health`
  because it only passes once the config parsed *and* the style resolved.
- **Do not "simplify" `railway.json`'s `startCommand`.** It looks redundant —
  it re-invokes the image's own `ENTRYPOINT` (`/usr/src/app/docker-entrypoint.sh`)
  from inside the `sh -c` that Railway already runs. Both halves are load-bearing:
  the `sh -c` is the only thing that expands `${PORT}`/`${PUBLIC_URL}` (Railway
  passes the start command as argv, not through a shell), and re-entering the
  entrypoint with a **non-executable** first argument (`--config`) is what makes
  it start **Xvfb** before exec'ing node. Point the start command straight at
  node and it boots fine, serves vector tiles fine, and every raster `.png`
  fails — there is no headless GL display. Raster is half the reason this
  service exists (`static-route-map.tsx`), so that failure is worth the odd-looking
  command.
- Give it more RAM/CPU than the OSRM service. Rasterising is headless GL and is
  the expensive part; vector-only serving is cheap.

Unlike OSRM, this service is called by **browsers**, not the backend, so it
needs a public domain — `*.railway.internal` will not work.

## 4. Wire the admin dashboard

Set on the Vercel project (both are optional and independent — set one, both, or
neither, and anything unset keeps today's third-party default). `maps.spinr.ca`
below is illustrative; the service's live host is whatever domain is attached to
it on Railway — currently `tilesserver-production-14c2.up.railway.app`:

```
NEXT_PUBLIC_MAP_STYLE_URL=https://<tile-host>/styles/basemap/style.json
NEXT_PUBLIC_RASTER_TILE_URL=https://<tile-host>/styles/basemap/{z}/{x}/{y}.png
```

- `NEXT_PUBLIC_MAP_STYLE_URL` becomes the **first hop** of `basemapChain()`. The
  existing providers stay behind it as fallbacks, so if this service goes down
  the maps degrade to OpenFreeMap/Carto rather than going blank.
- `NEXT_PUBLIC_RASTER_TILE_URL` retargets the no-WebGL renderer. `{z}`/`{x}`/`{y}`
  are substituted; anything else in the string is left alone.
  **This one has no fallback chain** — `rasterTileUrlTemplate()` is a plain
  `env || Carto` either/or, so setting it makes `static-route-map.tsx` wholly
  dependent on this service. That is a strictly bigger commitment than the
  vector variable above; set it second, once the server has proven stable.
- Optional `NEXT_PUBLIC_MAP_STYLE_URL_DARK` for a dark style, if you build one.
  Unset, the dark theme falls back to the light self-hosted style.

### These do nothing until a production build succeeds

`NEXT_PUBLIC_*` values are **inlined into the bundle at build time**, so:

- Saving the variable in Vercel does **not** redeploy. You must trigger a
  deployment yourself.
- Scope it to the **Production** environment. A variable set only for
  Preview/Development is correct-looking and inert in production.
- Redeploy with the build cache **off**, so the new value is actually re-inlined.
- Vercel **auto-cancels a production build that a newer commit supersedes.** On
  2026-09-14 eight consecutive production deployments were CANCELED this way
  during a run of back-to-back merges, leaving production pinned to a bundle
  built over an hour earlier — with the dashboard still on OpenFreeMap and an
  admin-visible "Basemap slow to load" banner, while the tile server itself was
  healthy and serving in 10-124 ms. If the banner persists after setting the
  variable, check that a production deployment actually reached **READY** before
  looking anywhere else.

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Raster tile 404s, vector fine | You are on `tileserver-gl-light`, which has no renderer. Use the full `maptiler/tileserver-gl`. |
| Style loads, every tile 404s | `PUBLIC_URL` unset behind a proxy. Set it to the public origin with a trailing slash. |
| Map renders in curl but is blank in the browser, no failed requests | Almost always CORS. The dashboard is on a different origin; the smoke test's CORS check catches this and curl alone never will. |
| Vector tile is ~a few hundred bytes | An empty tile — the extract does not cover that area. Check `REGION_URL` / `EXTRA_REGION_URLS`. |
| Labels missing everywhere, no errors | The fontstack the style names is not in the fonts zip. The build asserts this now, so a fresh build fails loudly instead; an older image may not. |
| Build fails at the style step with "no source was rewritten" | The style's vector source is not typed `vector`, or the tarball layout changed. Adjust `rewrite-style.jq` — it is a standalone file you can run against a downloaded style to debug: `jq --arg data v3 --argjson hasSprite true -f rewrite-style.jq style.json`. |
| Build fails with "style still reaches a third party after rewrite" | Working as intended — the rewrite missed a source, glyph or sprite URL. The message names the exact field. |
| Build fails at the fonts step | `FONTS_ZIP_URL` moved. Point it at a current release. |
| Build OOMs during planetiler | Raise `JAVA_OPTS` if the builder has headroom, or drop back to a single province. Peak RAM scales with the merged extract, so this is the first thing to fail on a multi-province build. |
| Container restart-loops with `exec: /usr/src/app/run.sh: not found` | There is no `run.sh` in `maptiler/tileserver-gl` — the entrypoint is `/usr/src/app/docker-entrypoint.sh`. The start command is defined in **two** places and both had to change: the `CMD` at the bottom of the `Dockerfile` (what actually runs — Railway auto-detects the Dockerfile and does not necessarily apply `railway.json`'s `startCommand`) and `railway.json`. If you change one, change both. See §3 for why it re-enters the entrypoint rather than calling node directly. |
| Vector tiles fine, every raster `.png` 500s or hangs | Xvfb never started, so the renderer has no display. Almost always a "simplified" `startCommand` whose first argument is an executable — the entrypoint then skips its Xvfb branch. See §3. |
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
