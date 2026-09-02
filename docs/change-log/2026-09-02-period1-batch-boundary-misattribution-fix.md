# 2026-09-02 — Period-1 deadhead batch boundary misattribution fix

## Issue/gap identified

`routes/drivers/location.py`'s v1 REST location-batch endpoint (the
WS-down / background-tracking fallback path) accumulated Period-1
("online, no active ride") deadhead distance for an entire incoming GPS
batch based on a single, whole-batch check of whether the driver
currently has an active ride — not on each point's own capture time.

## Root cause

`update_location_batch` fetches `resolve_active_ride(driver_id)` once per
request and either accumulates the *whole* batch as Period 1 (no active
ride) or drops the *whole* batch (an active ride exists). A batch queued
locally during a connectivity blip (app backgrounded, network drop) and
flushed on reconnect can straddle the moment the driver's ride was
assigned — some points genuinely pre-date `assigned_at` (real Period-1
deadhead) while later points in the same batch postdate it (Period 2).
The whole-batch check silently discarded the entire batch once any ride
was active by flush time, including the legitimate pre-assignment
distance — an undercount of the driver's Period-1 exposure at exactly the
regulatory Period 1→2 boundary this session's earlier fixes (see
`2026-09-02-insurance-period-window-precedence-fix.md`) were tightening.

The v2 idle-batch path (`utils/breadcrumbs.py::persist_idle_location_batch`)
already does this correctly — it rejects individual points at/after
`ride_window_start` (`ride_active` rejection reason) rather than gating
the whole batch — so this was an asymmetry between the two ingestion
paths, not a design gap in the Period-1 model itself.

## Fix/remediation

`update_location_batch`'s Period-1 accumulation block now mirrors the v2
path's boundary logic: when an active ride exists, compute
`ride_window_start` with the same precedence used everywhere else in this
codebase (`assigned_at` → `driver_accepted_at` → `created_at`), then keep
only the points captured strictly before that boundary before summing
distance via `batch_incremental_distance_km`. If the ride carries no
usable timestamp at all, the batch is dropped (fail safe — no guessing),
matching the previous behavior for that edge case.

## Risk & impact on existing functionality

- **Blast radius**: isolated to the Period-1 accumulation block inside
  `update_location_batch` (v1 REST path only). The v2 idle/trip paths,
  `period1_distance_finalizer.py`, and `driver_period_distances` writes
  are untouched — this only changes which *subset* of an already-fetched
  batch is fed into the existing `batch_incremental_distance_km` call.
- Grepped for other callers of `batch_incremental_distance_km`: only this
  call site and `breadcrumbs.py::_persist_v2_idle_batch` (already correct)
  call it. No other consumer affected.
- `points` (the full, unfiltered batch) is still used unchanged for the
  live marker update and the raw `driver_location_history` write earlier
  in the function — only the Period-1 scalar-accumulator delta is
  computed off the filtered subset.
- Net effect is **additive-in-accuracy**: this can only *increase*
  `period1_accum_km` versus before (previously-dropped pre-assignment
  points now count), never decrease it for a batch that was already
  accumulating. No existing driver-facing behavior changes.

## User experience effect

None visible. `period1_accum_km`/`driver_period_distances` are
audit-only figures (SGI insurance-period regulatory trail); no rider,
driver, or admin UI reads them directly today.

## Files modified

| File | What changed | Why |
|---|---|---|
| `backend/routes/drivers/location.py` | Period-1 accumulation now filters the batch to points before `ride_window_start` instead of an all-or-nothing check on whether a ride is currently active | Boundary misattribution fix (above) |
| `backend/utils/live_breadcrumbs.py` | Removed dead `MAX_BREADCRUMB_AGE_SECONDS` constant (zero references anywhere in the codebase; sibling `MAX_BREADCRUMB_POINTS` is actively used) | Cleanup, no behavior change |
| `backend/tests/test_period1_accumulation_endpoint.py` | Added 3 regression tests: batch straddling assignment counts only the pre-assignment leg, batch entirely after assignment counts nothing, ride with no usable timestamp fails safe | Test coverage for the fix |

## Before/after snippet

```python
# before
if _p1_active is None:
    _p1_delta = batch_incremental_distance_km(points)
    ...

# after
_p1_points = points
if _p1_active is not None:
    _ride_window_start = (
        parse_iso_utc(_p1_active.get("assigned_at"))
        or parse_iso_utc(_p1_active.get("driver_accepted_at"))
        or parse_iso_utc(_p1_active.get("created_at"))
    )
    if _ride_window_start is None:
        _p1_points = []
    else:
        _boundary_epoch = _ride_window_start.timestamp()
        _p1_points = [p for p in points if (point_epoch_seconds(p) or 0) < _boundary_epoch]
if _p1_points:
    _p1_delta = batch_incremental_distance_km(_p1_points)
    ...
```

## Rollback plan

`git revert` is safe here — this is pure code (no migration, no data
mutation, no flag). Reverting restores the previous whole-batch
gate, which fails toward *undercounting* Period-1 distance (the
pre-existing, already-shipped behavior), never toward overcounting or
misclassifying billed/settled distance. `period1_distance_tracking_enabled`
remains the existing kill switch if the whole feature needs to go dark.

## Verification performed

- `ruff check` on both changed files: no new findings (the one pre-existing
  `B904` finding in `location.py` is unrelated, on a different function).
- `ruff format --diff`: no changes needed.
- `pytest tests/test_period1_accumulation_endpoint.py -q` — 8/8 pass
  (5 pre-existing + 3 new).
- `pytest tests/test_location_batch.py tests/test_idle_location_batch.py tests/test_period1_distance_finalizer.py tests/test_period1_distance_finalizer_coverage.py tests/test_p3_background_location.py -q` — 46 passed, 1 pre-existing xfail.
- Manual trace of `point_epoch_seconds` (`utils/gps_filtering.py`) confirmed
  it accepts every timestamp shape the v1 REST payload's points actually
  carry (`ts`/`captured_at`/`device_timestamp`/`recorded_at`/`timestamp`,
  ISO string or epoch).
- No production build applicable — backend-only change, no
  `admin-dashboard`/`rider-app`/`driver-app` files touched.

## What was NOT verified

- Not exercised against a real Supabase instance or the actual driver
  app's outbox — only against `mock_supabase_client`-style mocks in the
  new/existing unit tests.
- `period1_distance_tracking_enabled` is off by default in production
  today, so this path has no live traffic to observe pre/post; the fix is
  verified by test, not by production telemetry.
- The magnitude of the previously-dropped distance in production (i.e.
  how often a real batch actually straddles an assignment boundary) is
  unmeasured — no query was run against live data for this session.
