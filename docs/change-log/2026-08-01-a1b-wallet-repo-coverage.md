# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude (A1b Track 2 coverage initiative) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | see PR (branch: `claude/a1b-wallet-repo-coverage`) |
| Related issue or gap ID | `ACTION_ITEMS.md` A1b, Track 2 (first item) |

## 1. Issue / gap identified

`backend/repositories/wallet_repo.py` (444 lines — wallet & Stripe repository:
atomic wallet RPCs, promo application, fare-split, Stripe webhook idempotency
helpers) had no dedicated unit-test file. Its only coverage was indirect,
as a side effect of route-level tests (`test_wallet.py`,
`test_p2_promo_wallet_loyalty.py`, `test_p3_wallet_concurrency.py`,
`test_p3_promo_concurrency.py`, `test_webhooks_main.py`) and one file
(`test_wallet_apply_delta_contract.py`) that only asserts against the SQL
migration text, never executes the Python. Measured (full suite,
`pytest tests/ -q --cov=repositories.wallet_repo`): 40% (169 stmts, 102
missed) — well below this backlog's 80%+ target for money-adjacent files.

## 2. Root cause

The file was extracted from `db_supabase.py` during the Phase 4 god-object
decomposition; the extraction moved code but no test file was created to
follow it, and no later PR closed the gap because route-level tests happened
to exercise a slice of it as a side effect (masking the true gap from a
naive "is this tested?" grep).

## 3. Fix / remediation

Test-only change. Added `backend/tests/test_wallet_repo.py` (67 tests)
covering, for every one of the module's 12 public functions: the "Supabase
client not configured" branch, the happy path (asserting on RPC name and
exact param dict — e.g. `wallet_apply_delta`'s `p_floor`/`p_clamp_to_floor`
Decimal-to-string serialization), and DB-error propagation (confirming a
raised exception from the RPC call surfaces as `DatabaseError`/`ValueError`/
`RuntimeError` rather than being swallowed, per CLAUDE.md's error-handling
rule). Specific areas of focus: `wallet_pay_for_ride`/`wallet_transfer`/
`fare_split_pay_share`'s message-string-to-`ValueError` translation
branches (`insufficient_funds`, `wallet_not_found`, `fare_underpaid`,
`ride_not_payable`); `increment_promo_uses`/`claim_promo_user_slot`'s
truthy-value branch matrix (`True`/`1`/list-wrapped/`False`/`None`/empty);
and `claim_stripe_event`'s three duplicate-detection message patterns plus
the stuck-vs-processed distinction (`processed_at IS NULL` → `logger.critical`).
No application code was changed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** This PR adds one new test file
  (`backend/tests/test_wallet_repo.py`), one ACTION_ITEMS.md bullet, and
  this change-log entry. Zero lines of `backend/repositories/wallet_repo.py`
  or any other application file changed.
- Grepped for every other file that imports/patches `wallet_repo` symbols
  to confirm no naming collision with the new file's fixtures/helpers:
  `conftest.py` (patches `repositories.wallet_repo.supabase` at module
  scope — the same target this new file's tests patch locally per-test,
  consistent with the established `test_auth_repo.py` pattern), plus
  `test_p3_promo_concurrency.py`, `test_p3_wallet_concurrency.py`, and
  `test_wallet_apply_delta_contract.py` (importing `repositories.wallet_repo`
  directly inside test bodies, unaffected by this new file). No shared
  fixture/class names collide.
- No interaction with any of the 16 background loops, the ride state
  machine, or a live wallet delta — this PR never calls a real Supabase
  RPC, only a `MagicMock` standing in for the client.

## 5. User-experience effect

None. Test-only change; no rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_wallet_repo.py` | New file — 67 tests | Close the coverage gap on `repositories/wallet_repo.py` (40% → 99%) |
| `ACTION_ITEMS.md` | Added the first itemized Track 2 bullet under A1b (was prose-only before) | Track progress per the existing A1b series format; documents two findings (see §9 below) |
| `docs/change-log/2026-08-01-a1b-wallet-repo-coverage.md` | New file (this log) | Required per CLAUDE.md for any commit touching a live-tested-adjacent (money) surface |

## 7. Before / after

Not applicable — purely additive test file; no existing behavior-changing
diff in application code.

## 8. Rollback plan

Revert the new test file (and the `ACTION_ITEMS.md` bullet / this log) via
a single `git revert` — this PR touches zero live data, zero application
code, and zero migrations, so a plain code revert is a complete rollback.

## 9. Verification performed

- [x] **Baseline measured fresh, full suite, not assumed:**
  `cd backend && python -m pytest tests/ -q --cov=repositories.wallet_repo --cov-report=term-missing`
  with `tests/test_wallet_repo.py` temporarily removed → `repositories/wallet_repo.py`
  **169 stmts, 102 missed, 40%** (full-suite run: 6705 passed, 8 skipped, 1
  xfailed, 0 failed in that particular run — see the flaky-test note below).
- [x] **After, same full-suite command, test file restored:**
  `repositories/wallet_repo.py` **169 stmts, 2 missed, 99%** (missing lines
  19-20 are the dual-import `except ImportError` fallback — structurally
  only one branch runs per process, per this repo's own documented
  convention, not pursued further).
- [x] New file in isolation: `pytest tests/test_wallet_repo.py -q --no-cov`
  → 67 passed.
- [x] **Full backend suite** (not `-m "not slow"`, the real gate):
  `pytest tests/ -q --cov=repositories.wallet_repo --cov-report=term-missing`
  → **6771 passed, 8 skipped, 1 xfailed, 1 failed**. The 1 failure is
  `tests/test_e2e_ride_lifecycle.py::TestRideLifecycleConcurrency::test_two_drivers_accepting_same_ride_one_wins`
  — confirmed **pre-existing and unrelated**: it also failed in the
  "before" baseline run (before this PR's test file existed), and its own
  assertion failure (`[200, 200] != [200, 409]`) is a known flaky
  concurrent-request-ordering issue in a ride-dispatch test, nothing to do
  with wallets. It passed cleanly on a second independent full-suite run
  (see finding #2 below) — timing-dependent flake, not a regression from
  this PR.
- [x] **Found and worked around a real, pre-existing test-pollution bug**
  (documented in `ACTION_ITEMS.md`, not fixed — out of scope for this
  file's PR): running the new test file inside the full suite (rather than
  in isolation) initially produced 53 failures, all
  `ServiceUnavailableException("database")` raised from
  `repositories/_base.py`'s deadline-exhausted guard before the mock was
  ever reached. Root cause: `tests/test_utils_extended.py`'s
  `TestDeadline*` tests call `utils/deadline.py:set_request_deadline(...)`
  directly and several never reset the `contextvars.ContextVar` afterward
  — a permanently-past deadline then leaks into every later test in the
  same pytest process that touches `run_sync` (every function in
  `wallet_repo.py` does). This is the same failure class as the
  already-tracked A8 finding (leaked test state failing an unrelated
  "victim" test), just via a different mechanism (contextvar, not an
  un-awaited coroutine). Added a local, defensive `autouse` fixture
  (`_clear_request_deadline`) to `test_wallet_repo.py` that resets the
  contextvar to `None` before every test in this file — this makes this
  file's tests deterministic regardless of run order, and as a side effect
  heals the leak for whatever test file runs after it, but does **not** fix
  `test_utils_extended.py`'s own cleanup bug (a different file/feature,
  out of scope for this PR). Confirmed the fix: re-ran
  `pytest tests/test_utils_extended.py tests/test_wallet_repo.py -q --no-cov`
  (the exact pollution scenario) → 229 passed, 1 skipped, 0 failed.
- [x] Reviewed against CLAUDE.md conventions: patch target is
  `repositories.wallet_repo.supabase` (the domain-module binding, not
  `repositories._base.supabase`), matching the documented convention since
  `wallet_repo.py` defines its own functions rather than re-exporting
  `_base`'s generic CRUD helpers. `Decimal` used for all money values in
  test fixtures/assertions (never float). Dual-import fallback lines left
  uncovered and explicitly called out, not chased.
- [ ] Manual repro / staging check — not applicable, test-only change with
  no deployable behavior difference.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (single revert, no data impact).
- [x] Blast radius is stated: isolated to a new test file; no other test
  file's fixtures/classes collide; grepped every other file that touches
  `wallet_repo` symbols (see §4).
- [x] No silent behavior change — this PR changes no shipped behavior.

## Bugs found, not fixed (test-only scope per this initiative)

1. **`mark_stripe_event_processed` (application code, `wallet_repo.py`)** —
   swallows a DB/payment error via `logger.warning(...)` + continue and
   always returns `None` regardless of success or failure, matching the
   exact pattern CLAUDE.md's "Do not silently swallow errors" section
   forbids for payment errors — the caller cannot detect the stamp failed
   short of grepping logs. Contrast with the sibling `unclaim_stripe_event`,
   which signals the same class of failure via a boolean return (its own
   docstring says the caller must escalate on `False`) — that one is a
   documented degrade-with-signal, not a swallow. `mark_stripe_event_processed`'s
   docstring argues the trade-off is bounded (Stripe already got its 2xx;
   a reconciliation job distinguishes stuck-vs-processed rows via
   `stripe_events.processed_at`), so this is likely not a live
   financial-correctness bug today — but it is a real, literal deviation
   from the documented convention and worth a deliberate follow-up decision
   (fix vs. formally accept as intentional). Not fixed here — test-only PR.
2. **Test-suite pollution (test code, `tests/test_utils_extended.py`)** —
   see §9 above for the full root-cause writeup. Several `TestDeadline*`
   tests leak a permanently-past request-deadline `ContextVar` value into
   every later test in the same pytest process, causing order-dependent
   `ServiceUnavailableException` failures in any file that calls
   `repositories._base.run_sync` and runs afterward. Worked around locally
   in `test_wallet_repo.py`; not fixed at the source since that's a
   different file/feature. Flagged in `ACTION_ITEMS.md` for a future
   session to either fix `test_utils_extended.py`'s cleanup or add a
   session-scoped autouse reset to `conftest.py`.

## What was NOT verified

- Not tested against a real Supabase instance or real Postgres RPCs — every
  test mocks `repositories.wallet_repo.supabase` with `unittest.mock.MagicMock`;
  the RPC functions' actual SQL behavior (locking, idempotency dedup) is
  covered separately by `test_wallet_apply_delta_contract.py`'s
  migration-text assertions and the `test_p3_wallet_concurrency.py` /
  `test_p3_promo_concurrency.py` race-simulation tests, not by this PR.
- No visual/UI surface touched — not applicable (backend-only, no frontend
  change).
- Did not independently re-verify every other in-flight session's PR
  didn't also touch this exact file between this PR's branch point and
  push — see the collision check performed at task start (`gh`/GitHub MCP
  search of all 26 open PRs at the time found none touching `wallet_repo.py`
  or a wallet-repo test file) and the pre-push `git fetch origin main`
  re-check described in the PR body.
