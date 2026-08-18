# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-14 |
| Author | Claude Code (session: imported-rides-map-generation) |
| Surface(s) | backend (+ data repair affecting driver-app / rider-app / admin-dashboard rendering) |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/imported-rides-map-generation-bwqjxm` (PR #3927) |
| Related issue or gap ID | — (found during legacy-import snapshot work) |

## 1. Issue / gap identified

The driver app's ride-detail map drew **no route line** for all 187 legacy-imported
rides, and sat at a wide default region instead of framing the trip. Caught from a
driver-app screenshot of a completed imported ride (6th St E → Cosmopolitan
Industries, 4.9 km) showing the whole of Saskatoon with no polyline.

## 2. Root cause

`rides.planned_route_polyline` is contractually a decoded `[[lat, lng], …]` array
(migration 100). `backend/scripts/backfill_imported_ride_routes.py` wrote OSRM
geometry as `[{"lat": …, "lng": …}, …]` objects instead.

Nothing errored. `validCoordinate()` in `shared/utils/routeSegments.ts` requires each
point to be an **array**, and `normalizeActualRouteSegments()` rejects a segment
*wholesale* when any point fails — so all 171–300 points were silently discarded,
`mapCoordinates` came back `[]`, `<RouteLine>` drew nothing, and the
`fitToCoordinates` effect (guarded on `mapCoordinates.length >= 2`) never ran, leaving
the map at its wide `initialRegion`. Both symptoms had the same single cause.

The "reject the whole segment" behaviour is correct and deliberate (it stops bad GPS
from drawing a false chord) — the data was wrong, not the guard.

## 3. Fix / remediation

1. **Migration 313** converts the 186 affected rows from `{lat,lng}` objects to
   `[lat, lng]` arrays (1 of the 187 was already correct). Lossless: 0 points dropped.
2. **Writer fixed** — `backfill_imported_ride_routes.py` now emits arrays, so a re-run
   cannot reintroduce the bad shape.
3. **Readers hardened** — new `normalize_polyline_points()` in `utils/route_snapshot.py`
   accepts either shape, so a snapshot render can never silently lose the route on an
   unconverted row. Both snapshot call sites now use it.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface read path, but data confined to 187 legacy-imported rides.**

Grepped every consumer of `planned_route_polyline`. All of them already expected
`[[lat,lng], …]`, so this repair moves the data *toward* what every reader wants — no
consumer needed changing:

| Consumer | Expectation | Before repair | After |
|---|---|---|---|
| `backend/schemas.py:798` | `List[List[float]]` | violated | conforms |
| `driver-app/store/driverStore.ts:368` | `[number, number][]` | violated | conforms |
| `rider-app/store/rideStore.ts:194` | `[number, number][]` | violated | conforms |
| `shared/utils/routeSegments.ts` `validCoordinate` | array points | rejected all → blank map | renders |
| `admin-dashboard/.../ride-detail-modal.tsx:762` | `p[0]` / `p[1]` | `undefined` lat/lng | correct |
| `driver-app/lib/androidAuto/carRoute.ts` | `[[lat,lng]]` | rejected | renders |
| `rider-app/app/{ride-details,ride-completed}.tsx` | via `toReactNativeSegments` | rejected → blank | renders |

Explicitly checked and **not** affected:
- **Only imported rides carried the bad shape** — a `jsonb_typeof` census over the whole
  `rides` table found zero non-imported rows with object-shaped points, so no
  organically-created ride is touched.
- **No money, fare, or distance field is read from this column.** `distance_km` was
  written by the same script but as a separate scalar and is unchanged; fares were
  settled at import time and are not recomputed from geometry.
- **Ride state machine untouched** — no status transition, no WS event, no dispatch path.
- **Background loops untouched** — the route finalizer writes v2 `actual_route_segments`
  on a different column and only for `route_schema_version >= 2` rides; imported rides
  are v1 and have no v2 route row, so the finalizer never reads this column for them.
- **Snapshot PNGs already in storage are correct** and were NOT regenerated — they were
  rendered by a dict-aware reader before the conversion. Left as-is deliberately.

Residual risk: a row that ended up with fewer than 2 usable points would have been
skipped rather than written back as a stub — the census confirms none hit that path
(min 2, max 300 points, 0 lossy conversions).

## 5. User-experience effect

- **Driver**: on the ride-detail screen for an imported (pre-migration) ride, the route
  now draws as the standard orange→red gradient and the map frames the trip instead of
  showing all of Saskatoon. Visible improvement, no copy change.
- **Rider**: same fix on `ride-details` / `ride-completed` for imported rides.
- **Internal admin**: the ride drawer's planned-route overlay now plots correctly rather
  than at `undefined` coordinates.
- **Mid-session visibility**: no. These are historical completed rides only — no rider
  mid-ride or driver online is affected, and no active flow changes behavior.
- No notification, no new validation, no rejected input.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/313_fix_imported_ride_polyline_shape.sql` | New: converts object-shaped points to `[lat,lng]` arrays | Repair the 186 malformed rows |
| `backend/scripts/backfill_imported_ride_routes.py` | Writes `[[lat,lng]]` instead of `{lat,lng}` | Stop reintroducing the bad shape on re-run |
| `backend/utils/route_snapshot.py` | New `normalize_polyline_points()` | Shape-tolerant reader so a render never silently drops the route |
| `backend/routes/admin/rides.py` | Snapshot endpoint uses the normalizer | Was dict-only; would have broken after conversion |
| `backend/scripts/backfill_imported_ride_snapshots.py` | Same | Same |
| `backend/tests/test_route_snapshot_coverage.py` | +6 regression tests | Lock both shapes, bool/None rejection |

## 7. Before / after

```python
# Before — backfill_imported_ride_routes.py:174
update_data["planned_route_polyline"] = json.dumps(
    [{"lat": p[0], "lng": p[1]} for p in polyline]
)
```

```python
# After
update_data["planned_route_polyline"] = json.dumps(
    [[p[0], p[1]] for p in polyline]
)
```

```jsonc
// Stored value — before (rejected by validCoordinate → blank map)
[{"lat": 52.112725, "lng": -106.655229}, …]

// After
[[52.112725, -106.655229], …]
```

## 8. Rollback plan

Migration 313's top comment carries the **lossless inverse** UPDATE (rebuilds
`{lat,lng}` objects from the arrays, scoped to `legacy_import_metadata IS NOT NULL`).
Run it directly against the DB — no deploy needed, since the shape-tolerant
`normalize_polyline_points()` reader handles either form.

Reverting is almost certainly the wrong move regardless: the pre-fix state is a blank
map, not a working one. No money, ride state, or insurance-period rows were touched, so
there is no data-level remediation beyond the inverse UPDATE.

## 9. Verification performed

- [x] Automated tests: 6 new unit tests over `normalize_polyline_points` (array shape,
      object shape, mixed/malformed, bool rejection, int widening, unusable input).
- [x] Blast-radius grep: `planned_route_polyline` and `route_schema_version` across the
      repo excluding `node_modules` — all 30+ call sites reviewed and tabulated above.
- [x] SQL census before applying: 186 object-shaped / 1 array-shaped, all imported;
      dry-run of the transform reported `lossy_conversions = 0`.
- [x] Post-apply verification: all 187 imported rides now `jsonb_typeof(… -> 0) = 'array'`,
      2–300 points each, sample point `[52.130053, -106.681272]` (correct lat/lng order).
- [x] Reviewed against `CLAUDE.md` conventions: migration naming/append-only/reversible-
      on-paper, no float money arithmetic involved, no PII in logs.
- [ ] Feature flag: not applicable — this is a data repair toward the documented
      contract, with no new user-visible surface to gate.

## 10. What was NOT verified

- **No production build was run** for driver-app / rider-app / admin-dashboard, because
  **no app code was changed** — the fix is entirely backend + data. The apps were read to
  confirm they already expect the corrected shape, not modified.
- **The rendered result was not visually confirmed.** The conclusion that the route now
  draws is reasoned from `validCoordinate()`'s contract plus the verified stored shape —
  it has not been screenshotted in the driver app. This repo has no visual-regression
  tooling for the mobile surfaces (standing gap, `ACTION_ITEMS.md`), so a screenshot from
  the driver app is the real confirmation and is worth taking.
- **The backend test suite was not run in full** — only the new tests' module. The
  container had no preinstalled backend deps; `pytest` was run after an ad-hoc
  `pip install -r requirements.txt`, not against the CI image.
- **Not tested against a live Supabase read path** — the migration was verified with
  direct SQL, not by fetching a ride through `/api/drivers/rides/{id}` and inspecting the
  serialized payload.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (inverse UPDATE in the migration header)
- [x] Blast radius is stated, not assumed (every consumer enumerated)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
