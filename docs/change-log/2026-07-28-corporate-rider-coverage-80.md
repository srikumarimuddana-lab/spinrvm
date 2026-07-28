# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude Code (session continuation of PR #2729 pattern) |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | (see PR opened from branch `claude/corporate-rider-coverage`) |
| Related issue or gap ID | `ACTION_ITEMS.md` A1b — corporate billing test-coverage track |

## 1. Issue / gap identified

`backend/routes/corporate_rider.py` (rider-facing work-profile endpoints:
balance, ride history, allowance requests, invite/domain-join) sat at
~32-33% unit-test coverage, below the module's own ≥80% target
(`CLAUDE.md` coverage-minimums table, same tier as rides/dispatch because
allowance requests move money via `apply_grant`/`corporate_wallet_apply_delta`).

## 2. Root cause

The original test file (`backend/tests/test_corporate_rider_routes.py`)
covered only the happy paths for `list_work_profiles`, `auto_match`,
`accept-invite` (success + 404), balance, and the allowance-request 409
rate-limit case. It never exercised: `_ensure_member`'s 403 branch,
`_compute_remaining`'s `unlimited`/missing-`amount` branches, the entire
`join-domain` flow (unverified email, no-email, unauthorized domain,
success), `my_rides` (empty result, join + `to`-date filtering, `from_`
filter), the allowance-request auto-approve-vs-pending branch, and
`my_requests`.

## 3. Fix / remediation

Test-only change: added 15 new unit tests to the existing test file,
exercising every previously-untested branch listed above. No production
code in `backend/routes/corporate_rider.py` was modified.

## 4. Risk & impact on existing functionality

- **Blast radius: none.** This PR adds tests only; it does not touch
  `backend/routes/corporate_rider.py`, `backend/services/corporate_allowance_service.py`,
  `backend/services/corporate_membership_service.py`, or any other
  production file. No other route/service consumes the test file itself.
- Grepped for other importers of `corporate_rider.py`'s router: only
  mounted once, in `backend/server.py` (router registration), which this
  change does not touch.
- No wallet/allowance deltas are applied against a real or mock DB beyond
  what the existing `mock_supabase_client`/`AsyncMock` patterns already
  exercised — `apply_grant` is mocked (`AsyncMock`) in the new
  auto-approve test, so no real Postgres RPC (`corporate_wallet_apply_delta`)
  is invoked.

## 5. User-experience effect

None. No production code changed; no rider/driver/corporate-admin/internal-admin
facing behavior is affected.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_corporate_rider_routes.py` | Added 15 unit tests covering `_ensure_member` 403, `_compute_remaining` unlimited/missing-amount branches, full `join-domain` flow (unverified email, no-email, unauthorized domain, success), `my_rides` (empty, `from_`/`to` filtering, join), allowance-request auto-approve vs. pending fallback, `my_requests` | Close coverage gap on `routes/corporate_rider.py` per `ACTION_ITEMS.md` A1b |
| `ACTION_ITEMS.md` | Updated A1b's corporate-billing coverage table: `routes/corporate_rider.py` line changed from "32-33%" (grouped with `corporate_signup.py`/`corporate_company_kyb.py`) to a standalone "97%" entry with a pointer to this change-log | Keep the backlog doc's measured numbers accurate |

## 7. Before / after

Not applicable — this is a purely additive test-only change (no existing
behavior modified). Per the template, before/after snippets are only
required for behavior-changing diffs.

## 8. Rollback plan

`git revert` of this commit is sufficient and complete: the change adds
test code only, touches no runtime state, no migrations, no feature
flags, and no live data (no Stripe charges, wallet deltas, or ride state
are created or mutated by this change — all DB/service calls in the new
tests are `AsyncMock`-patched). Reverting simply removes the added test
assertions and the two doc edits; nothing else depends on them.

## 9. Verification performed

- [x] Automated tests run — unit only:
  `cd backend && python -m pytest tests/test_corporate_rider_routes.py --cov=routes.corporate_rider --cov-report=term-missing -q`
  → **20 passed**, coverage for `routes/corporate_rider.py`: **117 statements,
  4 missed, 97%** (real `pytest-cov` output, not fabricated). The 3
  remaining missed lines (22-25) are the module's `except ImportError:`
  dual-import fallback branch, which is only reachable when the module is
  imported top-level outside the `backend` package (`CLAUDE.md`'s
  documented dual-import pattern) — not exercised by any test in this
  repo's suite and out of scope to fake-exercise.
- [x] Ran the full `test_corporate_rider_routes.py` file (not just the new
  tests) to confirm no regressions: all 20 tests pass together.
- [ ] Manual repro in staging — not applicable, test-only change, no
  runtime behavior to repro.
- [x] Blast-radius grep performed: confirmed `corporate_rider.py`'s router
  is mounted exactly once (`backend/server.py`); no other test file
  imports from `test_corporate_rider_routes.py`.
- [x] Reviewed against `CLAUDE.md` testing conventions: used
  `app.dependency_overrides` for `get_current_user` (existing
  `rider_override` fixture), patched DB/service functions at
  `routes.corporate_rider.<name>` (the binding site, not the source
  module) per the file's own docstring convention.
- [ ] Feature-flagged — not applicable, test-only change.
- [x] Full `-k corporate` run across the repo's test suite
  (`pytest tests/ -k corporate`): **411 passed, 3 skipped, 0 failed** in
  127.45s. Confirms no regression to any other corporate-domain test file.

## 10. What was NOT verified

- The full `backend/tests/` suite (all ~5000 tests, not just the
  `-k corporate` subset) was not run in this session — only the
  corporate-scoped subset (411 passed/3 skipped/0 failed) and the target
  file in isolation (20/20 passing, 97% coverage) were confirmed.
- No integration or E2E test was added or run against a real/throwaway
  Supabase schema — all new tests use `AsyncMock`-patched DB/service calls
  per this repo's existing unit-test convention for this file.
- Coverage of the two other files historically grouped with
  `corporate_rider.py` in `ACTION_ITEMS.md` (`corporate_signup.py`,
  `corporate_company_kyb.py`, still ~32-33%) was explicitly out of scope
  per the task instructions (other agents are working those files in
  parallel worktrees) and was not touched or re-measured.
- No application bug was found while writing these tests. If one had
  been, it would be reported here rather than silently fixed — none was.
