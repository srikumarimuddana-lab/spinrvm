# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (this branch) |
| Related issue or gap ID | Reported live by user against driver Alexander Gavu's Activity screen; related to A30 (`docs/audit/2026-08-13-migrated-data-visibility-audit.md`) Finding 3 but a distinct bug |

## 1. Issue / gap identified

A migrated driver's Activity screen ("All Time") showed `Total Earned $0.00`,
`0 Total Trips`, `0.0 KM Driven`, `0h Online Time`, `Avg per Trip $0.00` —
while the ride list directly below rendered 17 real completed rides with
real fares.

## 2. Root cause

`GET /drivers/earnings` (`backend/routes/drivers/earnings.py`) fetches
completed rides with `EXCLUDE_LEGACY_RIDES` applied (a filter that,
per `backend/utils/legacy_rides.py`'s own docstring, exists to keep
previous-app money out of Spinr earnings totals — Finding 3 of A30, by
design). The bug: `total_rides`, `total_distance_km`, and
`total_duration_minutes` — none of which are money — were summed from that
same legacy-excluded `rides` list. A driver whose completed rides in the
selected period are entirely legacy-imported (e.g. "All Time" for anyone
who migrated before their first Spinr-native trip) got an empty `rides`
list, so every one of those three fields, plus the money fields, read zero
— even though `utils/legacy_rides.py` is explicit that imported rides
"remain fully visible in ride history; this module only governs money
math." The trip-count/distance/duration stats never should have gone
through the money-only filter.

## 3. Fix / remediation

`get_driver_earnings` now runs a second, unfiltered "all completed rides in
period" query and sources `total_rides`/`total_distance_km`/
`total_duration_minutes` from it. `average_per_ride` is deliberately left
divided by the money-rides count (not the new unfiltered count) — averaging
real earnings across trips that paid $0 in this app would produce a
misleadingly diluted number; it now explicitly reads `len(rides)` rather
than reusing the `total_rides` key. All money fields (`total_earnings`,
`total_tips`, `total_incentives`, `total_bonuses`, `total_tax`) are
untouched and continue to exclude legacy rides, per design.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one endpoint** (`GET /drivers/earnings`,
  `backend/routes/drivers/earnings.py:get_driver_earnings`). Grepped every
  consumer:
  - driver-app `store/driverStore.ts` `fetchEarnings()` → `earnings` /
    `earningsByPeriod` state, consumed by:
    - `components/activity/ActivityView.tsx` — the screen in the bug
      report; now shows the real trip/distance/duration counts.
    - `components/dashboard/DriverTopBar.tsx` — "today's trips" pill; same
      fix applies (it reads `earnings?.total_rides`).
  - No other backend module imports `get_driver_earnings` or reuses its
    query composition; `/drivers/balance` (`get_driver_balance`) has an
    independent, still-buggy `total_rides = len(rides)` on the same
    legacy-excluded set, but its response field is unused by any frontend
    today (`DriverBalance` TS type in `driverStore.ts` has no `total_rides`
    field) — left alone to keep this fix scoped; noted as a follow-up gap
    below rather than fixed silently.
  - `/drivers/earnings/daily`, `/weekly`, `/monthly`, `/comparison`,
    `/forecast`, `/trips` are separate functions with their own queries —
    not touched, not affected.
- One extra `db_supabase.get_rows("rides", ...)` call per `/earnings`
  request (same table, same driver, no `EXCLUDE_LEGACY_RIDES` filter). Not
  expected to be perf-significant — this endpoint already made a comparable
  rides call plus incentive-claims/cancelled-rides/bonus lookups per
  request; adds one more bounded (`limit=10000`) query of the same shape.
- No ride state, money computation, or WebSocket path touched. No new
  write. No migration.

## 5. User-experience effect

- **Driver-facing.** Any driver with legacy-imported rides in the selected
  Activity period sees accurate trip count / distance / online time instead
  of zeros. No effect for drivers with zero legacy rides (the extra query
  returns the same rows as before, so nothing changes for them).
- Not visible mid-session in a way that could confuse an active trip —
  Activity/earnings is a historical summary screen, not something read
  during dispatch or an in-progress ride.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/earnings.py` | `get_driver_earnings`: added a second, unfiltered `all_completed_rides` query; `total_rides`/`total_distance_km`/`total_duration_minutes` now source from it instead of the legacy-excluded `rides` list; `average_per_ride` now explicitly divides by `len(rides)` | Fix the reported all-zero Activity stats for drivers whose period rides are entirely legacy-imported, while keeping money totals legacy-excluded by design |
| `backend/tests/test_earnings_coverage.py` | Added `TestGetDriverEarningsLegacyActivityStats` (all-legacy and mixed-legacy-plus-real cases) | Regression coverage for this exact bug |

## 7. Before / after

```python
# Before
rides = await db_supabase.get_rows("rides", filters, limit=10000)  # EXCLUDE_LEGACY_RIDES applied
...
stats = {
    ...
    "total_rides": len(rides),
    "total_distance_km": sum(r.get("distance_km", 0) or 0 for r in rides),
    "total_duration_minutes": sum(r.get("duration_minutes", 0) or 0 for r in rides),
}
...
"average_per_ride": (
    _money_str(_total_with_extras / stats.get("total_rides", 1)) if stats.get("total_rides", 0) > 0 else "0.00"
),
```

```python
# After
rides = await db_supabase.get_rows("rides", filters, limit=10000)  # EXCLUDE_LEGACY_RIDES applied (money only)
...
all_completed_rides = await db_supabase.get_rows("rides", _activity_filters, limit=10000)  # no legacy exclusion
...
stats = {
    ...
    "total_rides": len(all_completed_rides),
    "total_distance_km": sum(r.get("distance_km", 0) or 0 for r in all_completed_rides),
    "total_duration_minutes": sum(r.get("duration_minutes", 0) or 0 for r in all_completed_rides),
}
...
"average_per_ride": (_money_str(_total_with_extras / len(rides)) if len(rides) > 0 else "0.00"),
```

## 8. Rollback plan

Plain `git revert` — no data mutation, no migration, no Stripe/wallet
interaction. The endpoint is read-only (a `GET`), so a revert takes effect
on the very next request with zero cleanup.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_earnings_coverage.py backend/tests/test_drivers_extended.py -q` — 134/134 pass (2 new)
- [ ] Manual repro steps followed in staging — not available in this
      session; reasoned from the reported screenshots and the query code
      instead (see "What was NOT verified")
- [x] Blast-radius grep performed: `grep -rn "total_rides\|EXCLUDE_LEGACY_RIDES" backend/routes backend/services driver-app/store driver-app/components` — findings listed in §4
- [x] Reviewed against relevant CLAUDE.md convention: "Do not silently
      swallow errors" (n/a here, no error path changed);
      `utils/legacy_rides.py`'s own stated contract ("only governs money
      math") is the standard this fix restores
- [ ] Feature-flagged — not applicable; this is a pure bugfix restoring
      already-intended behavior (the docstring's contract), not a new
      user-visible feature, and it's a `GET`-only read-path fix

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no data touched)
- [x] Blast radius is stated: isolated to `GET /drivers/earnings`'s two
      driver-app consumers (ActivityView, DriverTopBar); `/drivers/balance`
      has the same latent bug but is unused by any frontend field today —
      left as a named follow-up, not silently ignored
- [x] No silent behavior change to an already-shipped flow beyond what's
      described in §5 (drivers with legacy rides now see correct, not
      zeroed, activity stats)
