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

`backend/routes/corporate_rider.py` was at 65% test coverage. `join-domain`,
`my_rides`, `my_requests`, the allowance-request auto-approve/pending split,
the `InviteAlreadyConsumed` 409 branch, and several allowance edge cases
(unlimited allowance, non-member 403) had no test coverage.

## 2. Root cause

Endpoints were added over multiple PRs (work-profile listing, balance,
allowance requests) with tests only for the first happy/one error path per
endpoint; branch coverage on money-adjacent logic (`_compute_remaining`'s
unlimited-allowance branch, the auto-approve cap comparison) was never
completed.

## 3. Fix / remediation

Extended `backend/tests/test_corporate_rider_routes.py` with 12 new tests:
`accept-invite` 409 (already-consumed), `join-domain` (unverified email,
no-email-on-account, unauthorized domain, success), balance (unlimited
allowance → `remaining=None`, non-member → 403), `my_rides` (empty result,
join + `to`-date ceiling filter), `submit_request` (auto-approved path that
calls `apply_grant` with the wallet's soft-negative floor, and the
over-cap path that stays `pending` with zero wallet/allowance RPC calls),
and `my_requests`.

Coverage for `routes/corporate_rider.py`: 65% → 95% (measured via
`pytest --cov=routes.corporate_rider --cov-report=term-missing` against
this one test file). Remaining misses are the dual-import fallback (lines
22-25) and two defensive one-liners (80, 211) not reachable from normal
request flow.

## 4. Risk & impact on existing functionality

**Test-only change — zero application code modified.** Blast radius:
isolated, no other callers.

## 5. User-experience effect

None — no behavior change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_corporate_rider_routes.py` | +12 tests | Cover join-domain, my_rides, allowance auto-approve/pending split, my_requests, unlimited-allowance/non-member branches |

## 7. Before / after

Not applicable — additive tests only.

## 8. Rollback plan

`git revert` this commit. No data or production code touched.

## 9. Verification performed

- [x] `pytest backend/tests/test_corporate_rider_routes.py -q` — 19 passed, 0 failed
- [x] Coverage measured: `pytest --cov=routes.corporate_rider --cov-report=term-missing` against this file → 95%
- [x] Reviewed against CLAUDE.md Testing Conventions (patch-at-route-module-binding style already established in this file; `@pytest.mark.anyio` for the one direct-call test)

## What was NOT verified

- Coverage was measured running only this one test file, not the full
  `pytest -k corporate` suite.
- No live/staging Supabase exercised — `AsyncMock`/`app.dependency_overrides`
  patches only, per the file's existing convention.
