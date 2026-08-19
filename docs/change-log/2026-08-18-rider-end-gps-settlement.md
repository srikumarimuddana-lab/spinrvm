# Rider-end / admin-complete rides now settle GPS geometry (+ backstop sweep)

**Date:** 2026-08-18
**Surface:** rides (live-tested), insurance audit
**Trigger:** ride SPR-PE7TTB (`f3fe8061-bd11-4f4f-9758-83845c60efa6`) completed with no actual route.

## Issue/gap identified
Rides completed via the rider "end ride early" flow or admin force-complete skipped GPS settlement entirely: no breadcrumb aggregation, no `ride_routes` row, route finalizer never queued, no P2/P3 `driver_period_distances` audit rows — even when breadcrumbs existed (SPR-PE7TTB had 51 points stored but `gps_points_count=0` on the ride).

## Root cause
`routes/rides/lifecycle.py::rider_complete_ride` and `routes/admin/rides.py::admin_complete_ride` each re-implemented the completion status flip without the geometry-settlement block that only lived inline in `routes/drivers/ride_complete.py`. Its docstring even said "skipping GPS aggregation" — the gap was designed in, and nothing swept for missed settlement afterward.

## Fix/remediation
- New `backend/utils/ride_settlement.py::settle_completed_ride_geometry(ride_id, trigger=…)` — standalone, replay-safe, never-raises settlement for already-completed rides: breadcrumb flush → `compute_trip_distances` → `ride_routes` legacy payload upsert → rides geometry-only fields → P2/P3 period-distance audit → `mark_route_pending` (v2 finalizer queue). Metric `spinr_rides_geometry_settled_total{trigger,outcome}`.
- `rider_complete_ride` spawns it fire-and-forget after the atomic status flip (wrapped; a spawn failure is recovered by the sweep).
- `admin_complete_ride` awaits it (not latency-critical; file has no strong-ref spawn helper).
- Backstop: `route_finalizer_loop` sweeps every ~5 min for completed rides (7-day lookback, 5-min grace, ≤25/sweep) with no `ride_routes` row and settles them — heals history (incl. SPR-PE7TTB) and any future missed writer.
- The driver completion path is **unchanged** (kept inline; its tests patch module attributes and assert on source text, so extraction was deliberately avoided — the shared math already lives in `trip_distance`/`period_distance_audit`/`route_finalizer`; a drift note sits in the util docstring).

## Risk & impact on existing functionality
Blast radius of the shared writers (all pre-existing, all idempotent):
- `ride_routes` — readers: `ride_repo._project_route_detail` (rider/driver/admin ride detail), admin map modal, `email_receipt._await_route_receipt_projection`, `route_finalizer` (claims `processing_status='pending'`). New rows for rider-/admin-completed rides now appear where before there were none; readers already handle every `processing_status`.
- `driver_period_distances` — reader: `routes/admin/compliance.py` insurer billing (periods 2,3). Rider-/admin-completed rides now produce billable-audit rows they previously lacked. Partial unique `(ride_id, period)` makes concurrent double-writes fail closed.
- `rides` geometry columns (`actual_distance_km`, `phase_distances`, `phase_durations`, `gps_points_count`, `route_quality`, `route_geometry_status/_error`) — readers: admin daily-activity/detail, rider `ride-details`. **Never writes** `distance_km`, fare fields, `status`, `payment_status`, `ride_metrics` — rider-end full-fare policy and admin `waived_admin` are untouched.
- `route_finalizer_tick()` signature untouched; the sweep runs at loop level only. `test_coverage_rides.py` spawn-count test updated (settlement is a new first spawn in `rider_complete_ride`).

## User experience effect
Riders who end a ride early now get an actual-route map and measured distance on the completed/history screens (previously "Actual route unavailable"/planned preview). No mid-session change for anyone currently in a ride: settlement only runs after `completed`. Fares unchanged everywhere.

## Files modified
| file | what changed | why |
|---|---|---|
| `backend/utils/ride_settlement.py` | new standalone settlement util | shared by rider-end/admin/backstop |
| `backend/routes/rides/lifecycle.py` | spawn settlement after status flip; docstring corrected | rider-end rides get routes/audit |
| `backend/routes/admin/rides.py` | await settlement in force-complete | admin-completed rides get routes/audit |
| `backend/utils/route_finalizer.py` | `sweep_unsettled_completed_rides` + loop wiring | heal history + future missed writers |
| `backend/tests/test_ride_settlement.py` | new unit tests (7) | contract + SPR-PE7TTB regression anchor |
| `backend/tests/test_coverage_rides.py` | spawn-count 2→3 in quest-failure test | settlement is a new first spawn |

## Before/after snippet
Before (`lifecycle.py::rider_complete_ride` — nothing after the status flip touched GPS):
```python
    guard = await _deps.db_supabase.update_one(
        "rides", {"id": ride_id, "status": RideStatus.IN_PROGRESS}, update_fields
    )
    ...
    # (no aggregation, no ride_routes, no period audit, no finalizer queue)
```
After:
```python
    guard = await _deps.db_supabase.update_one(
        "rides", {"id": ride_id, "status": RideStatus.IN_PROGRESS}, update_fields
    )
    ...
    _deps.spawn(settle_completed_ride_geometry(ride_id, trigger="rider_end"))
```

## Rollback plan
No migration, no flag needed: revert the two call-site edits (lifecycle/admin) and the sweep block in `route_finalizer.py` — the util then has no callers. Data already written is additive (new `ride_routes`/audit rows) and matches what the driver path would have written; no live-data mutation to unwind. If only the sweep misbehaves, set `BACKSTOP_BATCH_LIMIT = 0` is not needed — revert the loop block alone; the two call sites are independent.

## Verification performed
- `pytest tests/test_ride_settlement.py tests/test_route_finalizer.py` — 18 passed.
- `pytest tests/test_coverage_rides.py tests/test_admin_extended.py tests/test_quests.py tests/test_earnings_snapshot.py` — 276 passed (1 pre-existing spawn-count test updated for the new spawn).
- Production Supabase forensics of SPR-PE7TTB (read-only) established the exact missing artifacts this fills.
- No frontend build required (backend-only change).

## What was NOT verified
- Not run against live Supabase — unit tests mock the DB layer per repo convention.
- The backstop sweep's PostgREST range/`$in` filters were verified against `repositories/_base.py` operator support but not against a live PostgREST instance.
- OSRM/Google route providers not exercised (aggregation falls back to planned distance exactly as the driver path does when providers fail).
- SPR-PE7TTB itself will be healed by the first production sweep (~5 min after deploy); its route will be partial (51 points covering the first 2.3 min) — the capture-side loss is a separate driver-app fix (plan Phase 0.8).
