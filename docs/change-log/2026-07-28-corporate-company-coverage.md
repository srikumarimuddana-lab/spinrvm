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

`backend/routes/corporate_company.py` was at 79% coverage. `_validate_geofence`,
the invite email/audit failure-isolation branches, member removal/reactivation
audit branches, several allowance/allowed-domains 404 paths,
`decide_allowance_request`'s approve/deny branches, policy audit-failure
isolation, and the billing summary/statement pagination continuation branch
had no or partial test coverage — several are money-adjacent (allowance
grants via `apply_grant`, billing aggregation).

## 2. Root cause

Same pattern as the other three files: endpoints and their defensive
try/except audit-log wrappers were added across several corporate-lifecycle
PRs, with tests covering the primary success path per endpoint but not every
branch (especially the "log failure never surfaces as a request failure"
isolation, and multi-page pagination loops that only execute their
continuation branch when a page is exactly `page_size` long).

## 3. Fix / remediation

Added `backend/tests/test_corporate_company_gap_coverage.py` (34 tests)
covering: `_validate_geofence`'s four rejection branches + valid pass-through
(via `PUT /policy`), `list_members`'s comma-split status filter, invite's
email-delivery-exception and audit-log-failure isolation, `update_member`
(not-found, cross-company section reassignment, section-clear, reactivation
audit, removal ride-cancel-failure isolation, removal audit-failure
isolation), `remove_member` not-found, allowance CRUD 404s, allowed-domains
list/remove, `decide_allowance_request` (not-found, cross-company member,
already-decided 409, missing-allowance/wallet 409, missing-amount 422,
approve applies `apply_grant`, deny skips it), policy audit-failure isolation
(both PUT and PATCH), `_month_bounds` (December year-rollover, invalid-string
422), and the billing summary/statement pagination continuation branch
(`offset += page_size` only fires on a full page).

Coverage for `routes/corporate_company.py`: 79% → 93% (measured via
`pytest --cov=routes.corporate_company --cov-report=term-missing` against
this file plus the existing `test_corporate_company_routes.py`). Remaining
misses are the dual-import fallback (37-52), two narrow schema-shape lines
(316-317, 581), and a couple of allowance-field edge cases (403-404, 416,
451-459, 469-472) not reachable from the branches exercised here.

## 4. Risk & impact on existing functionality

**Test-only change — zero application code modified.** Blast radius:
isolated, no other callers.

## 5. User-experience effect

None — no behavior change. Tests exercise existing corporate-admin-portal
and rider-work-profile-adjacent code paths (allowance decisions, policy
edits, billing) that were previously unverified by any test.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_corporate_company_gap_coverage.py` | New file, 34 tests | Cover geofence validation, invite/removal/reactivation audit isolation, allowance-request decision money path, policy audit isolation, billing pagination |

## 7. Before / after

Not applicable — additive tests only.

## 8. Rollback plan

`git revert` this commit. No data or production code touched.

## 9. Verification performed

- [x] `pytest backend/tests/test_corporate_company_gap_coverage.py backend/tests/test_corporate_company_routes.py -q` — 70 passed, 0 failed
- [x] Coverage measured: `pytest --cov=routes.corporate_company --cov-report=term-missing` against those two files → 93%
- [x] Reviewed against CLAUDE.md money-arithmetic convention: `decide_allowance_request`'s approve path is tested to confirm it calls `apply_grant` with `Decimal(str(amount_raw))` and the wallet's `soft_negative_floor`, not a float

## What was NOT verified

- Coverage was measured running this file plus one pre-existing test file,
  not the full `pytest -k corporate` suite.
- No live/staging Supabase exercised — `AsyncMock` patches only.
- `apply_grant`'s own internals (the `corporate_allowance_apply_delta` RPC
  call) are covered separately in `test_corporate_allowance_*` files, not
  re-verified here — this commit only checks that `decide_allowance_request`
  calls it with the right arguments.
