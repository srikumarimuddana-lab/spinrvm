# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-29 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | 08-22 legacy migration batch — route/snapshot backfill step, `docs/runbooks/legacy-booking-import-2026-08-22-batch.md` |

## 1. Issue / gap identified

The 62 newly-imported legacy rides (55 regular completed + 7 zero-fare
completed) were committed without a road-following `planned_route_polyline`
or a computed `distance_km` — the CSV import only carries pickup/dropoff
coordinates, not a route. A CLI script
(`backend/scripts/backfill_imported_ride_routes.py`) already existed to
backfill this, but it requires direct shell + `SUPABASE_SERVICE_ROLE_KEY`
access, which this session's standing constraint (never type production
service-role credentials into chat, all writes go through the deployed
admin dashboard/API) rules out as the execution path for the live batch.
There was no admin-dashboard UI equivalent — only the separate "Regenerate
Snapshots" tool (PNG map images), which is a different backfill entirely
and does not touch `planned_route_polyline`.

## 2. Root cause

Two distinct backfills were bundled under one "route/snapshot backfill"
task name but are unrelated: (a) `route_snapshot_url` — a rendered PNG map
image, already covered by the existing "Regenerate Snapshots" admin tool
(and its concurrency fix earlier today), and (b) `planned_route_polyline` /
`distance_km` — the actual road-route geometry and trip distance, computed
via OSRM (Google Directions fallback), which had no admin-dashboard path at
all. This gap was only discovered mid-flow while verifying the snapshot
backfill's production state via SQL.

## 3. Fix / remediation

Added a new admin route, `POST /api/admin/rides/regenerate-imported-routes`
(`backend/routes/admin/rides.py`), gated `role == "super_admin"`. It:

- Fetches rides with `legacy_import_metadata IS NOT NULL` (up to 500).
- Filters to rides needing a route: `force=true` re-does all of them;
  otherwise only rides with a missing or trivial (`len <= 1`)
  `planned_route_polyline`.
- Reuses the existing, already-production-used
  `utils/route_distance.py::compute_route()` (OSRM-first, Google Directions
  fallback) rather than re-implementing OSRM-calling logic — the same
  function `routes/rides/booking.py` calls for live ride creation, so the
  write shape (`planned_route_polyline` as a native
  `[[lat,lng],...]` Python list, not `json.dumps()`-stringified) matches
  the live convention exactly (confirmed by reading
  `routes/rides/booking.py`'s own write call).
- Runs concurrently via `asyncio.gather()` bounded by
  `asyncio.Semaphore(_ROUTE_CONCURRENCY = 8)` — built with the same
  bounded-concurrency pattern as today's earlier snapshot-regeneration fix,
  from the start, rather than shipping a sequential loop and hitting the
  same production-stall bug class a fifth time.
- Returns `{total, success, failed, errors}`; audit-logs the run via
  `log_admin_action`.

Frontend: added `adminRegenerateImportedRoutes()` +
`RouteRegenerateResult` to `admin-dashboard/src/lib/api/imports.ts`
(re-exported from `src/lib/api.ts`), and a new `RouteRegenerateSection`
component in `bulk-operations/page.tsx`, mirroring the existing
`SnapshotRegenerateSection` exactly (checkbox for "re-generate all",
submit button with loading state, results panel with success/failed counts
and an expandable error list).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** New route, no other backend caller. Grepped
  `compute_route` — its only other caller is `routes/rides/booking.py`
  (live ride creation); this route is a read of that same function, not a
  modification to it, so live ride booking is unaffected.
- **Query scope is the same one used by the existing snapshot route**
  (`legacy_import_metadata IS NOT NULL`), so it only ever touches
  legacy-imported rides — never a live/organic ride.
- **Idempotent by default**: `force=false` (the default in the UI) only
  processes rides currently missing a real route, so re-running the tool
  after a partial run or a stall does not re-charge OSRM/Google Directions
  quota for rides already backfilled.
- **`success`/`failed` counters**: same cooperative-scheduling safety
  argument as the snapshot fix — asyncio coroutines never interleave at
  the bytecode level, so unlocked `+= 1` is safe across concurrently
  gathered tasks.
- All 98 tests in `tests/test_admin_rides_coverage.py` pass, including 6
  new tests for this route (403 check, no-rides-skips-audit, skips rides
  with a real existing route, happy path writes polyline+distance, no
  route from any provider counts as failed, and the concurrency proof).
- `npx tsc --noEmit` clean; `npm run build` succeeded,
  `/dashboard/bulk-operations` compiled without error.

## 5. User-experience effect

- **Internal admin only** (super_admin-gated), on the existing Bulk
  Operations page. Purely additive — a new section, no existing section's
  behavior changed. Before: no way to backfill `planned_route_polyline`
  for imported rides without shell access; the rider/driver app would show
  a route-less trip history entry. After: a button computes and writes the
  road route, matching what a live-created ride would have.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/rides.py` | New `POST /api/admin/rides/regenerate-imported-routes` route, `RegenerateRoutesRequest` model, `_ROUTE_CONCURRENCY = 8` constant | Provide a non-CLI, admin-UI path to backfill `planned_route_polyline`/`distance_km` for imported rides |
| `backend/tests/test_admin_rides_coverage.py` | New `TestAdminRegenerateImportedRoutes` class, 6 tests | Lock in the new route's contract and concurrency behavior |
| `admin-dashboard/src/lib/api/imports.ts` | New `adminRegenerateImportedRoutes()` client function + `RouteRegenerateResult` type | Frontend client for the new route |
| `admin-dashboard/src/lib/api.ts` | Re-export the two new symbols | Keep the barrel export in sync |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | New `RouteRegenerateSection` component + section header, mounted after the existing snapshot section | Give the operator a button to run the backfill |

## 7. Before / after

Before: no admin-dashboard control existed for this backfill — the only
path was the CLI script (`backend/scripts/backfill_imported_ride_routes.py`),
which needs shell + service-role credentials this session cannot use for a
live write.

```tsx
// After — new section in bulk-operations/page.tsx, mirrors the existing
// SnapshotRegenerateSection pattern exactly
<RouteRegenerateSection />
```

```python
# After — backend/routes/admin/rides.py
@router.post("/rides/regenerate-imported-routes")
async def admin_regenerate_imported_routes(body: RegenerateRoutesRequest, admin: dict = Depends(get_admin_user)):
    ...
    semaphore = asyncio.Semaphore(_ROUTE_CONCURRENCY)
    async def _process_one(ride):
        async with semaphore:
            result = await compute_route(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng)
            await db.update_one("rides", {"id": ride_id}, {"planned_route_polyline": result["polyline"], "distance_km": result["distance_km"]})
    await asyncio.gather(*(_process_one(r) for r in targets))
```

## 8. Rollback plan

`git-revert-safe` — the route is new and additive (no existing route or
column semantics changed). If a bad run writes wrong routes, the CLI
script or this same route re-run with `force=true` can be used to
recompute and overwrite `planned_route_polyline`/`distance_km` again; no
migration or data-repair SQL is needed since nothing else reads those two
columns for anything but display.

## 9. Verification performed

- [x] `pytest backend/tests/test_admin_rides_coverage.py` — 98 passed
      (6 new tests for this route, all passing; confirmed concurrency test
      fails without the semaphore via the same reasoning as today's earlier
      snapshot fix — bounded concurrency was built in from the start here,
      not retrofitted).
- [x] `ruff check` / `ruff format --check` — clean.
- [x] `npx tsc --noEmit` — clean.
- [x] `npm run build` (admin-dashboard) — real production build, succeeded,
      `/dashboard/bulk-operations` compiled.
- [x] Confirmed write shape against live production code
      (`routes/rides/booking.py`'s own `planned_route_polyline` write) —
      native list, not JSON-stringified.
- [x] Blast-radius grep: `compute_route`'s only other caller is
      `routes/rides/booking.py`; unaffected by this change.

## What was NOT verified

- Not yet run against real production — this is the operator's next step
  (clicking "Regenerate Routes" on the Bulk Operations page), after which
  production will be checked directly via SQL for all 62 rides (same
  verification rigor as every other step this session).
- No live OSRM/Google Directions network access in this environment to
  exercise `compute_route()` end-to-end; the 6 new tests mock
  `compute_route()` directly.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`; re-run with
      `force=true` also self-heals a bad run)
- [x] Blast radius is stated, not assumed (isolated new route + section;
      one other caller of the reused `compute_route()` helper, unaffected)
- [x] No silent behavior change — purely additive route + UI section
