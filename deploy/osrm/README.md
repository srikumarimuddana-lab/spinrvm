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
(~40 MB), runs the MLD pipeline, and bakes the result into the image (no volume
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

Handy SK coordinates (lat, lng — flip to lng,lat for OSRM):

| City | lat, lng | OSRM (lng,lat) |
|---|---|---|
| Regina (downtown) | 50.4452, -104.6189 | `-104.6189,50.4452` |
| Saskatoon | 52.1332, -106.6700 | `-106.6700,52.1332` |
| Moose Jaw | 50.3917, -105.5347 | `-105.5347,50.3917` |
| Prince Albert | 53.2033, -105.7531 | `-105.7531,53.2033` |

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
| All requests time out / connection refused via `*.railway.internal` | Missing `--ip ::` (IPv6). Use the public URL or fix the bind. |
| `code:Ok` but distance ~0 or way off | Coordinate order flipped — OSRM is **lng,lat**, not lat,lng. |
| `Too many trace coordinates` | Raise `--max-matching-size` (default 100); the backend already downsamples to 100. |
| Matching splits one trip into many pieces | Expected on sparse/noisy traces; the backend sums all `matchings[].distance` and sends `gaps=ignore&tidy=true`. |
| Backend still billing haversine | `OSRM_URL` unset/typo, or the value fell outside the 1/3×–3× sanity gate vs haversine (check warn logs). |
