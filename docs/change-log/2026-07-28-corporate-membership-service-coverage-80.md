# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude Code (claude-sonnet-5) |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | (see PR opened alongside this file) |
| Related issue or gap ID | `ACTION_ITEMS.md` A1b — corporate services coverage gap |

## 1. Issue / gap identified

`backend/services/corporate_membership_service.py` — the invite/accept/domain-auto-match state
machine for corporate membership — was under-tested relative to CLAUDE.md's coverage-minimums
table (`services/corporate_*.py` target ≥80%, same tier as rides/dispatch because it's on the
money path via `corporate_wallet_apply_delta`-adjacent flows). Measured coverage before this
change: 67% (5 existing tests; the 27% figure in `ACTION_ITEMS.md` was from an earlier aggregate
measurement and had already drifted).

## 2. Root cause

The existing test file (`backend/tests/services/test_corporate_membership_service.py`) covered
only the happy paths for `invite_member`, `accept_invite`, and `auto_match_by_email`. It never
exercised: the concurrent-consumption race branch in `accept_invite`, the two early-return guard
clauses in `auto_match_by_email` (no `@`, blank domain), `join_via_domain` (both the happy path
and its "accept raced, fall back to invite row" branch), the `_uuid_or_none` UUID-coercion helper,
and `bootstrap_owner`'s three branches (self-serve create, self-serve idempotent-on-retry, and the
staff-created invite-flow fallback).

## 3. Fix / remediation

Test-only change. Added 12 new unit tests to the existing test file, using the same
`unittest.mock.AsyncMock`/`patch` pattern already established there (patch target:
`services.corporate_membership_service.<imported_name>`, matching how the module imports its
`db_supabase` helpers). No application code in `corporate_membership_service.py` was modified.

No bugs were found in the module while writing these tests — all behavior matched the module's
own docstrings (idempotent bootstrap, `_uuid_or_none` fallback, `join_via_domain` fallback to the
invite row when `accept_member_invite` races).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to test code.** No production code path in `corporate_membership_service.py`,
  `db_supabase.py`, or any caller (`routes/corporate_*` membership endpoints, rider auto-match flow)
  was touched.
- The 5 pre-existing tests in the same file were left unmodified and still pass.
- Grepped for other consumers of the functions under test to confirm no shared-fixture or
  shared-mock collision: `invite_member`, `accept_invite`, `auto_match_by_email`, `join_via_domain`,
  `bootstrap_owner`, `_uuid_or_none` are only imported by `backend/routes/corporate_accounts.py`,
  `backend/routes/corporate_rider.py`, `backend/routes/corporate_signup.py`, and
  `backend/routes/corporate_company.py` (per `grep -rln` over `backend/`) — those routes are
  covered by their own separate test files (`test_corporate_ride_payment.py`,
  `test_corporate_rider_routes.py`, etc.), which were run end-to-end (see Verification) and
  passed unchanged.
- No interaction with the 16 background loops, the ride state machine, or wallet/allowance money
  deltas — this module only manages `corporate_members` row state (invited/active), not payments.

## 5. User-experience effect

None. Test-only change; no rider, driver, corporate-admin, or internal-admin facing behavior is
altered. Not visible mid-session to anyone.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/services/test_corporate_membership_service.py` | Added 12 new unit tests covering `accept_invite`'s race branch, `auto_match_by_email`'s two guard clauses and existing-membership exclusion, `join_via_domain`'s happy path and race-fallback, `_uuid_or_none`'s three branches, and `bootstrap_owner`'s three branches | Close coverage gap on invite/accept/domain-match/bootstrap branches per `ACTION_ITEMS.md` A1b |
| `ACTION_ITEMS.md` | Updated the `services/corporate_membership_service.py` line from 27% to 100%, with a dated note | Keep the tracked coverage backlog accurate |
| `docs/change-log/2026-07-28-corporate-membership-service-coverage-80.md` | New change-log entry (this file) | Mandatory Change Impact & Risk Log for a corporate-surface change per CLAUDE.md |

## 7. Before / after

Not applicable — pure additive test code, no existing caller's behavior changed.

## 8. Rollback plan

Test-only change with no runtime effect: `git revert` of the test-file/doc commit is sufficient
and complete — there is no data, flag, or config state to unwind (no Stripe charges, wallet
deltas, or ride/insurance state touched).

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/services/test_corporate_membership_service.py -q`
      — real coverage run via the repo's default `pytest.ini` addopts (`--cov=.
      --cov-report=term-missing`), not a stub. Result: **17 passed**,
      `services/corporate_membership_service.py 66 0 100%` (no missing lines).
- [x] Broader regression check: `pytest backend/tests/ -k "membership or corporate_membership" -q`
      — **46 passed, 1 skipped** (pre-existing skip, unrelated to this change), confirming no
      regression in the routes that call this service (`corporate_accounts`, `corporate_rider`,
      `corporate_ride_payment` membership-related tests).
- [x] Blast-radius grep performed: `grep -rn` for every import of the six public names in
      `corporate_membership_service.py` across `backend/` — only `routes/corporate_accounts.py`,
      `routes/corporate_rider.py`, `routes/corporate_signup.py` (listed above).
- [x] Reviewed against CLAUDE.md testing conventions: patch target matches the module's own
      import names (service-layer file, so `services.corporate_membership_service.<name>`, per
      the "for service-layer files patch the specific imported function names" guidance), used
      `AsyncMock`/`patch`, no real network/DB calls made.
- [ ] Feature-flagged: not applicable — test-only change, nothing user-visible to flag.
- Backend has no `npm run build` equivalent; this is a Python service — `pytest` (real run, not
  `--collect-only`) is the correct verification tier here, and was run as stated above.

## 10. What was NOT verified

- No integration/E2E test against a real (or throwaway-schema) Supabase instance — all tests use
  `AsyncMock` per the repo's unit-test convention; the module's actual SQL/`db_supabase` helper
  behavior (e.g. `insert_corporate_member_invite`'s real constraint behavior on
  `corp_members_company_user_unique`) is exercised elsewhere (`test_corporate_membership_db_helpers.py`),
  not by this PR.
- Did not re-run the *entire* backend test suite (5000+ tests) end-to-end in this session, only
  the membership-scoped subset (46 tests) plus the target file — chose this to stay within the
  task's "test-only, scoped diff" instruction rather than touching unrelated coverage state.
- No visual/snapshot regression tooling applies here (backend-only, no UI).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-level unwind needed)
- [x] Blast radius is stated, not assumed (grep results listed above)
- [x] No silent behavior change — nothing changed for any already-shipped flow (test-only)
