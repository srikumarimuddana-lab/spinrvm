# Change Impact & Risk Log — extend pre-launch flagging to dormant riders

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (spinr platform) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `mvapps/blissful-thompson-u14frt` |
| Related issue or gap ID | Gap found in `docs/change-log/2026-09-10-a34-pre-launch-data-contamination-check.md`; ACTION_ITEMS.md A34 |

## 1. Issue / gap identified

`pre_launch_flag_service.py` (Migration Checklist tool #16) only ever flagged
dormant drivers and pre-launch rides — it never wrote to `users`. A live
investigation on 2026-09-10 found 1,046 of 1,132 legacy-imported rider
accounts (92%) have never taken a single ride in Spinr, with no way to
identify that population on the admin dashboard short of a one-off manual
database query.

## 2. Root cause

The tool was built and broadened (2026-08-30/31) with drivers and rides in
mind; riders were never in scope, not because of a technical blocker but
because the gap wasn't surfaced until the 2026-09-10 investigation above.

## 3. Fix / remediation

Extended the same additive, zero-activity-gated pattern already used for
drivers to riders:

- `pre_launch_flag_service.py`: new `_fetch_pre_launch_rider_candidates()` —
  a legacy-imported rider (`legacy_import_metadata->>'rider_csv_import'`)
  with zero rides ever is a candidate. Riders have no
  `driver_insurance_periods` equivalent, so zero-rides-ever is the only
  activity signal (drivers additionally check insurance periods).
  `created_at` is not used as a gate for riders, mirroring why it was
  already dropped for drivers — riders' import doesn't preserve the
  original old-app signup date reliably either.
- `PreLaunchFlagPlan.rider_candidates`, wired through
  `build_pre_launch_flag_plan`/`apply_pre_launch_flags`/`print_report`.
  `apply_pre_launch_flags`'s conflict dict gained a `"users"` key.
- `routes/admin/pre_launch_flag.py`: preview/commit responses and the
  admin-action audit log now carry `rider_candidates`/`riders_flagged`/
  `rider_conflicts` alongside the existing driver/ride fields.
- `migration_status_service.py`'s tool #16 status text now counts flagged
  riders too (`_count("users", ...)`).
- `admin-dashboard`'s `PreLaunchDataFlag.tsx` + `lib/api/imports.ts`: added
  the rider stat tile, updated the copy-summary text, the safety note, and
  the confirm/result strings.

No new endpoint, no new confirm phrase, no new admin-permission model — this
reuses every existing plumbing path exactly as drivers/rides already do.

## 4. Risk & impact on existing functionality

- **Blast radius: narrow, additive.** `fetch_pre_launch_flagged_ids(table)`
  was already table-generic (used today with `"drivers"` in
  `routes/admin/drivers.py` and `"rides"` in `routes/admin/rides.py`) — no
  change needed there, and calling it with `"users"` in the future (e.g. a
  future riders admin list filter) would work today without further code
  changes, though no such filter is added in this change. Grepped
  `pre_launch_flag_service`/`build_pre_launch_flag_plan`/
  `apply_pre_launch_flags`/`fetch_pre_launch_flagged_ids` repo-wide before
  starting: only the driver/ride list filters (unaffected, table-generic
  already), the admin route, the migration-status tool, and this module's
  own tests/docstrings reference these symbols. `migration_data_quality_service.py`
  and `migration_driver_repair_service.py` only mention this module in
  design-precedent comments, not actual calls.
- The existing `conflicts == {"drivers": [], "rides": []}` shape gained a
  `"users"` key — every call site inside this module was updated
  accordingly; no external caller destructures that dict (checked via grep,
  it's only consumed inside `pre_launch_flag_service.py` and
  `routes/admin/pre_launch_flag.py`, both updated in this change).
- No interaction with the ride state machine, insurance periods, or any
  money/wallet path. `users` writes are additive-only (one new JSONB key),
  same pattern already proven safe for `drivers`/`rides` in production.

## 5. User-experience effect

- Internal admin only (Migration Checklist / Bulk Operations page). No
  rider, driver, or corporate-admin-facing change.
- Not visible mid-session to a rider using the app — a flagged rider's
  account, login, or ride history is unaffected; the flag lives only in an
  internal JSONB field on `legacy_import_metadata` that no rider-facing
  code path reads.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/pre_launch_flag_service.py` | New `_fetch_pre_launch_rider_candidates`, `rider_candidates` on the plan, wired into build/apply/print, docstring updated | Core rider candidacy logic |
| `backend/tests/test_pre_launch_flag_service.py` | 5 new tests + updated conflict-dict assertions | Cover rider candidacy and the widened conflicts shape |
| `backend/routes/admin/pre_launch_flag.py` | `_report`/commit now include rider fields | Surface rider counts over HTTP + audit log |
| `backend/tests/test_admin_pre_launch_flag.py` | 2 new endpoint tests | Cover the HTTP-layer rider path |
| `backend/services/migration_status_service.py` | Tool #16 status counts flagged riders | Keep the Migration Checklist panel accurate |
| `backend/tests/test_migration_status_service.py` | Updated + 1 new test | Cover the new detail-string shape |
| `admin-dashboard/src/lib/api/imports.ts` | Added rider fields to the two report/result interfaces | Type the new backend fields |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/PreLaunchDataFlag.tsx` | Added rider stat tile, safety-note copy, summary/result text | Surface rider counts in the UI |
| `docs/runbooks/migration-tool-order.md` | Tool #16 row + a dated note | Document the dependency on #3 and this change |
| `ACTION_ITEMS.md` | A34 addendum updated | Mark the recommendation as implemented |

## 7. Before / after

```
# Before
def build_pre_launch_flag_plan() -> PreLaunchFlagPlan:
    plan.driver_candidates = _fetch_pre_launch_driver_candidates()
    plan.ride_candidates = _fetch_pre_launch_ride_candidates()
    plan.stats = {"driver_candidates": ..., "ride_candidates": ...}
```

```
# After
def build_pre_launch_flag_plan() -> PreLaunchFlagPlan:
    plan.driver_candidates = _fetch_pre_launch_driver_candidates()
    plan.ride_candidates = _fetch_pre_launch_ride_candidates()
    plan.rider_candidates = _fetch_pre_launch_rider_candidates()
    plan.stats = {"driver_candidates": ..., "ride_candidates": ..., "rider_candidates": ...}
```

## 8. Rollback plan

- Code: `git revert` is sufficient — no other code path depends on the new
  `rider_candidates`/`"users"` plumbing added here.
- Data: if a bad batch is committed, the same rollback migration 328's
  wallet/crosswalk tools already document applies here — the flag write
  itself has no dedicated rollback SQL since it's a plain additive JSONB
  key, but nothing reads `pre_launch_test` to make an irreversible
  downstream decision (it's a display/filter flag only), so a bad flag can
  simply be corrected with a scoped `UPDATE ... SET legacy_import_metadata
  = legacy_import_metadata - 'pre_launch_test' - 'pre_launch_flag' WHERE
  legacy_import_metadata->'pre_launch_flag'->>'batch' = '<batch>'` if ever
  needed — no such correction has been required for the drivers/rides
  version of this tool in production to date.
- No feature flag added — same justification as the original tool: fully
  additive-insert-only, gated behind `super_admin`, and reuses a pattern
  already proven safe in production for two other tables.

## 9. Verification performed

- [x] Automated tests run — unit only: `pytest backend/tests/test_pre_launch_flag_service.py`
  (22/22 passed), `pytest backend/tests/test_admin_pre_launch_flag.py` (9/9
  passed), `pytest backend/tests/test_migration_status_service.py` (31/31
  passed). `ruff check`/`ruff format` clean via the repo's pre-commit hook
  on all touched Python files.
- [x] **Real production build run for the admin-dashboard change**: `npx tsc
  --noEmit -p .` — clean, and `npx eslint` on both touched frontend files —
  clean, 0 errors/warnings. A full `npm run build` was **not** run (not
  available in this environment) — per CLAUDE.md's own distinction, a clean
  `tsc --noEmit` is explicitly *not* equivalent to a real production build;
  stating that gap explicitly rather than implying full coverage.
- [ ] Manual repro steps followed in staging — **not performed**. No live
  Supabase or staging admin-dashboard access in this environment; verified
  by unit tests against the mocked Supabase fake and static analysis only.
- [x] Blast-radius grep performed — see section 4 above.
- [x] Reviewed against relevant CLAUDE.md conventions — dual-import pattern
  followed (this module already used it, unchanged); task decomposed into 4
  commits, each ≤ ~130 changed lines; loud-error-logging convention
  unaffected (no new error path added).
- [x] Feature-flagged if user-visible and non-trivial (or justify why not) —
  see Rollback plan above.

## What was NOT verified

- Not run against live production — the actual `preview`/`commit` HTTP
  calls, and therefore the real live rider-candidate count this would flag,
  were not exercised against the real `spinrmobileapp` Supabase project in
  this pass. The 1,046-dormant-rider figure cited above comes from the
  read-only investigation in
  `docs/change-log/2026-09-10-a34-pre-launch-data-contamination-check.md`,
  not from running this new code path live.
- No visual/screenshot verification of the updated `PreLaunchDataFlag.tsx`
  component — Bulk Operations is not one of the 6 seeded visual-regression
  pages (`docs/change-log/...` visual-regression list in CLAUDE.md), and no
  dev server was reachable from this session. Reasoned about via `tsc`/
  `eslint` only, per this repo's "reasoned about, not screenshotted"
  disclosure convention.
- Whether an admin actually running this tool's commit button against
  production riders would surface any unexpected edge case in the real
  `rider_csv_import` metadata shape (e.g. malformed JSON) beyond what the
  existing driver-side tests already establish for that shape's general
  reliability — not independently re-verified for the rider-specific key.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (scoped metadata-key removal,
  or a full `git revert` for the code)
- [x] Blast radius is stated, not assumed (isolated, reuses existing
  table-generic helpers, only 2 external readers of `fetch_pre_launch_flagged_ids`
  and neither is affected)
- [x] No silent behavior change to an already-shipped flow — this is new,
  additive functionality only; the existing driver/ride flagging behavior
  is unchanged (same candidacy logic, same output fields still present)
