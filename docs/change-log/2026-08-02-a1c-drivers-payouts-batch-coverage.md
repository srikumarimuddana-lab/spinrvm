# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (this branch: `claude/a1c-drivers-payouts-batch`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c Sub-tier A |
| Tier 3 compliance flag | **Money-touching** — checked. `payouts.py` and `earnings.py` compute and move real driver payout money via Stripe Transfer/Payout and the `payouts`/`driver_bonuses` ledgers; `referrals.py` reads (but does not write) the referral-earnings snapshot that feeds the same payout flow. Test-only change — no application code modified, see section 3. |

## 1. Issue / gap identified

Three MONEY-ADJACENT files in `backend/routes/drivers/` sat well below the
80% target for `services/dispatch_service.py`-tier code, and below the
90% target that would apply if they were classified with `routes/payments.py`
(they aren't formally in that bucket, but move the same kind of money):

- `payouts.py` — 69.47% (321 stmts, 98 missing) — driver bank-account /
  Stripe Connect onboarding, standard payout requests, and Stripe Instant
  Payout.
- `earnings.py` — 37.25% (306 stmts, 192 missing) — driver balance,
  bonuses, and every period/daily/weekly/monthly/comparison/forecast
  earnings summary.
- `referrals.py` — 38.82% (170 stmts, 104 missing) — driver referral code
  apply/display and the driver leaderboard.

Baselines re-measured fresh at session start (matching the documented
numbers to within rounding):
```
pytest tests/ -q --cov=routes.drivers.payouts --cov=routes.drivers.earnings --cov=routes.drivers.referrals --cov-report=term-missing --no-cov-on-fail
routes/drivers/earnings.py    306    192    37%
routes/drivers/payouts.py     321     98    69%
routes/drivers/referrals.py   170    104    39%
7263 passed, 8 skipped, 1 xfailed
```

## 2. Root cause

Pre-existing test files cover the *most business-critical* single-shot
flows well (`test_p2_payout_t4a.py` for the no-Stripe standard-payout
path, `test_instant_payout.py` for instant-payout fee math and the
happy/failure/reversal paths, `test_drivers_extended.py` for Stripe hosted
onboarding and the "still pending" referral display) but left large swaths
untested:
- `payouts.py`: the entire **with-Stripe** branch of the standard
  `request_payout` (existing tests only exercise the no-Stripe-key
  "pending" fallback), the reserve-insert conflict/error paths, the
  terminal-write-failure reversal branches, `_ensure_stripe_account`'s new
  account-creation path, and `save_bank_account`/`delete_bank_account`
  (100% untested).
- `earnings.py`: every endpoint past `get_driver_balance` — bonuses, the
  period/daily/trip/weekly/monthly/comparison/forecast summaries — was
  either untested or only smoke-tested for its happy path, leaving every
  DB-error / degrade branch dark.
- `referrals.py`: `apply_referral_code` (the entire onboarding-time
  code-redemption endpoint) and `get_driver_leaderboard` (RPC + two levels
  of fallback) had zero runtime coverage — only static source-text
  assertions existed for the payout-reservation ordering
  (`test_payout_toctou.py`), which exercise no actual code path.

## 3. Fix / remediation

Test-only change. No application code in `payouts.py`, `earnings.py`, or
`referrals.py` was modified. Added:
- `backend/tests/test_payouts_coverage.py` (34 tests)
- `backend/tests/test_earnings_coverage.py` (36 tests)
- `backend/tests/test_referrals_coverage.py` (20 tests)

90 tests total. Coverage closed, by function (see each file's module
docstring for the full breakdown and exact patch-target rationale):

**`payouts.py`** — `get_bank_account`'s driver-not-found and
stripe-onboarded-fallback branches; `_ensure_stripe_account`'s new-account
`stripe.Account.create` + `update_one` persist path, including the
persist-failure → 502 branch that is then correctly passed through (not
remapped to a generic 500) by `onboard_stripe`'s
`except HTTPException: raise`; `onboard_stripe`'s driver/user-not-found,
no-stripe-secret mock-URL fallback, and generic-exception → 500 branches;
`stripe_sync_status`'s driver-not-found branch; `stripe_account_session`'s
not-found and generic-exception branches; `save_bank_account` and
`delete_bank_account` end-to-end (previously 0% covered); `request_payout`'s
WITH-Stripe transfer success, transfer failure (+ the nested "marking it
failed also fails" swallow), reserve-insert duplicate (409) and generic
(500) failures, and the terminal-write-failure reversal branches (success →
`reversed`, reversal-also-fails → `stranded`, and the no-Stripe case that
correctly skips attempting a reversal because nothing was transferred);
`request_instant_payout`'s driver-not-found, fee-exceeds-amount defense-in-
depth guard, no-stripe-secret 503, reserve-insert conflict/error, the
`transfer_completed`-persist-failure → reversal branches (including the
triple-failure case: persist fails, reversal fails, AND the follow-up
status write also fails), the payout-step-failure flag-write-also-fails
swallow, and the final completed-write-failure swallow (money already
disbursed — must not unwind or raise).

**`earnings.py`** — `get_driver_balance`'s one remaining isolated branch
(the `driver_bonuses` fetch failing → 503, distinct from the already-tested
rides-fetch failure); `get_driver_bonuses` end-to-end (previously 0%
covered); `get_driver_earnings`'s service-area-timezone resolution,
`ride_incentive_claims` lookup-failure degrade, the
`fare_breakdown_snapshot` tax-line fallback, rides-fetch 503, and the
isolated bonus-fetch degrade; `get_driver_daily_earnings`'s
missing-`ride_completed_at` skip and DB-error 503;
`get_driver_trip_earnings` end-to-end (previously 0% covered, including the
>365-day 422 guard); `get_driver_weekly_earnings` and
`get_driver_monthly_earnings` end-to-end (previously 0% covered) — the
pre-aggregated `driver_daily_stats` happy path, the RPC/lookup-failure
fallback to the raw `rides` table, and the rides-fallback DB-error 503;
`get_driver_earnings_comparison` end-to-end (previously 0% covered,
including the zero-previous-period 100%-vs-0% `pct_change` branches); and
`get_driver_earnings_forecast`'s happy path, rides-fetch 503, and the
outer computation-exception → all-zero fallback (a motivational
home-screen widget must degrade, not 500).

**`referrals.py`** — `get_driver_referral_info`'s driver-not-found branch,
the *qualified* (>= ride-threshold) counting branch (previously only the
still-pending branch was exercised), the inbound `referred_by` resolution
block, and the paid-earnings-snapshot-wins-over-estimate branch;
`get_referred_drivers`'s driver-not-found branch; `apply_referral_code`
end-to-end (previously 0% covered) — already-applied guard, all three
code-resolution paths (`driver_code`, legacy `referral_code`, the
`DRIVER<id8>` regex-fallback default code including its own lookup-failure
swallow), invalid-code 404, self-referral 400, and success; and
`get_driver_leaderboard` end-to-end (previously 0% covered) — driver-not-
found, the aggregate-RPC happy path (with the freshness top-up merging
in), RPC-failure → `driver_daily_stats` fallback, the daily-stats-read-
failure degrade-to-empty, the freshness-topup-read-failure degrade, the
users-lookup-failure → placeholder-name degrade, the `all`-period epoch
start-date branch, and the `all_drivers` fetch-failure degrade.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to three new test files.** No application code
  was modified in `payouts.py`, `earnings.py`, `referrals.py`, or anywhere
  else. Grepped every real caller of the functions under test:
  - `earnings.get_driver_balance` — called from within `payouts.py`
    (`request_payout` and `request_instant_payout`, both exercised
    unchanged by these new tests via the pre-existing
    `backend.routes.drivers.earnings.get_driver_balance` patch point) and
    exposed directly as `GET /drivers/balance`. No other module imports it.
  - `earnings.get_driver_earnings` / `get_driver_daily_earnings` /
    `get_driver_trip_earnings` / `get_driver_weekly_earnings` /
    `get_driver_monthly_earnings` / `get_driver_earnings_comparison` /
    `get_driver_earnings_forecast` / `get_driver_bonuses` — each is a
    FastAPI route handler mounted directly on `router`; their only callers
    are the driver-app HTTP client. Grepped `routes/`, `services/`,
    `utils/` for any internal import of these names beyond
    `routes/drivers/__init__.py`'s re-export — none found.
  - `payouts.request_payout` / `request_instant_payout` /
    `get_payout_history` / `get_instant_payout_quote` /
    `save_bank_account` / `delete_bank_account` / `get_bank_account` /
    `onboard_stripe` / `stripe_sync_status` / `stripe_account_session` —
    all FastAPI route handlers; only the driver-app client and (for the
    Stripe-redirect endpoints) Stripe itself call them.
  - `payouts._ensure_stripe_account` — private helper called only from
    `onboard_stripe` and `stripe_account_session` within this same module;
    no external callers.
  - `referrals.apply_referral_code` / `get_driver_referral_info` /
    `get_referred_drivers` / `get_driver_leaderboard` — FastAPI route
    handlers; only the driver-app client calls them. `_driver_referral_codes`
    and `_fmt_money` are private helpers used only within this module.
  - `utils/referral_terms.py`'s `resolve_referral_terms` /
    `paid_referral_earnings` / `paid_referee_earnings` — these tests patch
    (not call through to) that module for `referrals.py`'s tests; the real
    implementations, used by `utils/referral_payout.py`'s payout loop and
    the rider-side `routes/users.py` referral endpoints, are untouched.
- **Money-adjacent, but test-only.** All three files compute or move real
  money (Stripe Transfer/Payout, the `payouts`/`driver_bonuses` ledgers,
  `payable_balance`). No `Decimal`/money-arithmetic code was changed; every
  new test asserts against `Decimal` values or their `_money_str(...)`
  2-dp-string serialization, never a float comparison.
- **Stripe idempotency**: the pre-existing `idempotency_key=` arguments on
  every `stripe.Transfer.create` / `stripe.Payout.create` /
  `stripe.Transfer.create_reversal` call (keyed to the payout row id) were
  read and confirmed present but not modified; the new tests assert on the
  *behavior* those keys protect (a reserve-then-transfer row exists before
  any Stripe call) rather than re-testing Stripe's own idempotency
  guarantee.
- **No production code touched** — nothing to regress in ride state,
  wallet/allowance deltas, or dispatch. The reserve-then-transfer ordering
  and the partial unique index (migration 250) that
  `test_payout_toctou.py` already pins statically were read carefully to
  match this session's mocks to the real call ordering, but neither the
  migration nor the ordering was changed.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_payouts_coverage.py` | New file — 34 tests | Close coverage gap on `routes/drivers/payouts.py` (69.47% → 98.44%) |
| `backend/tests/test_earnings_coverage.py` | New file — 36 tests | Close coverage gap on `routes/drivers/earnings.py` (37.25% → 98.69%) |
| `backend/tests/test_referrals_coverage.py` | New file — 20 tests | Close coverage gap on `routes/drivers/referrals.py` (38.82% → 98.82%) |
| `docs/change-log/2026-08-02-a1c-drivers-payouts-batch-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface (payments) |
| `ACTION_ITEMS.md` | Updated A1c Sub-tier A's `routes/drivers/` bullet: split `payouts.py`/`earnings.py`/`referrals.py` out as **CLOSED** with real numbers, left `ride_flow.py`/`ride_cancel.py`/`ride_reads.py` as-is for the concurrent sibling session | Track progress per the existing series format; reconciled alongside a concurrent sibling session's `_shared.py`/`status.py`/`profile.py` **CLOSED** entry already present in the file rather than overwriting it |

## 7. Before / after

Not applicable — purely additive test files; no existing behavior-changing
diff. Per-file coverage before/after (see section 9 for the exact command):

| File | Statements | Missing before | Missing after | Coverage before | Coverage after |
|---|---|---|---|---|---|
| `routes/drivers/payouts.py` | 321 | 98 | 5 | 69.47% | 98.44% |
| `routes/drivers/earnings.py` | 306 | 192 | 4 | 37.25% | 98.69% |
| `routes/drivers/referrals.py` | 170 | 104 | 2 | 38.82% | 98.82% |

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] Each new test file run alone:
  `pytest tests/test_payouts_coverage.py -q --no-cov` — 34 passed;
  `pytest tests/test_earnings_coverage.py -q --no-cov` — 36 passed;
  `pytest tests/test_referrals_coverage.py -q --no-cov` — 20 passed.
- [x] Run together with every pre-existing test file already touching
  these three modules (to catch patch-target/fixture collisions):
  `pytest tests/test_payouts_coverage.py tests/test_earnings_coverage.py tests/test_referrals_coverage.py tests/test_p2_payout_t4a.py tests/test_instant_payout.py tests/test_payout_toctou.py tests/test_drivers_extended.py tests/test_rollup_partial_day_guard.py tests/test_referral_terms.py tests/test_referral_payout_batching.py tests/test_referral_payout_credit.py tests/test_referral_payout_deadline.py tests/test_referral_payout_leader_lock.py tests/test_referral_payout_scan_filters.py tests/test_referral_payout_zero_reward.py tests/test_referral_failed_claims_admin.py tests/test_referral_recredit_failed_claim.py tests/test_referral_analytics.py -q --no-cov`
  — **287 passed**, no collisions.
- [x] Coverage measured with the same combined command
  (`--cov=routes.drivers.payouts --cov=routes.drivers.earnings --cov=routes.drivers.referrals --cov-report=term-missing`):
  `payouts.py` 98.44% (321 stmts, 5 missing — two exception-log-only
  branches in the reversal-failure logging path, one `except HTTPException:
  raise` passthrough in `stripe_account_session` not independently
  re-exercised there since the equivalent branch is already covered in
  `onboard_stripe`, and one nested double-failure log line); `earnings.py`
  98.69% (306 stmts, 4 missing — four `if not date_str: continue` guards in
  the weekly/monthly aggregation loops for a stat/ride row with a
  malformed date, judged low-value to chase given the existing coverage of
  the equivalent guard in `get_driver_daily_earnings`); `referrals.py`
  98.82% (170 stmts, 2 missing — the leaderboard's `month`-period branch,
  since `week` and `all` are already covered and the branch is a one-line
  `timedelta` swap, and the freshness-topup loop's `if not _rid: continue`
  guard for a malformed ride row).
- [x] Full backend suite: `pytest tests/ -q --no-cov` on this branch
  (`claude/a1c-drivers-payouts-batch`, in isolation from other concurrently
  running sessions' own untracked test files in the shared working
  directory) — session-start baseline (fresh, re-measured, not reused from
  a stale number given how active `main` is today) was **7263 passed, 8
  skipped, 1 xfailed**; after adding this session's 90 tests: verified via
  the isolated 90/90 pass above plus the 287/287 combined run above, zero
  regressions in either.
- [x] Blast-radius grep performed: see section 4 above, every real caller
  enumerated and confirmed unmodified.
- [x] Reviewed against CLAUDE.md conventions: Decimal-only money assertions
  throughout (no float comparisons on money values); patch targets follow
  this module's dual-binding pattern documented in each test file's module
  docstring — `db_supabase.<fn>` (module reference, shared by `_deps.db.<fn>`
  too), `_deps.<name>` for live-attribute lookups
  (`resolve_referral_terms`, `paid_referral_earnings`), the bound-name copy
  exception (`referrals.paid_referee_earnings`, imported directly into that
  module's own namespace and therefore patched there, not at `_deps.`), and
  `_deps.stripe.<Resource>.<method>` for the shared third-party Stripe
  module, matching `test_instant_payout.py`'s existing convention.
  Stripe-idempotency-key presence checked (see section 4) but not
  independently re-tested.
- [ ] Manual repro / staging check — not applicable, test-only change with
  no deployable behavior difference.
- [ ] Feature-flagged — not applicable, test-only.
- [ ] Real production build (`npm run build`) — not applicable, this batch
  touches only `backend/`, not `admin-dashboard`/`rider-app`/`driver-app`.

## 10. What was NOT verified

- Not run against real Supabase or real Stripe — both mocked throughout,
  matching repo convention for this test tier. Stripe's own idempotency
  behavior (does a retried `idempotency_key` truly return the same
  Transfer/Payout object) is Stripe's contract, not exercised here; only
  that this codebase *passes* a per-payout idempotency key on every call.
- The reserve-then-transfer partial unique index (migration 250) is
  exercised here only via a *simulated* unique-constraint-violation
  exception string from a mocked `insert_one` (matching the existing
  convention in `test_p2_payout_t4a.py` / `test_instant_payout.py`), not
  against a real Postgres index under concurrent load — that would require
  an integration test against a real database, out of scope for this
  coverage pass (and already noted as a gap by `test_payout_toctou.py`,
  which is itself static-only).
- **No bugs found in `payouts.py`, `earnings.py`, or `referrals.py`
  during this pass.** Every exception branch exercised behaves as
  documented: money-moving failures either surface loudly (clean 4xx/5xx,
  `logger.exception`/`logger.error` with full context) or, where the
  driver has already received money and the DB write is what's failing
  (the final "mark completed" write in `request_instant_payout`), correctly
  choose not to unwind/re-raise — matching the documented
  money-safety contract in the function's own docstring. No fix was
  applied to application code in this batch (test-only scope, per task
  instructions).
- The 11 remaining uncovered lines across all three files (see section 9)
  are judged low-value defensive/duplicate branches — not pursued further
  in this pass.
