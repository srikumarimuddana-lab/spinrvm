# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (claude-sonnet-5) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers / admin |
| PR / commit link | see PR description |
| Related issue or gap ID | ACTION_ITEMS.md A1b Track 1, item 4 (`backend/routes/admin/`) |

## 1. Issue / gap identified

`backend/routes/admin/drivers.py` — the second-largest file in
`backend/routes/admin/` (1015 statements) — had ~59% test coverage. Admin
driver-management endpoints (approve/suspend/ban a driver, override status,
review documents/photos, reveal a driver's SIN) are audited but were not
comprehensively tested; a broken write endpoint here can lock a real driver
out of the platform or let an ineligible driver stay online, which is both a
production-data-integrity risk and a Saskatchewan Transportation Act
driver-eligibility/regulatory risk (see `CLAUDE.md` → Saskatchewan
Regulatory → Driver eligibility).

## 2. Root cause

This is a coverage gap from incremental feature growth, not a single root
cause — the file accumulated ~30 endpoints over time (list/search, stats,
approval-queue, lifecycle actions, notes, photo review, Stripe KYC/SIN
reveal, referrals, payouts) and test coverage did not keep pace, especially
for the write/mutation endpoints added later (status-override, reveal-sin,
nudge-expiry, refresh-stripe-kyc).

## 3. Fix / remediation

**Test-only change.** Added `backend/tests/test_admin_drivers_coverage.py`
(52 new unit tests, all Supabase/Stripe/push-notification calls mocked via
the repo's existing `unittest.mock.patch` + `test_client` convention — no
real DB, Stripe, or FCM/Twilio calls). No application code was modified.

Coverage on `backend/routes/admin/drivers.py`, measured via a full
`pytest tests/ -q` run (pytest-cov, real output, not estimated):

```
routes/admin/drivers.py    1015    301    70%   102-103, 147-159, 220, 222, 224,
226, 278-282, 346-348, 424, 459-472, 497, 502-522, 529, 534-539, 552-559, 678,
689, 769-791, 858, 905, 917, 932, 1035-1038, 1189-1194, 1221, 1268-1270,
1277-1281, 1286-1287, 1387-1388, 1437-1440, 1628-1629, 1710-1717, 1776-1813,
1831-1832, 1839-1843, 1848, 1855-1936, 1942-1945, 2004-2054, 2070-2120,
2156-2157, 2171, 2208, 2228, 2233, 2243-2244, 2278-2303, 2346-2369, 2391-2502,
2607, 2727-2764
```

Before: ~59% (per task baseline). After: **70%** (measured against the same
`pytest.ini --cov` configuration).

Prioritization, per the task's ordering and this file's real-world blast
radius:
1. **Driver-status-mutation endpoints** (`POST /drivers/{id}/action` —
   approve/suspend/ban/unban/reactivate; `PUT /drivers/{id}/status-override`;
   `POST /drivers/{id}/verify`) — happy paths, validation errors (missing
   suspend/ban reason → 400), 404 on missing driver, DB-failure → 500 (never
   silently swallowed, per `CLAUDE.md`), and push-notification-failure
   non-fatal (best-effort push must not roll back an already-committed
   status change).
2. **`PUT /drivers/{id}`** (the general driver-edit endpoint) — field
   routing across `users` vs `drivers` tables, the 409 on editing
   `email`/`gender` for a driver with no linked user row, null→`""`
   coalescing for `NOT NULL` vehicle columns, `work_authorization_status`
   flag-sync side effects, and DB-failure → 500.
3. **Document/eligibility-decision endpoints**: driver notes CRUD, photo
   upload/review (`/photo`, `/photo-review`), `nudge-expiry` (push-failure
   → 502, best-effort throttle-timestamp write), `refresh-stripe-kyc`, and
   `reveal-sin` (super_admin-only 403 gate, 400s for no Stripe
   account/no SIN on file, audit-log-before-reveal, SIN never appears in
   audit metadata, Stripe-failure → 502).
4. **Area assignment** (`PUT /drivers/{id}/area`).
5. Read/list/export endpoints (`GET /drivers`, `/drivers/stats`,
   `/drivers/approval-queue`, `/drivers/expiring`, referral
   leaderboards/analytics, payouts-summary, location-trail,
   daily-activity) were **deliberately deprioritized** — lower
   real-world consequence than a broken write (misreading a dashboard vs.
   corrupting a driver's live status), and several already have partial
   coverage from `test_admin_approval_queue.py`,
   `test_admin_drivers_expiring.py`, and `test_referral_analytics.py`. The
   remaining 301 uncovered lines are concentrated in these read-heavy
   endpoints (referral leaderboard/analytics aggregation, payouts-summary,
   vehicle-history/live-stats formatting) plus a few error-log-only branches
   in the write endpoints (e.g. the `except Exception: logger.warning(...)`
   best-effort branches after a note/activity-log write already succeeded).
   This is an accepted, documented partial stop, not a silent one — 70% is
   a real improvement over the 59% baseline and covers every write path
   this file exposes.

### Bugs found, NOT fixed (test-only task constraint)

Two pre-existing behavioral gaps were found while reading the code for test
coverage. Per the task's explicit instruction, these are **reported, not
fixed**:

1. **`POST /drivers/{id}/action` cannot actually reject a driver.**
   `DriverActionRequest.action` is typed
   `Literal["approve", "reject", "suspend", "ban", "unban", "reactivate"]`
   and the docstring/push-notification map both list `"reject"` as a valid
   lifecycle action — but the handler's `if/elif` chain
   (`backend/routes/admin/drivers.py` ~line 1459-1504) has branches only for
   `approve`, `suspend`, `ban`, `unban`, `reactivate`. Calling this endpoint
   with `{"action": "reject"}` passes pydantic validation, then falls
   through to `else: raise HTTPException(400, f"Unknown action: {req.action}")`.
   **Effective impact:** an admin cannot reject a pending driver application
   through the intended lifecycle-action endpoint — the request 400s. Pinned
   with a regression test (`test_reject_action_is_documented_but_unimplemented`)
   so this doesn't get silently "fixed" as an incidental side effect of a
   future unrelated change without anyone noticing the behavior actually
   changed.
2. **`PUT /drivers/{id}/status-override` has a Literal/handler-guard
   mismatch.** `DriverStatusOverride.status` is typed
   `Literal["pending", "active", "rejected", "suspended", "banned"]`, but the
   handler's own `valid = {"pending", "active", "needs_review", "suspended",
   "banned"}` set does not include `"rejected"` (so a pydantic-valid
   `"rejected"` 400s at the handler's internal check) and does include
   `"needs_review"` (which pydantic rejects with 422 before the handler ever
   runs, since it's not in the Literal). Pinned with
   `test_invalid_status_rejected_by_endpoint_guard`.

Both are flagged prominently in the PR description per the task instructions
and in `ACTION_ITEMS.md`'s item 4 sub-bullet. Recommend a follow-up ticket
to decide the intended behavior (should `action=reject` set
`status=rejected`? should the two status Literals be unified?) before
fixing, since it touches the driver-eligibility state machine.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to test files.** No application code in
`backend/routes/admin/drivers.py` or any other production file was changed.

- Grepped for other consumers/importers of the tested endpoints and of
  `backend/tests/test_admin_drivers_coverage.py`'s patch targets — none;
  this is a new, additive test file.
- Existing test files that already cover overlapping endpoints
  (`test_admin_business_logic.py::TestAdminDriverActions`,
  `test_admin_driver_photo.py`) were left untouched; the new file adds
  cases they didn't cover (DB-failure paths, push-failure-is-non-fatal
  paths, the `reveal-sin`/`refresh-stripe-kyc`/`nudge-expiry`/notes/area
  endpoints which had no prior direct coverage) rather than duplicating.
- Ran the new file alone, the new file plus the other `admin/drivers`-adjacent
  test files (168 tests), and the **full backend suite** (`pytest tests/ -q`)
  to confirm zero regressions anywhere in the codebase — see Verification.

## 5. User-experience effect

None. This is a test-only change with zero effect on any running surface —
no rider, driver, corporate-admin, or internal-admin facing behavior changed.
(The two bugs found above are pre-existing production behavior, unchanged by
this PR — flagged for a separate follow-up, not silently fixed here.)

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_admin_drivers_coverage.py` | New file — 52 unit tests for `routes/admin/drivers.py` write/mutation endpoints | Close the coverage gap identified in ACTION_ITEMS.md A1b Track 1 item 4 |
| `ACTION_ITEMS.md` | Added a sub-bullet under A1b Track 1 item 4 documenting the new measured coverage % and the two bugs found | Track progress per the item's own convention (matches the style of the Track 1 item 3 sub-bullets) |
| `docs/change-log/2026-07-29-a1b-admin-drivers-coverage.md` | New file — this document | Mandatory Change Impact & Risk Log for any commit closing a gap, per `CLAUDE.md` |

## 7. Before / after

Not applicable — this is purely additive test code with no behavior-changing
diff to any existing file. (The two bugs documented above are pre-existing;
no before/after diff exists because nothing was changed.)

## 8. Rollback plan

`git revert` is sufficient and complete: this PR touches only test files and
documentation, has no schema, config, or live-data footprint, and reverting
it simply removes the new tests and doc entries. No feature flag, migration,
or data remediation is needed.

## 9. Verification performed

- [x] Automated tests run:
  - `pytest tests/test_admin_drivers_coverage.py -q` → 52 passed
  - `pytest tests/test_admin_drivers_coverage.py tests/test_admin_business_logic.py tests/test_admin_driver_photo.py tests/test_admin_driver_import.py tests/test_admin_approval_queue.py tests/test_admin_drivers_expiring.py tests/test_admin_driver_training.py tests/test_referral_failed_claims_admin.py tests/test_referral_analytics.py tests/test_drivers.py -q` → 168 passed
  - Full suite: `pytest tests/ -q` → **5605 passed, 8 skipped, 1 xfailed, 0 failed** in 1018.62s; overall repo coverage 72.53% (above the 60% CI floor)
- [ ] Manual repro steps followed in staging — not applicable (test-only change, no runtime behavior to repro)
- [x] Blast-radius grep performed: searched for other importers/consumers of the new test file (none — it's new) and confirmed no overlap-conflict with the endpoints already covered by `test_admin_business_logic.py` / `test_admin_driver_photo.py`
- [x] Reviewed against relevant `CLAUDE.md` conventions: dual-import pattern (unaffected, no app code touched), testing conventions (mock Supabase via `unittest.mock.patch` targeting `db_supabase.*` / `routes.admin.drivers.*`, matches `test_admin_driver_photo.py`'s established pattern), "do not silently swallow errors" (asserted DB-failure → 500 and push-failure → non-fatal-200 paths explicitly)
- [ ] Feature-flagged if user-visible and non-trivial — not applicable, zero user-visible change

**What was NOT verified:** not tested against a real Supabase instance —
all DB/Stripe/push calls are mocked, per this repo's stated unit-test tier
convention (`mock_supabase_client`-style patching, no real DB in unit
tests). No production build (`npm run build` or equivalent) was run because
this PR touches only `backend/` Python test files, not
`admin-dashboard`/`rider-app`/`driver-app`. The two bugs documented in
section 3 were confirmed by reading the code and pinning the *current*
(broken) behavior with a test — they were not exercised against a live
admin dashboard UI, so it's possible (though unlikely, given the code is
unambiguous) that the frontend already works around the `reject` gap in
some way not visible from the backend alone; that should be checked as part
of any follow-up fix.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, test-only diff)
- [x] Blast radius is stated, not assumed — isolated to test files, zero production-code changes
- [x] No silent behavior change to an already-shipped flow — none made; the two discovered bugs are explicitly surfaced above, not fixed, and pinned with regression tests instead of being silently "fixed" as a side effect
