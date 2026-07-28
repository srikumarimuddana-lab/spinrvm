# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude Code (session) |
| Surface(s) | backend (tests only) |
| Domain (Sentry tag) | corporate |
| PR / commit link | claude/b12-corporate-coverage-runbook |
| Related issue or gap ID | ACTION_ITEMS.md B12 |

## 1. Issue / gap identified

`backend/routes/corporate_accounts.py` was at 82% coverage. The
`change_company_status` handler — which drives wallet auto-topup freezing,
pre-pickup ride cancellation, and wallet wind-down on suspend/close — had
almost none of its branches (error-swallowing paths, reactivation
auto-topup-review flag) under test, and several 404/500/503/502 error paths
across get/update/delete/kyb-view were untested.

## 2. Root cause

Happy-path and a handful of validation-error tests existed per endpoint, but
`change_company_status`'s side-effect branches (each wrapped in its own
try/except per CLAUDE.md's "surface loudly, isolate the audit log" pattern)
were added across several PRs without matching tests for every branch.

## 3. Fix / remediation

Added `backend/tests/test_corporate_accounts_lifecycle.py` (29 tests)
covering: get/update/delete 404 and 500 paths, audit-log-failure isolation
for update/delete/create/status-change (the try/except around
`log_admin_action` must never surface as a request failure), the
`change_company_status` suspend path (auto-topup freeze +
`cancel_pre_pickup_rides_for_company`, including that path's own
failure-is-logged-not-raised branch), the close path (`refund_wallet_balance_on_close`
success/incomplete/exception branches), the reactivation
`auto_topup_needs_review` flag (both the true and swallowed-exception
branches), the `admin_view_kyb_document` 404/503/502/200 branches, and
`create_corporate_account`'s phone-normalization and insert-failure paths.

Coverage for `routes/corporate_accounts.py`: 82% → 95% (measured via
`pytest --cov=routes.corporate_accounts --cov-report=term-missing` against
this file plus the existing `test_corporate_admin_routes.py`). Remaining
misses are the module's dual-import fallback lines and a couple of narrow
validator branches (346-357, 473-474) not exercised by these tests.

## 4. Risk & impact on existing functionality

**Test-only change — zero application code modified.** Blast radius:
isolated, no other callers.

## 5. User-experience effect

None — no behavior change; tests only exercise existing code paths,
including internal-admin-facing error handling that was previously
unverified.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_corporate_accounts_lifecycle.py` | New file, 29 tests | Cover get/update/delete/status-transition/kyb-view/create error and side-effect branches |

## 7. Before / after

Not applicable — additive tests only.

## 8. Rollback plan

`git revert` this commit. No data or production code touched.

## 9. Verification performed

- [x] `pytest backend/tests/test_corporate_accounts_lifecycle.py backend/tests/test_corporate_admin_routes.py -q` — 54 passed, 0 failed
- [x] Coverage measured: `pytest --cov=routes.corporate_accounts --cov-report=term-missing` against those two files → 95%
- [x] Reviewed against CLAUDE.md conventions (error-isolation pattern for audit-log failures; "do not silently swallow errors" — verified the DB/500 paths still raise loudly, only the audit-log wrapper is soft by design)

## What was NOT verified

- Coverage was measured running this file plus the pre-existing admin-routes
  test file, not the full `pytest -k corporate` suite.
- No live/staging Supabase or real Stripe/Storage call was exercised —
  `AsyncMock`/`MagicMock` patches only.
