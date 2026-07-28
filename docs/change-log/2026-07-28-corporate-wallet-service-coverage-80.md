# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude Code (claude-sonnet-5) |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | (see PR link in this branch) |
| Related issue or gap ID | ACTION_ITEMS.md A1b — corporate services coverage gap |

## 1. Issue / gap identified

`backend/services/corporate_wallet_service.py` — the module all corporate money movements (top-ups, adjustments, refunds) route through — was measured at ~41% test coverage, below the ≥80% minimum CLAUDE.md sets for `services/corporate_*.py` (same tier as rides/dispatch, since it moves real money via `corporate_wallet_apply_delta`).

## 2. Root cause

Existing tests (`backend/tests/services/test_corporate_wallet_service.py`) covered the happy paths for `apply_topup`, `apply_adjustment`, and the empty-rows RPC guard, but were missing: `apply_refund`'s non-positive-amount validation branch, `Decimal` rounding-edge-case verification (ROUND_HALF_UP vs. banker's rounding), the "no floor supplied" branch of `apply_adjustment`, and — most importantly — a test proving that an RPC/DB exception is not swallowed but surfaces as a `DatabaseError` per the CLAUDE.md "do not silently swallow errors" convention. The stated 41% figure predates a related historical measurement; independent re-measurement of just this test file today (isolated run) shows the pre-existing suite already reached ~90% on its own — the true gap was a handful of specific missing branches, not broad neglect.

## 3. Fix / remediation

Test-only change. Added 5 new unit tests to `backend/tests/services/test_corporate_wallet_service.py`:
- `test_refund_rejects_non_positive_amount` — covers the previously-untested `ValueError` branch in `apply_refund`.
- `test_money_str_rounds_half_up_and_accepts_various_numeric_types` — asserts `_money_str` uses `ROUND_HALF_UP` (10.125 → "10.13"), not Python's default banker's rounding, and that a string amount input round-trips correctly across the RPC boundary.
- `test_adjustment_without_floor_omits_floor_param` — covers the `floor is None` branch of `apply_adjustment`.
- `test_apply_propagates_rpc_exception_without_swallowing` — asserts an RPC-layer exception is not swallowed: it surfaces as `utils.error_handling.DatabaseError` (via `run_sync`'s wrapping in `repositories/_base.py`) with the original exception text preserved in `.details["original"]`, matching CLAUDE.md's error-surfacing rule.
- No production code was touched.

## 4. Risk & impact on existing functionality

Test-only change — no production code paths modified, so no runtime regression risk. Blast-radius grep for real (non-test) callers of `corporate_wallet_service` (`apply_topup` / `apply_adjustment` / `apply_refund`):
- `backend/services/payment_service.py` — fare settlement corporate-payment-source branch calls into wallet application.
- `backend/services/corporate_wallet_winddown_service.py` — account offboarding/winddown wallet closeout.
- `backend/routes/webhooks.py` — Stripe webhook handler applies top-ups on `payment_intent.succeeded`.
- `backend/routes/corporate_wallet.py` — admin-facing manual top-up/adjustment endpoints.

None of these call sites were modified. The new tests exercise the same public function signatures (`apply_topup`, `apply_adjustment`, `apply_refund`) already used by all four callers above, so they add coverage of behavior those callers already depend on without changing it.

**Bug found but NOT fixed (per task constraint — test-only change):** none. All money arithmetic in `corporate_wallet_service.py` correctly uses `Decimal` and routes exclusively through the `corporate_wallet_apply_delta` Postgres RPC (`_apply()` is the single call site; no direct wallet-table mutation exists in this file). No float arithmetic or delta-application bypass was found.

## 5. User-experience effect

None. Test-only change; no rider, driver, corporate-admin, or internal-admin facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/services/test_corporate_wallet_service.py` | Added 5 unit tests (refund validation, Decimal rounding, floor-omitted branch, exception-propagation) | Close missing-branch coverage gaps to raise module coverage from ~41% (stated) to 97% (measured) |
| `ACTION_ITEMS.md` | Updated A1b entry's `corporate_wallet_service.py` line from "41%" to "97% (closed 2026-07-28)" | Keep backlog doc in sync with real measured coverage |
| `docs/change-log/2026-07-28-corporate-wallet-service-coverage-80.md` | New file (this doc) | Mandatory Change Impact & Risk Log entry per CLAUDE.md, since the surface touched (corporate money code) requires one even for test-only changes |

## 7. Before / after

Not applicable — no behavior-changing diff; purely additive test code.

## 8. Rollback plan

Revert the test-file commit (`git revert`) if any new test proves flaky in CI. This is safe as a plain revert because: (a) no production code changed, (b) no data was written (all Supabase/RPC calls are mocked via `unittest.mock.patch`), and (c) no migration or `app_settings` flag is involved. A `git revert` is a legitimate rollback here specifically because nothing touched live data — unlike a wallet-delta or ride-state change, which CLAUDE.md correctly requires more than `git revert` for.

## 9. Verification performed

- [x] Automated tests run: `cd backend && python -m pytest tests/services/test_corporate_wallet_service.py -q` — 11 passed, 0 failed.
- [x] Coverage measured: `python -m pytest tests/services/test_corporate_wallet_service.py -q` (project's default `--cov` config) shows `services/corporate_wallet_service.py 37 stmts, 1 miss, 97%` — missing line is 16, the `except ImportError:` fallback-import branch (only reachable when running as a top-level script, not under `python -m backend.server`; both the original file and the pre-existing tests never exercised it either — it is dual-import boilerplate, not app logic).
- [x] Regression check on related corporate wallet test files: `python -m pytest tests/test_corporate_webhook.py tests/test_allowance_cap_fallback.py tests/test_corporate_e2e_wallet.py tests/test_corporate_ride_payment.py -q` — 22 passed, 0 failed.
- [x] Blast-radius grep performed: `grep -rl "corporate_wallet_service\|apply_topup\|apply_adjustment\|apply_refund" backend --include=*.py` — 14 files found, 4 real (non-test) callers listed in section 4.
- [x] Reviewed against CLAUDE.md conventions: Decimal-only money arithmetic (confirmed, no float use in file), `corporate_wallet_apply_delta` as sole mutation path (confirmed), error-surfacing rule (confirmed via new exception test).
- [ ] Feature-flagged — not applicable, test-only change with no runtime behavior.
- Production build: not applicable — this PR touches only `backend/` Python test code, not `admin-dashboard`/`rider-app`/`driver-app`, so no `npm run build` was required or run.

## 10. What was NOT verified

- Coverage was measured only via the project's default pytest-cov invocation against this one test file in isolation; the full `pytest` suite (all ~34k statements) was not run end-to-end in this session due to session time constraints — only the target file's own tests plus four directly related corporate-wallet test files were re-run for regression checking.
- Not tested against a live Supabase instance or the real `corporate_wallet_apply_delta` Postgres function — all RPC calls are mocked (`unittest.mock.patch` on `services.corporate_wallet_service.supabase`), consistent with this repo's existing unit-test convention (`mock_supabase_client`-equivalent pattern), but this means row-level locking, idempotency-on-`stripe_payment_intent_id`, and floor-enforcement behavior *inside* the Postgres function itself remain unverified by this change — only that the Python service passes the correct parameters to it.
- No visual/UI verification needed or performed (backend-only, non-visual change).

## Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, safe because no production code or live data touched)
- [x] Blast radius is stated, not assumed (4 real callers named above)
- [x] No silent behavior change to an already-shipped flow — none occurred; test-only change
