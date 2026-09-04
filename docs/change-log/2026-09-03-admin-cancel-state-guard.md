# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude Code (WS-1 executing session) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch, rides, safety (insurance) |
| PR / commit link | (attached to the WS-1 PR) |
| Related issue or gap ID | #1 Blocker, `docs/audit/2026-09-03-engineering-director-teardown-round2.md`; `plans/2026-09-03-path-to-a-implementation-plan.md` WS-1 subtask A |

## 1. Issue / gap identified

`POST /api/admin/rides/{id}/cancel` (`admin_cancel_ride`) accepted an
`in_progress` ride — its only guard was a deny-list of `("completed",
"cancelled")`. An admin could force-"cancel" a ride with a passenger
aboard, which never closes the ride's insurance Period 3 window (that only
happens via `admin_complete_ride` or the normal trip-end flow) and violates
`CLAUDE.md`'s ride state machine invariant: "Transitions from `in_progress`
are `completed` only. Never `cancelled` after trip start."

Separately, the write itself was unconditional (`update_ride(ride_id,
payload)`, filtered only by `id`), with a follow-up re-read to detect a
"silent no-op." This is race-prone: two concurrent admin actions (or a
driver accepting/arriving between the admin's read and write) could both
"succeed," with the loser's write silently overwriting a real, newer state
transition.

## 2. Root cause

The endpoint predates the state-machine's `in_progress`-is-terminal-for-cancel
rule being fully enforced at every write site; the deny-list style
(`status in (...)` → reject) only lists the two states someone thought to
add, rather than an allow-list of the actual pre-trip states the operation
is valid for. The unconditional write was never revisited when
`routes/drivers/ride_flow.py`'s driver-accept path adopted the
conditional-update optimistic-lock pattern for the equivalent problem.

## 3. Fix / remediation

- Replaced the deny-list with an explicit allow-list,
  `_ADMIN_CANCELLABLE_STATUSES = (scheduled, searching, driver_assigned,
  driver_accepted, driver_arrived)` — `in_progress` (and any future/unknown
  status) is now rejected with 400, mirroring `admin_complete_ride`'s
  existing allow-list style for the complementary operation.
- Replaced the unconditional `update_ride` + re-read-to-verify with a
  conditional `update_one("rides", {"id": ride_id, "status": status_from},
  with_38_or_37_payload)` — the same optimistic-lock pattern
  `routes/drivers/ride_flow.py:331` uses for driver-side accept.
- `None` (0 rows matched) is disambiguated by a single re-read **on the
  failure path only** (not on every cancel, as the old verify-by-re-read
  did): if the ride has moved on, it was a genuine race → 409 +
  `info` log; if the ride still holds the status we filtered on, the write
  silently did nothing (RLS / service-role misconfiguration) → the
  original loud 500 + `error`-level "silent no-op" log is preserved.
  Collapsing both into a 409 would have misreported a broken deployment as
  routine contention and downgraded an `error` diagnostic to `info` — a
  diagnosability regression against the very failure this endpoint was
  hardened for. (Caught in adversarial self-review of this PR, fixed
  before merge.)
- Added `record_period_transition(driver_id, 1)` on driver release,
  matching `admin_complete_ride`'s identical call — the freed driver's
  insurance period audit trail was previously missing this row on the
  cancel path (present on the complete path).
- Kept the migration-37/38 layered-payload fallback unchanged in shape,
  just re-targeted at the conditional filter.

## 4. Risk & impact on existing functionality

- Blast radius: `admin_cancel_ride` is called from exactly one place,
  `admin-dashboard/src/lib/api/live-monitoring.ts`'s `adminCancelRide` (the
  live-monitoring page's Cancel button) — its response shape
  (`{success, ride_id, status}` on 200, `detail: string` on error) is
  unchanged, so no frontend change is required. Checked
  `admin-dashboard/src/lib/api/client.ts`: error `detail` is surfaced as a
  plain string in a thrown `Error`, confirming the new 409's `detail`
  (a string, not an object) renders correctly.
  admin-dashboard is not a build/deploy target of this backend-only
  change, and no `npm run build` was run for it (nothing there changed).
- Three existing test files coupled directly to the old `update_ride`-based
  implementation and needed updates in this same change (found via a
  grep for `api/admin/rides/.*cancel` across `backend/tests/`):
  `test_admin_rides_coverage.py` (5 tests), `test_admin_business_logic.py`
  (1 test), `test_ride_accept_flow.py` (2 tests). All were updated to mock
  `update_one` instead of `update_ride` with matching call-shape
  assertions; no test's *intent* changed, only its mock target, except
  `test_cancel_silent_no_op_surfaces_500` which no longer has an
  equivalent scenario (the conditional update structurally cannot "succeed
  but not persist" the way the old unconditional write could) — replaced
  with `test_cancel_race_lost_returns_409`, covering the new failure mode
  that supersedes it.
- No other reader/writer of `rides.status` for the `cancelled` value is
  affected — the rider/driver-facing cancel path (`routes/rides/...`) is a
  separate function with its own, already-conditional guard.

## 5. User-experience effect

- Internal admin-facing only (live-monitoring page). An admin attempting to
  cancel an `in_progress` ride now sees a 400 ("Cannot cancel ride from
  state 'in_progress'") instead of the cancel silently succeeding — this is
  a **new, correct rejection** of an action that was already supposed to be
  invalid per the documented state machine, not a new restriction on
  previously-valid admin behavior. An admin whose cancel races a driver's
  concurrent accept/arrive now sees a 409 ("Ride state changed...") instead
  of either an incorrect 200 or an unrelated-looking 500.
- Not visible mid-session to a rider or driver beyond the existing
  behavior: a *validly* cancelled ride (any allowed pre-trip state) still
  triggers the same `ride_cancelled` WS push and notifications as before.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/rides.py` | `admin_cancel_ride`: allow-list state guard, conditional `update_one` write, 409 on race, `record_period_transition` on release. | Fix the #1 Blocker + the adjacent write race |
| `backend/tests/test_admin_rides_cancel_state.py` (new) | State-matrix coverage (all 5 allowed states, 3 rejected states), race-guard filter shape, Period-1 recording. | Plan-specified new test file |
| `backend/tests/test_admin_rides_coverage.py` | 5 tests: `update_ride` mock → `update_one`; silent-no-op test replaced with race-lost-409 test. | Keep passing against the new implementation |
| `backend/tests/test_admin_business_logic.py` | 1 test: deterministic `update_one` + notification mocks, asserts exactly 200 (was `in (200, 500)`). | Same |
| `backend/tests/test_ride_accept_flow.py` | 2 tests: `update_ride` mock → `update_one`, added `record_period_transition` mock + assertion. | Same |

## 7. Before / after

```python
# Before
    if ride.get("status") in ("completed", "cancelled"):
        raise HTTPException(status_code=400, detail="Ride already completed or cancelled")
    ...
    try:
        await db_supabase.update_ride(ride_id, with_38)
    except Exception:
        ...
        await db_supabase.update_ride(ride_id, with_37)
    verify = await db_supabase.get_ride(ride_id)
    if not verify or verify.get("status") != "cancelled":
        raise HTTPException(status_code=500, detail="Cancel did not persist — see backend logs.")

# After
    status_from = ride.get("status")
    if status_from not in _ADMIN_CANCELLABLE_STATUSES:  # scheduled/searching/driver_assigned/driver_accepted/driver_arrived
        raise HTTPException(status_code=400, detail=f"Cannot cancel ride from state '{status_from}'")
    ...
    cancel_filter = {"id": ride_id, "status": status_from}
    try:
        updated = await db_supabase.update_one("rides", cancel_filter, with_38)
    except Exception:
        ...
        updated = await db_supabase.update_one("rides", cancel_filter, with_37)
    if updated is None:
        raise HTTPException(status_code=409, detail="Ride state changed before the cancel could be applied — refresh and retry")
```

## 7b. Post-review correction (2026-09-04)

A second review pass caught a **regression introduced by this very fix**. The
first version returned 409 whenever the conditional update matched 0 rows and
the re-read showed anything other than `status_from` — including `cancelled`.
But `update_one` runs under `run_sync`'s default `"read"` retry policy (3
attempts), so a write that lands and then loses its response to a dropped H2
connection is retried, and the retry matches 0 rows because the row it just
wrote no longer holds `status_from`. That path 409'd **after** the ride was
already cancelled in the DB but **before** the driver release, the
`ride_cancelled` WS/push and the audit row — driver stuck `is_available=False`
until the orphan reaper, rider stuck on "Finding driver". Precisely the failure
this endpoint exists to prevent, and one the *old* verify-by-re-read handled
correctly.

The 0-rows path now distinguishes three cases, not two: already-`cancelled` is
idempotent **success** and falls through to the side effects (using the
re-read row as the source of `driver_id`); status unchanged is still the loud
500 silent-no-op; anything else is the 409 race. Regression test:
`test_already_cancelled_completes_side_effects_instead_of_409`.

## 8. Rollback plan

`git revert` is sufficient: no migration, no stored data shape change, and
the response contract for the existing success/400/404 cases is unchanged
(only a previously-mishandled `in_progress` case now correctly 400s, and a
previously-impossible-to-detect race now correctly 409s instead of a false
200 or an unrelated 500).

## 9. Verification performed

- [x] Automated tests written: 6 new/rewritten test cases in
  `test_admin_rides_cancel_state.py` plus fixes to 8 existing tests across
  3 files (13 total touched).
- [ ] **Not run in this session** — same environment limitation as
  `docs/change-log/2026-09-03-ws1-correctness.md`: PyPI access is blocked
  by this sandbox's network egress policy, so `pytest` could not execute.
  Every test was hand-traced against `update_one`'s actual return
  semantics (`repositories/_base.py`: `None` on 0 rows, the updated dict
  otherwise) and the route's actual call order. **Must be run in CI before
  merge.**
- [x] Static verification: `py_compile` + `ruff check` + `ruff format
  --check` clean on every touched file.
- [x] Blast-radius grep: `api/admin/rides/.*cancel` and `admin_cancel_ride`
  across `backend/tests/*.py` (3 files, 8 tests, all updated) and
  `admin-dashboard/src` (1 caller, response shape unchanged).
- [x] Reviewed against `CLAUDE.md`: ride state machine ("Never `cancelled`
  after trip start" — now enforced), insurance-period rules
  (`record_period_transition` added to match `admin_complete_ride`), the
  documented optimistic-lock pattern (`ride_flow.py:331`, now mirrored
  here).
- [ ] Manual repro in staging — no staging environment exists yet (WS-4).

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed.
- [x] No silent behavior change without the UX field filled in (§5) —
  both behavior changes (400 on `in_progress`, 409 on race) are corrections
  of previously-wrong behavior, stated explicitly above.

## What was NOT verified

- No automated test run in this session (network-policy blocked PyPI —
  see above). Static analysis and manual tracing only.
- Not tested against a live Supabase instance — `update_one`'s conditional
  filter behavior is verified by reading `repositories/_base.py`'s actual
  PostgREST query construction, not by exercising a real Postgres
  `UPDATE ... WHERE id = ? AND status = ?`.
- No `admin-dashboard` build was run — this PR does not touch
  `admin-dashboard/`, and the one caller's request/response shape is
  unchanged.


## Post-merge correction (2026-09-04) — this surface is under visual regression after all

The admin-dashboard portion of this work (C68, `ride-panel.tsx` and
`ride-detail-modal.tsx`) was documented as unverifiable beyond type-reading,
citing CLAUDE.md's statement that admin-dashboard's Playwright visual-regression
job self-skips for want of committed baselines.

**That statement was stale.** PR #4916 (`27ff638`, 2026-09-02) seeded 5 of the 6
baselines, `dashboard-monitoring` among them — the very page `ride-panel.tsx`
renders. The job runs, and on this PR's own CI it reported failures.

What that changes:

- Hiding the Cancel button on an `in_progress` ride is a deliberate visual
  change to a page with a committed baseline, so a `dashboard-monitoring` diff
  is the expected outcome, not evidence of a bug. The baseline needs
  re-capturing through `update-visual-baselines.yml` by someone with
  Actions-dispatch access (this session has none — same 403 recorded in B38).
- It is not merge-blocking: `visual-regression-test` carries
  `continue-on-error: true` in `ci.yml` until all 6 baselines exist, which is
  why the check can be red while the workflow run concludes `success`.
- `dashboard-rides` (missing snapshot) and `dashboard-settings` are **not** this
  change's doing. Both failed on `a2359e8`, a commit in this PR whose diff
  contained no admin-dashboard file — which is the evidence that separates them
  from the monitoring diff.

CLAUDE.md's paragraph has been corrected so the next author is not told this
surface has no coverage.
