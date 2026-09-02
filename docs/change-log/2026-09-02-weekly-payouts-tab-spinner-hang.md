# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-02 |
| Author | Claude (bug report: "Weekly pay tab" spinner never resolves) |
| Surface(s) | backend (admin-dashboard's Weekly Payouts tab consumes it) |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/weekly-payout-spinner-fix` |
| Related issue or gap ID | User report, this session |

## 1. Issue / gap identified

Clicking the admin dashboard's Earnings → **Weekly Payouts** tab shows "Loading
weekly payout data…" with a spinning icon indefinitely — no data, no error
card, ever renders.

## 2. Root cause

The tab's `AutoPayoutsPanel` component calls `GET
/admin/auto-payouts/blocked-drivers`, which runs `find_blocked_drivers()` in
`backend/utils/auto_payout.py`. That function scans the entire `drivers`
table and, for **every driver that fails an eligibility gate** (no Stripe
account, missing GST/SIN, etc.), computes their full payable balance via
`_compute_payable_balance()` — which issued **4–5 sequential Supabase round
trips per driver**. The scan loop capped only the *output* (`len(out) >=
limit`, default 50), not how many drivers it was willing to balance-check
along the way.

A driver who signed up but never completed onboarding (never drove, no
Stripe account) fails the `no_stripe_account` gate immediately, has a $0
balance, and is silently skipped without ever counting toward the output —
so the loop keeps going. On a fleet with any meaningful number of
never-active driver signups (a realistic and expected state pre-launch or
during growth), this loop balance-checks the *entire* `drivers` table before
giving up, at 4–5 sequential round trips each. That is minutes of wall-clock
time with the frontend `fetch()` never settling — from the browser's
perspective, indistinguishable from "stuck forever," since there is no
client-side or server-side timeout anywhere in this path.

This bug pre-dates today's session (`find_blocked_drivers`'s driver scan was
never gated on Stripe-account presence — only the separate weekly-batch
driver-fetch was, and that pre-filter was intentionally removed earlier
today for an unrelated, correct reason: making `no_stripe_account` drivers
visible in the weekly batch's own skip summary, see
`docs/change-log/` "stop hiding no_stripe_account drivers"). That change
did not introduce this bug — `find_blocked_drivers` was never filtered — but
it does mean the fleet-wide `no_stripe_account` population this endpoint
walks through is now also correctly visible elsewhere, which is what
surfaced the report.

## 3. Fix / remediation

Two independent, additive fixes, neither changing what data is returned for
any request that completes within the existing behavior:

1. **`_compute_payable_balance()` now issues its four independent queries
   (completed rides, cancelled rides, driver bonuses, payouts) concurrently**
   via `asyncio.gather` instead of sequentially. Only the
   `ride_incentive_claims` lookup is a genuine second stage (it needs
   `ride_ids` from the rides query) and stays sequential after the gather.
   This is a pure latency win — same queries, same results, same order of
   summation — verified by the full existing `TestComputePayableBalance`
   suite passing unchanged.
2. **`find_blocked_drivers()` now caps the number of gate-failing drivers it
   will balance-check at `_MAX_BALANCE_CHECKS = 300`**, independent of the
   output `limit`. Hitting the cap logs a loud `logger.warning` (not a
   silent truncation) and the function returns whatever it found so far,
   still running the (cheap, bounded) over-cap lookup tail. This bounds the
   endpoint's worst-case latency regardless of how many never-active driver
   signups exist in the fleet.

## 4. Risk & impact on existing functionality

- **Blast radius: `_compute_payable_balance` is called from three places** —
  `find_blocked_drivers` (this fix's target), `_handle_skipped_driver`
  (used by the weekly batch's per-driver skip-notification path in
  `run_weekly_auto_payout`), and indirectly nowhere else (the driver-facing
  `/balance` endpoint in `routes/drivers/earnings.py` has its own, separate
  implementation — parity-tested against this one, not sharing code, so
  unaffected by either fix here). The parallelization fix benefits all
  callers of `_compute_payable_balance` for free; the cap only applies
  inside `find_blocked_drivers`.
- **Known, deliberately out-of-scope related risk:** `_handle_skipped_driver`
  (the weekly batch's per-skip path) has the same "one `_compute_payable_balance`
  call per gate-failing driver, no cap" shape, and — after today's earlier
  `no_stripe_account` visibility fix — now runs for every never-active driver
  in the fleet, every week. It is a background loop, not a request a human
  is waiting on, so it does not reproduce this bug's user-visible symptom,
  and it already benefits from the parallelization fix. Adding a cap there
  would change weekly-batch semantics (a capped driver would silently stop
  being skip-notified) and needs a deliberate product decision, not a
  silent fix bundled into this bug report — flagged here rather than
  changed.
- The `_compute_payable_balance` correctness is protected by the existing
  parity test (`test_parity_with_balance_endpoint`) against
  `routes/drivers/earnings.get_driver_balance` — unaffected by either fix
  since that endpoint's implementation was not touched.

## 5. User-experience effect

- **Admin-facing only.** The Weekly Payouts tab in admin-dashboard now loads
  (or fails cleanly with the existing error card + Try again button) instead
  of spinning forever. No rider/driver-facing change.
- If a fleet has more than 300 gate-failing drivers with genuinely nothing
  payable, the "Blocked drivers" list may now under-report slightly (capped,
  not exhaustive) rather than hang — a `logger.warning` line names this so
  it's visible in ops logs, not silently hidden.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/auto_payout.py` | `_compute_payable_balance`: 4 independent queries now run via `asyncio.gather`. `find_blocked_drivers`: added `_MAX_BALANCE_CHECKS` cap + loud warning log on hit. | Bound both per-driver latency and total scan cost — the two multiplicative factors that made this endpoint's worst case effectively unbounded. |
| `backend/tests/test_auto_payout.py` | New `TestFindBlockedDriversBalanceCheckCap` (2 tests): cap is enforced + logged, and real blocked drivers found before the cap are not lost. | Regression coverage for the actual reported bug — a fleet with more gate-failing drivers than the cap must not hang. |

## 7. Before / after

```python
# Before (utils/auto_payout.py::_compute_payable_balance)
rides = await db_supabase.get_rows("rides", {...status: "completed"...})
...
cancelled_rides = await db_supabase.get_rows("rides", {...status: "cancelled"...})
...
bonus_rows = await db_supabase.get_rows("driver_bonuses", {...})
...
payout_rows = drop_legacy_offset_payouts(await db_supabase.get_rows("payouts", {...}))
# 4-5 sequential round trips, called once per gate-failing driver with no cap
```

```python
# After
rides, cancelled_rides, bonus_rows, raw_payout_rows = await asyncio.gather(
    db_supabase.get_rows("rides", {...status: "completed"...}),
    db_supabase.get_rows("rides", {...status: "cancelled"...}),
    db_supabase.get_rows("driver_bonuses", {...}),
    db_supabase.get_rows("payouts", {...}),
)
# 1 round trip (concurrent), and find_blocked_drivers now stops after
# _MAX_BALANCE_CHECKS = 300 such calls even if more gate-failing drivers exist
```

## 8. Rollback plan

`git-revert-safe`. No migration, no schema change, no new config/flag. Both
fixes are pure code changes to a read-only admin diagnostic endpoint and an
internal helper function — reverting restores the exact prior (slow but
functionally correct when it does complete) behavior with no data-level
cleanup needed.

## 9. Verification performed

- [x] Automated tests: full `backend/tests/test_auto_payout.py` (65 tests,
      including the 2 new cap-regression tests), plus
      `test_payout_toctou.py`, `test_drivers_extended.py`,
      `test_payouts_coverage.py`, `test_earnings_coverage.py` (266 total) —
      all pass, confirming no correctness regression from either fix.
- [ ] Manual repro / staging check — **not performed**. No staging
      environment or real driver-table row counts were available in this
      session to reproduce the exact hang or measure the fix's real-world
      latency improvement; the root cause was established by static code
      analysis (counting round trips, tracing the uncapped loop) and
      confirmed consistent with the reported symptom (spinner never
      resolves, no error), not by watching the bug happen live.
- [x] Blast-radius grep: confirmed `_compute_payable_balance`'s only three
      callers (`find_blocked_drivers`, `_handle_skipped_driver`,
      unrelated separate implementation in `routes/drivers/earnings.py`)
      and reasoned through each.
- [x] Reviewed against CLAUDE.md conventions: no float introduced, no
      Decimal-math change (execution order only), no silent error
      swallowing (the cap logs loudly), background-loop replay-safety
      unaffected (no loop touched, only a request-path helper and an
      admin GET endpoint).
- [ ] Feature-flagged — not flagged. Justification: both changes are
      latency/robustness fixes to a read-only admin diagnostic with no
      behavior change for any request that would have completed before
      (same queries, same results when under the cap); a flag would add
      complexity without a real rollout risk to gate.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no data impact)
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX
      field filled in — the only behavior change (a possible under-report
      past 300 gate-failing drivers) is stated above and logged loudly
