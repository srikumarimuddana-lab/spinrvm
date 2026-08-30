# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Follow-up to `docs/change-log/2026-08-30-pre-launch-flag-tool.md` |

## 1. Issue / gap identified

The pre-launch flag tool (previous change) set
`legacy_import_metadata.pre_launch_test = true` on 310 dormant drivers and
25 rides, but no admin view could filter on it — the flag was purely
additive with no reader. The owner asked for a filter to hide (or, for QA,
isolate) flagged rows in the Drivers and Rides admin list views.

## 2. Root cause

Expected — the flag tool was built explicitly scoped to "flag only" per the
owner's own earlier decision; using the flag was flagged as a deliberate
follow-up, not an oversight. This change is that follow-up.

## 3. Fix / remediation

- `services/pre_launch_flag_service.py`: new `fetch_pre_launch_flagged_ids(table)`
  — read-only, returns the set of ids in `drivers`/`rides` currently
  flagged. Exported for reuse (not duplicated) by both list routes, so
  every reader of "flagged" agrees with the writer's own definition
  (`PRE_LAUNCH_FLAG_KEY`).
- `routes/admin/drivers.py` (`admin_get_drivers`) and `routes/admin/rides.py`
  (`admin_get_rides` / `_build_rides_filters`): new `pre_launch: Optional[bool]`
  query param. `True` = flagged rows only, `False` = hide flagged rows,
  omitted (default) = no filter, byte-identical to every prior request.
  The generic filter DSL (`repositories/_base.py`) has no JSONB-path
  operator, so flagged ids are resolved via the new helper first, then fed
  into the DSL's existing `$in`/`$nin` operators against `id` — same
  two-step pattern the `photo_status` filter already uses in
  `admin_get_drivers` for a cross-table lookup.
- Admin-dashboard: new "Pre-launch flag" filter dropdown on both the
  Drivers page (next to the existing Legacy Import filter, matching its
  exact UI shape) and the Rides page (next to the Service Area filter).
  Default `"all"` (no filter) on every page load.

**Edge cases handled explicitly, not left to chance:**
- `pre_launch=true` with zero flagged rows: drivers route returns `[]`
  immediately without querying the drivers table at all; rides route
  passes an empty `$in: []`, which PostgREST compiles to `id=in.()` —
  matches zero rows (verified, not assumed to be safe).
- `pre_launch=false` with zero flagged rows: no `id` filter is added at
  all (not a vacuous `$nin: []`), so the query is identical to omitting
  the param — avoids a filter that could be misread as "exclude
  everything" if `$nin` semantics were ever misremembered.

## 4. Risk & impact on existing functionality

- **Blast radius, checked directly**: `getDrivers`/`getRides` (the admin-
  dashboard client functions) are also called from
  `driver-license-backfill/page.tsx`, `drivers/decals/page.tsx`, and
  `lib/__tests__/api.test.ts` — all three call sites omit the new
  `pre_launch`/`preLaunch` option entirely, so they are unaffected (a new
  optional field on an options object never breaks an existing caller that
  doesn't set it).
- **No existing filter behavior changed.** Every other filter
  (`legacy_import`, `service_area_id`, `status`, `search`, dates, sort) is
  untouched; `pre_launch` only ever adds a new, independent `id`
  `$in`/`$nin` clause, ANDed with whatever else is active — confirmed via
  the drivers route's own comment that `filters["id"]` is otherwise unused
  by that route (no key collision).
- **No silent default-view change**: both dropdowns default to "All" (no
  filter) — an admin who has never touched this control sees the exact
  same drivers/rides list as before this change shipped.
- **rides.py's `admin_export_filtered_rides` was deliberately left
  unwired** — `_build_rides_filters`'s new `pre_launch` param defaults to
  `None`, so CSV export behavior is unchanged; the owner's ask was scoped
  to the list *views*, not the export path.

## 5. User-experience effect

Admin-facing only (Drivers and Rides list pages). A super_admin or any
admin with the `drivers`/`rides` module can now filter either list to hide
or isolate pre-launch test data. No rider/driver-facing change. Nothing
changes for an admin who doesn't touch the new dropdown.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/pre_launch_flag_service.py` | New `fetch_pre_launch_flagged_ids()` | Shared flagged-id lookup for both list routes |
| `backend/routes/admin/drivers.py` | New `pre_launch` query param + filter compilation | Drivers list filter |
| `backend/routes/admin/rides.py` | New `pre_launch` query param + filter compilation | Rides list filter |
| `backend/tests/test_admin_drivers_coverage.py` | 5 new tests (`TestAdminGetDriversPreLaunchFilter`) | Lock in the filter's compilation + edge cases |
| `backend/tests/test_admin_rides_coverage.py` | 5 new tests (`TestAdminGetRidesPreLaunchFilter`) | Same, for the rides route |
| `admin-dashboard/src/lib/api/drivers.ts` | New `pre_launch` option + query param | Client for the new backend param |
| `admin-dashboard/src/lib/api/rides.ts` | New `preLaunch` option + query param | Same, for rides |
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | New filter state + dropdown | Drivers page UI |
| `admin-dashboard/src/app/dashboard/rides/page.tsx` | New filter state + handler + prop wiring | Rides page state |
| `admin-dashboard/src/app/dashboard/rides/_components/ride-list.tsx` | New prop pair + dropdown | Rides page UI |

## 7. Before / after

```python
# Before (routes/admin/drivers.py)
if legacy_import is not None:
    filters["legacy_import_metadata"] = {"$ne": {}} if legacy_import else {"$eq": {}}

# See the "Driver search" block above the route for the two-query design.
```

```python
# After
if legacy_import is not None:
    filters["legacy_import_metadata"] = {"$ne": {}} if legacy_import else {"$eq": {}}

if pre_launch is not None:
    flagged_ids = fetch_pre_launch_flagged_ids("drivers")
    if pre_launch:
        if not flagged_ids:
            return []
        filters["id"] = {"$in": list(flagged_ids)}
    elif flagged_ids:
        filters["id"] = {"$nin": list(flagged_ids)}

# See the "Driver search" block above the route for the two-query design.
```

## 8. Rollback plan

`git revert` — purely additive query params and UI controls; no data
change, no schema change, nothing to undo beyond the code itself. The
underlying flag data (from the prior change) is unaffected regardless.

## 9. Verification performed

- [x] `pytest tests/test_admin_drivers_coverage.py tests/test_admin_rides_coverage.py tests/test_pre_launch_flag_service.py tests/test_admin_pre_launch_flag.py` — 269 passed, 0 regressions.
- [x] `ruff check` / `ruff format --check` on every touched Python file — clean (the only findings were 4 pre-existing, unrelated `B904` warnings in `drivers.py` far from this diff, confirmed via `git diff` that this change didn't touch those lines).
- [x] `npx tsc --noEmit` — clean.
- [x] `npm run build` (admin-dashboard) — real production build, succeeded, both pages compiled.
- [x] Blast-radius grep: every other caller of `getDrivers`/`getRides` confirmed to omit the new option (unaffected).
- [x] Edge cases (zero flagged rows, both directions) explicitly tested, not assumed.

## What was NOT verified

- Not run against real production — the operator can try the new filter on
  the live Drivers/Rides pages once this deploys; the 310/25 flagged rows
  from the prior change are already live, so the filter should show them
  immediately.
- CSV export (`admin_export_filtered_rides`) does not support the new
  filter — deliberately out of scope, not a gap discovered late.
- No visual regression tooling exists for admin-dashboard (per CLAUDE.md's
  standing note — zero committed baselines) — the new dropdowns' visual
  placement was reasoned about against the existing `legacyFilter`
  Select's exact styling, not screenshotted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data change)
- [x] Blast radius is stated, not assumed (every other caller checked)
- [x] No silent behavior change to any existing flow — both filters default
  to "no filter", identical to every page load before this change
