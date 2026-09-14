# Self-hosted OSRM — Saskatchewan map-matching for billable distance

Spinr bills trips on **road-matched** distance: the trip-end settlement snaps the
driver's GPS trace to the road network and sums the matched route. The matcher is
a self-hosted **OSRM** server. The backend calls
`GET {OSRM_URL}/match/v1/driving/{lng,lat;…}` and is wired in
`backend/utils/route_distance.py` (OSRM preferred, Google Roads fallback,
haversine last; a 1/3×–3× sanity gate protects the fare either way).

This folder is the reproducible OSRM build + a smoke test.

---

## 1. Configure Saskatchewan (build the data)

OSRM needs the SK OpenStreetMap extract preprocessed into its own binary format.
The `Dockerfile` here does it all — downloads the [Geofabrik Saskatchewan
extract](https://download.geofabrik.de/north-america/canada/saskatchewan.html)
(~112 MB as of 2026-09-14) merged with the Alberta extract, runs the MLD pipeline, and bakes the result into the image (no volume
needed; rebuild to refresh the map):

```
osrm-extract -p /opt/car.lua region.osm.pbf   # "driving" profile
osrm-partition region.osrm                     # MLD step 1
osrm-customize region.osrm                      # MLD step 2
osrm-routed --algorithm mld --ip :: region.osrm
```

**Wider coverage:** override the extract at build time (costs build time + RAM):

```
docker build --build-arg REGION_URL=https://download.geofabrik.de/north-america/canada-latest.osm.pbf -t spinr-osrm .
```

### Adding a second province (e.g. Alberta)

**One OSRM process serves exactly one graph.** You cannot load a second `.osrm`
alongside the first, and you cannot point `/match` at a province-specific
dataset per request. To cover Alberta as well as Saskatchewan the two OSM
extracts must be **merged into a single `.osm.pbf` before `osrm-extract` runs**.
The `fetch` stage in the `Dockerfile` does that for you — pass the extra
extract(s) in `EXTRA_REGION_URLS` (space-separated):

```
docker build \
  --build-arg EXTRA_REGION_URLS=https://download.geofabrik.de/north-america/canada/alberta-latest.osm.pbf \
  -t spinr-osrm .
```

Coverage is set by the `EXTRA_REGION_URLS` **ARG in the Dockerfile**, which
defaults to the Alberta extract — so the graph covers Saskatchewan **and**
Alberta. Nothing in the backend changes: same `OSRM_URL`, same endpoints; the
graph simply covers more ground. Narrow it by editing that ARG.

> ### ⚠️ Coverage cannot be set from the Railway dashboard
> An earlier version of this file said to set `EXTRA_REGION_URLS` as a
> "build-time variable" on the service. **That does not work.** Railway does not
> pass service variables to `docker build` as build args: the `ARG` keeps its
> default and the extra province is silently dropped. Verified on the sibling
> tile service on 2026-09-14 — the build log read
> `for url in <saskatchewan only> ;` and the deploy went green.
>
> Two further traps in the same area, both of which also fail green:
> - **A Railway *redeploy* never rebuilds.** It reuses the previous deployment's
>   existing build, so no build-time change of any kind takes effect.
> - **This service has a `/deploy/osrm/**` watch pattern**, so commits touching
>   only other directories are SKIPPED and never rebuild it.
>
> Net effect: a coverage change must be a **commit under `deploy/osrm/`**.
> Verify the result with `EXPECT_ALBERTA=1 deploy/osrm/smoke-test.sh` rather
> than assuming — and see `docs/change-log/2026-09-14-osrm-alberta-coverage.md`.

Three options, cheapest first:

| Approach | Build cost | When to use |
|---|---|---|
| SK only | Baseline | Narrower than today — edit the ARG to `""` if you want to shrink the graph back. |
| SK + AB (**current default**) | Meaningfully larger than SK alone — Alberta's extract is several times the size of Saskatchewan's | Cross-border trips (Lloydminster straddles the SK/AB line), or launching in AB. This is what ships. |
| `REGION_URL=<canada-latest>` | Much larger again — whole-country extract | Only if you actually serve nationally |

Verify the current sizes on
[Geofabrik's Canada page](https://download.geofabrik.de/north-america/canada.html)
before you build — they grow over time, and `osrm-extract`'s peak RAM scales
with the extract, so a build that fits Railway's builder today may not later.
If a build dies without a clear error, it is almost certainly OOM in
`osrm-extract`; drop back to a narrower region.

> **⚠️ This changes recorded distance. Whether it changes a fare depends on one
> flag.**
>
> Adding Alberta means a trace that enters the province gets map-matched instead
> of falling through, so `actual_distance_km` — the number on the admin
> dashboard, the SGI dispute map and the audit trail — changes for those trips.
>
> **It does not change what anyone is charged, under current production
> settings.** Verified against production on 2026-09-13:
> `fare_lock_enabled = true`, `fare_distance_basis = 'road'`. Migration 248
> records this as an owner-confirmed product decision — *"no post-ride GPS
> re-pricing"* — and `routes/drivers/ride_complete.py`'s `_fare_lock` branch
> writes `distance_km` only, skipping `recalculate_fare_for_distance`
> entirely. The rider's pre-booking quote is priced on the Google Directions
> road distance (`routes/rides/_shared.py`) and never touches OSRM either way.
>
> **It becomes a real fare change if `fare_lock_enabled` is ever turned off**,
> at which point settlement reprices on the measured distance. Re-check the live
> flag before assuming display-only.
>
> Two further things to know before enabling this on a real deployment:
>
> - **The sanity gate may reject the very trips this fixes.**
>   `utils/trip_distance.py`'s 1/3×–3× gate compares the road distance against a
>   haversine baseline that **drops** segments over 5 km, 300 s, or 150 km/h
>   rather than interpolating them. Long rural stretches near a provincial
>   border are exactly where those gaps happen, so the deflated baseline can put
>   a correct, complete OSRM distance above `3×` and get it discarded —
>   silently keeping the old lower number. Dry-run a gappy rural cross-border
>   trace, not just an urban one.
> - **Today's "before" number may already be wrong, not cleanly haversine.** A
>   partial match returns `code:"Ok"` with several non-contiguous `matchings`,
>   and `_compute_via_osrm` sums only what matched. A trip that dips into
>   uncovered Alberta and returns can therefore be silently under-counted today
>   rather than falling back to Google Roads at all — so the size of the change
>   for those trips may be larger than a haversine-vs-road comparison suggests.
>
> Trips wholly inside Saskatchewan are unaffected — the SK graph is identical
> either way.

### Deploy on Railway

- Point the OSRM service at this Dockerfile (`deploy/osrm/Dockerfile`, root
  dir `deploy/osrm`), or `railway up` from this folder.
- Railway sets `PORT`; the container binds it. Two non-negotiables baked into
  the run command:
  - `--algorithm mld` — must match the partition/customize preprocessing.
  - `--ip ::` — Railway **private networking is IPv6-only**. Without this, the
    backend's `*.railway.internal` calls get connection-refused. (It still
    serves IPv4, so the public domain works too.)

---

## 2. Wire the backend

Set on the **backend** service → Variables (no code redeploy needed; restart):

```
OSRM_URL=http://<osrm-service>.railway.internal:5000     # private (recommended)
# or
OSRM_URL=https://<osrm-service>.up.railway.app           # public; no port, no trailing slash
```

Empty/unset → OSRM disabled (falls back to Google Roads, then haversine). A DB
override `osrm_url` in `app_settings` beats the env var if you prefer rotating
it from the admin dashboard.

---

## 3. Test it

```
OSRM_URL=https://<your-osrm>.up.railway.app deploy/osrm/smoke-test.sh
```

It checks `/nearest` (is SK loaded?) and `/match` (does map-matching work?) with
real Regina coordinates. `✅ PASS` = the backend can bill on road distance.

Manual one-liners (OSRM coords are **lng,lat** — longitude first):

```bash
base=https://<your-osrm>.up.railway.app

# Region loaded? expect {"code":"Ok",...} with a Regina waypoint.
curl -fsS "$base/nearest/v1/driving/-104.6189,50.4452"

# Map-match a ~2 km Regina trace; expect "code":"Ok" and matchings[].distance (m).
curl -fsS "$base/match/v1/driving/-104.6178,50.4452;-104.6189,50.4378;-104.6205,50.4291?overview=false&gaps=ignore&tidy=true"
```

Built with `EXTRA_REGION_URLS`? Add `EXPECT_ALBERTA=1` and the smoke test also
asserts an Edmonton coordinate resolves — a merged build that silently fell back
to SK-only otherwise looks identical to a working one:

```
EXPECT_ALBERTA=1 OSRM_URL=https://<your-osrm>.up.railway.app deploy/osrm/smoke-test.sh
```

Handy coordinates (lat, lng — flip to lng,lat for OSRM):

| City | lat, lng | OSRM (lng,lat) |
|---|---|---|
| Regina (downtown) | 50.4452, -104.6189 | `-104.6189,50.4452` |
| Saskatoon | 52.1332, -106.6700 | `-106.6700,52.1332` |
| Moose Jaw | 50.3917, -105.5347 | `-105.5347,50.3917` |
| Prince Albert | 53.2033, -105.7531 | `-105.7531,53.2033` |
| Lloydminster (SK side) | 53.2780, -110.0000 | `-110.0000,53.2780` |
| Edmonton, AB | 53.5461, -113.4938 | `-113.4938,53.5461` |
| Calgary, AB | 51.0447, -114.0719 | `-114.0719,51.0447` |

**Don't test the merge with a `"code":"Ok"` check.** OSRM always snaps to the
nearest road it *has*, so an SK-only graph answers `Ok` for Edmonton by snapping
~230 km east to the Saskatchewan border. What separates the two cases is the
**snap distance** — metres when Alberta is really loaded, hundreds of kilometres
when it isn't:

```bash
curl -fsS "$base/nearest/v1/driving/-113.4938,53.5461?number=1"
# merged SK+AB → "distance": 12.4      (a real Edmonton street)
# SK-only      → "distance": 231480.7  (the SK border, 230 km away)
```

Lloydminster is a poor test for the same reason in reverse: the provincial
boundary runs through the city, so SK roads sit a few hundred metres from any
Alberta coordinate there and an SK-only graph returns a short, plausible-looking
route. `EXPECT_ALBERTA=1` uses the Edmonton snap distance for exactly this
reason.

### End-to-end (backend → OSRM)

After setting `OSRM_URL` and restarting the backend, complete a test ride with
> 5 `trip_in_progress` breadcrumbs, then check the ride's `actual_distance_km` /
`ride_metrics`. On a provider failure the backend logs
`[route_distance] OSRM ...` and silently falls back — so absence of those warn
logs on a completed trip means OSRM answered.

---

## 4. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `{"code":"NoMatch"}` on SK coords | Extract doesn't cover the area — rebuild with the SK (or wider) extract. |
| SK coords fine, Alberta coords `NoMatch` after a merged build | `EXTRA_REGION_URLS` didn't reach the build. **Setting it on the Railway service does nothing** — Railway does not pass service variables as `docker build` args; it must be the ARG default in the Dockerfile. Confirm the build log shows two `curl` lines and an `osmium merge`, and a `region.osm.pbf` larger than SK alone. |
| Merged build fails during `osrm-extract` with no useful error | Almost always OOM — peak RAM scales with the merged extract. Use a narrower region or a bigger builder. |
| `osmium merge` errors about unsorted input | A non-Geofabrik extract. `osmium sort` each input first, or stick to Geofabrik, whose extracts are already sorted by (type, id). |
| All requests time out / connection refused via `*.railway.internal` | Missing `--ip ::` (IPv6). Use the public URL or fix the bind. |
| `code:Ok` but distance ~0 or way off | Coordinate order flipped — OSRM is **lng,lat**, not lat,lng. |
| `Too many trace coordinates` | Raise `--max-matching-size` (default 100); the backend already downsamples to 100. |
| Matching splits one trip into many pieces | Expected on sparse/noisy traces; the backend sums all `matchings[].distance` and sends `gaps=ignore&tidy=true`. |
| Backend still billing haversine | `OSRM_URL` unset/typo, or the value fell outside the 1/3×–3× sanity gate vs haversine (check warn logs). |
