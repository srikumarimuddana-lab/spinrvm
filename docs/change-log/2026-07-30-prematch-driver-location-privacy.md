# Change Impact & Risk — pre-match driver location exposure (T3)

**Date:** 2026-07-30 · **Branch:** `claude/critical-security-pipeda-breach-pn67ww`
**Surface:** backend (rider REST + WebSocket) · **Risk:** medium-high — rider-visible change to a live-tested map
**Related:** `docs/LAUNCH_GATE_IMPLEMENTATION_PLAN.md`, prior commit `edb50c3` (`coarsen_coord`)

Launch-gate hard no-go closed: *"Any client can enumerate precise driver locations
without an assigned ride."*

---

## Issue / gap identified

`GET /drivers/nearby` and the `get_nearby_drivers` WebSocket message both returned
**exact driver coordinates** to any authenticated rider, with a **caller-supplied,
uncapped radius**, alongside a stable driver row `id`, `heading`, `vehicle_make`
and `vehicle_model`.

That combination is enumeration plus re-identification:

| Ingredient | Consequence |
|---|---|
| Exact `lat`/`lng` | Pinpoints a contractor's live position |
| Caller-set `radius` (no cap) | One request with `radius=1000` sweeps the province |
| Arbitrary `lat`/`lng` query params | Walk a grid to enumerate every online driver |
| Stable driver `id` | Correlate across polls → follow one vehicle over time |
| `vehicle_make` + `vehicle_model` + `heading` | Identify the specific car on the street |

The REST handler's own comment said `# hide personal info for riders`, which is
what made this easy to miss: it *did* strip name and phone, so it looked handled.
Exact GPS plus a stable ID plus make/model **is** personal information about where
a specific contractor is right now.

## Root cause

Two independent handlers implementing the same product feature, written at
different times, with no shared policy layer — so "what may a pre-match rider see"
was never stated in one place, and each handler answered it by listing fields
inline. They had already drifted:

- The WS handler had **no geo-bound** on its query. It fetched an arbitrary 100
  online drivers province-wide and filtered in Python, so above 100 online drivers
  the realtime map could omit cars that were genuinely nearby while rendering
  distant ones. The REST endpoint had been fixed for exactly this.
- The WS handler gated on `if lat and lng:` — **truthiness** — so a rider
  legitimately at latitude 0 or longitude 0 silently got an empty map. The REST
  endpoint had been fixed for exactly this too, with a comment explaining it.

Neither handler had **any test**. The existing suite covers
`db_supabase.find_nearby_drivers` (a different function) and the *admin*
`/admin/drivers/nearby` endpoint. 280 tests over the files that mention "nearby"
passed unchanged when this behaviour was rewritten — a concrete instance of the
CLAUDE.md warning that a stub gives zero real coverage.

## Fix / remediation

A single policy module, `utils/driver_map_visibility.py`, now owns the answer for
both call sites:

1. **Coordinates are coarsened** to a 500 m grid cell via `coarsen_coord()`
   (added in `edb50c3`). Deterministic, not jittered — jitter looks like motion
   and averages back to the truth under repeated sampling.
2. **Field allowlist, not denylist.** `_PREMATCH_FIELDS` is `vehicle_type_id`,
   `vehicle_type_name`, `marker_variant`, `heading`. A new column on `drivers`
   cannot become rider-visible for free. Drops `vehicle_make`/`vehicle_model`.
3. **Radius capped server-side** (`driver_map_max_radius_km`, default 15 km).
4. **Kill switch** (`driver_map_show_locations`) — the launch plan requires map
   visibility to sit behind one. When off, both paths return an empty list; the
   rider map renders no cars and the availability count from `/rides/estimate` is
   unaffected, because that endpoint never carried coordinates. This is the
   "disable" arm of the plan's "disable **or** coarse-grain".
5. **`MIN_CELL_M = 100` floor.** `coarsen_coord` treats `cell_m<=0` as an exact
   passthrough, which is correct for the assigned-ride caller but must be
   unreachable from a pre-match path, so a hostile or fat-fingered settings value
   cannot silently re-expose exact positions.
6. **Distance filtering still uses the true position**, so coarsening does not
   widen the effective radius.
7. WS handler additionally gains the geo-bound and the `is not None` fix.

Settings live in DB-backed `app_settings` (per the existing convention) so
granularity can be tuned and the map killed **without a redeploy**.

**Exact coordinates are unchanged for the assigned-ride tracking path and for
admin.** `/drivers` (admin, `get_admin_user`) still returns full rows — admins need
precision for monitoring and safety dispatch.

## Risk & impact on existing functionality

**Blast radius — enumerated, not assumed:**

- **Clients of the changed payload.** Only the rider app calls either path
  (`rider-app/app/(tabs)/index.tsx:258` REST; WS via the shared `wsEvents` type).
  The driver app does not call it at all; admin uses the separate admin endpoint.
- **`vehicle_make`/`vehicle_model` removal is safe.** Every reference in the rider
  app (`driver-arriving.tsx`, `driver-arrived.tsx`, `ride-status.tsx`,
  `ride-in-progress.tsx`, `ai-assistant.tsx`, `_layout.tsx`) reads them from
  `currentDriver` — the **assigned** driver on post-match screens, populated by a
  different endpoint. None reads them from the nearby list. The rider app's own
  type for nearby drivers is `{id, lat, lng, heading?, vehicle_type_id?,
  vehicle_type_name?, marker_variant?}` — it never declared make/model.
- **`id` is deliberately unchanged.** The rider app uses it only as a React `key`
  and map `identifier`; rotating it would remount markers mid-session. See "not
  closed" below — this is the one part of the exposure not fully addressed.
- **`/rides/estimate` untouched.** It reads nearby drivers but only ever returned
  counts, never coordinates.
- **Admin monitoring untouched.**

**Risks accepted:**

- **Reduced map fidelity, deliberately** (see UX below).
- The WS geo-bound is new behaviour on a hot path. It matches what the REST
  endpoint already does and reuses the same `dispatch_geo_bounds` helper, so it
  should be strictly an improvement (fewer rows, indexed), but it is a query-shape
  change on a live surface.

## User experience effect

**Rider-facing, and visible mid-session to someone with the map open.**

- Cars on the pre-booking map now sit at ~500 m grid positions rather than their
  true positions. At city zoom the map still answers "are there cars near me,
  roughly where" — which is its purpose pre-booking — but a rider can no longer
  watch a specific car approach before they have booked. ETA and availability
  are unaffected: both are computed from the **true** positions server-side.
- **Known visual consequence worth watching in canary:** the rider app
  de-clusters colliding markers itself (`(tabs)/index.tsx:285-310`) at ~11 m
  precision, spreading collisions on an 8 m ring. With coarsening, every driver
  in a cell now shares *identical* coordinates, so that logic will ring them
  tightly at the cell centre instead of spreading them naturally. Functionally
  fine, and arguably a more honest depiction of "approximately here", but it is a
  visible change in how clusters look. Not addressed on the backend — spreading
  within the cell would need a deterministic per-driver offset, which is polish,
  not privacy, and should be a rider-app decision.
- Driver-facing: none. Corporate/admin: none.

## Files modified

| File | What changed | Why |
|---|---|---|
| `backend/utils/driver_map_visibility.py` | New — `map_settings`, `clamp_radius`, `prematch_driver_payload/_list`, `MIN_CELL_M`, `_PREMATCH_FIELDS` | One place that owns what a pre-match rider may see, so the two call sites cannot drift again |
| `backend/routes/drivers/location.py` | `/nearby` now resolves settings, honours the kill switch, caps radius, returns the projection | Close the REST exposure |
| `backend/routes/websocket.py` | `get_nearby_drivers` same policy; plus geo-bound query and `is not None` coordinate check | Close the WS exposure and the two drifted defects |
| `backend/schemas.py` | Added `driver_map_show_locations`, `driver_map_cell_m`, `driver_map_max_radius_km` | Tunable + killable without redeploy |
| `backend/tests/test_prematch_driver_location_privacy.py` | New — 23 tests | First tests of either path |
| `docs/change-log/2026-07-30-prematch-driver-location-privacy.md` | New — this file | Required by CLAUDE.md |

## Before / after

```python
# BEFORE — routes/drivers/location.py, exact position + re-identifying fields
safe_driver = {                      # comment said "hide personal info for riders"
    "id": d["id"],
    "lat": d_lat,                    # exact
    "lng": d_lng,                    # exact
    "heading": d.get("heading"),
    "vehicle_type_id": d.get("vehicle_type_id"),
    "vehicle_type_name": ...,
    "marker_variant": ...,
    "vehicle_make": d.get("vehicle_make"),    # + heading => identifies the car
    "vehicle_model": d.get("vehicle_model"),
}

# AFTER — one projection, allowlisted, coarsened
return prematch_driver_list(in_radius, cell_m)
# -> {"id": ..., "lat": 52.13124, "lng": -106.67041, "precision": "approximate",
#     "precision_m": 500, "vehicle_type_id": ..., "vehicle_type_name": ...,
#     "marker_variant": ..., "heading": ...}
```

```python
# BEFORE — websocket.py: no geo-bound, truthiness check on coordinates
if lat and lng:                       # lat=0 -> silently empty map
    drivers = await db_supabase.get_rows(
        "drivers",
        {"is_online": True, "is_available": True, "is_verified": True, "status": "active"},
        limit=100,                    # arbitrary 100 province-wide
    )

# AFTER
if lat is not None and lng is not None:
    drivers = await db_supabase.get_rows(
        "drivers",
        {..., "$and": dispatch_geo_bounds(lat, lng, radius)},
        limit=100,
    )
```

## Rollback plan

**Kill switch first, not a revert.** If the coarsened map causes a product problem,
set `driver_map_show_locations = false` in `app_settings` (admin dashboard, no
deploy) to stop showing positions entirely, or raise `driver_map_cell_m` /
adjust `driver_map_max_radius_km` to tune. Settings propagate within the
60-second `settings_loader` TTL.

There is deliberately **no setting that restores exact coordinates** — the
`MIN_CELL_M` floor prevents it, because that would re-open the no-go. Restoring
exact pre-match positions requires a code revert and a privacy sign-off, which is
the intended friction.

`git revert` is otherwise safe: no migration (the settings columns are pydantic
defaults, not DDL), no data written, no ride/money state touched.

## Verification performed

- **New tests:** 23 pass — 20 policy/REST plus 3 driving the real WebSocket
  handler through a `TestClient`.
- **Mutation-verified, six mutations, all caught:**

  | Mutation | Failing tests |
  |---|---|
  | Return exact coordinates from the projection | 5 |
  | Remove the `MIN_CELL_M` floor | 1 |
  | Remove the radius cap | 2 |
  | Put `vehicle_make`/`vehicle_model` back in the allowlist | 3 |
  | Ignore the kill switch in the REST route | 1 |
  | Drop the WS geo-bound | 2 |

- **Anti-vacuity:** tests assert drivers are still listed, that a driver outside
  the radius is still excluded (so coarsening did not widen the radius), that
  `vehicle_type_name`/`marker_variant` survive, and that REST and WS return
  identical output for identical input.
- **A test-design error was found and fixed:** the first version asserted that one
  *specific* pair of drivers 60 m apart always collapses to one cell. That is false
  for any grid — a chosen pair can straddle a boundary — and it failed for exactly
  that reason. Replaced with the aggregate property (81 drivers in a 400 m box → ≤4
  distinct reported positions). This is the second time this mistake was made in
  this work; the fixed tests carry a comment saying so.
- **Full suite:** `pytest -m "not slow"` → **5835 passed, 8 skipped, 1 xfailed,
  1 failed** (5796 before; +39 new). The single failure is the same pre-existing
  `test_compliance_reports.py` timestamp mismatch, proven unrelated in the T1 log.
- **Client blast radius checked by grep, not assumption** — every
  `vehicle_make`/`vehicle_model` consumer in the rider app enumerated and confirmed
  to read from `currentDriver`, not the nearby list.
- **Lint:** `ruff check` clean on all five changed backend files.

## What was NOT verified

- **No production build was run, and no rider app build was run.** This is a
  backend-only diff, so `npm run build` is not applicable to the changed files —
  but the *consumer* is the rider app, and its behaviour with identical
  coordinates across many drivers (the marker-ring effect described above) was
  **reasoned about from reading `(tabs)/index.tsx:285-310`, not observed**. There
  is no visual/snapshot regression tooling for the rider map in this repo
  (standing gap, `ACTION_ITEMS.md`), so the visual outcome needs a human look in
  staging or canary before wide rollout. This is the weakest link in this change.
- **Not tested against live Supabase.** All DB interaction is mocked, so the WS
  geo-bound's real query plan and row counts are unmeasured. `dispatch_geo_bounds`
  is already used in production by the REST endpoint and dispatch, which is the
  basis for expecting it to be safe — not a measurement.
- **The stable driver `id` is unchanged, so longitudinal tracking at 500 m
  resolution remains possible.** A caller can still poll and follow one driver's
  coarse movements, which over time can suggest a home area or shift pattern. The
  literal no-go ("enumerate *precise* locations") is closed by coarsening and the
  radius cap, but this is real residual exposure, not a solved problem. Rotating
  the id (e.g. HMAC per rider-session window) is the fix and needs a rider-app
  decision, because the id is a marker key and rotation remounts markers. Tracked
  separately — deliberately not bundled into a privacy fix on a live surface.
- **No load or latency measurement.** `coarsen_coord` runs per driver per request
  (a few float ops), and the WS geo-bound should reduce rows fetched, but neither
  was benchmarked against the `/drivers/nearby` p95.
- **No rate limiting was added.** Coarsening and the radius cap reduce what a
  sweep yields, but nothing stops a client polling either path aggressively.
  Whether the existing SlowAPI limits cover these routes adequately was not
  checked and is not claimed.
