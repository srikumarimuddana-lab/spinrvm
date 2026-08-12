# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude (automated, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend, driver-app (consumer, no frontend file changed) |
| Domain (Sentry tag) | payments (driver earnings/payouts) |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | `ACTION_ITEMS.md` A28's "/balance vs /earnings composition can diverge" item, `docs/audit/2026-08-11-driver-rider-migration-audit.md` Phase 3 cross-surface findings #6/#7 |

## 1. Issue / gap identified

`GET /drivers/balance` (`payable_balance`, the number that actually bounds
a driver's Stripe payout Transfer) and `GET /drivers/earnings` (the
period-earnings screen) disagreed on two independent axes:

1. **Source of truth for base ride income.** `/balance` always recomputed
   from raw fare components (`base_fare + distance_fare + time_fare +
   tip_amount`), ignoring the stored `driver_earnings` column even when
   it's set. `/earnings` and driver statements trust `driver_earnings`
   first (`_ride_income()`), only falling back to fare components for
   legacy rows predating that column. A manual correction to
   `driver_earnings` (e.g. a support adjustment) would silently not show
   up on the balance screen.
2. **What gets added on top.** `/balance` only added quest/referral
   bonuses. `/earnings` and driver statements also add per-ride incentive
   claims (`ride_incentive_claims`), cancellation/no-show fees the driver
   earned, and GST/PST passed through to the driver as income. A driver
   who earned a cancellation fee or incentive bonus saw a higher number on
   `/earnings` than on `/balance` — the number that actually governs what
   they can withdraw.

## 2. Root cause

`/balance` predates (or wasn't updated alongside) the fuller composition
`/earnings` and `utils/driver_statement.py`'s official driver statements
already use and agree with each other on — confirmed by direct comparison:
`driver_statement.py`'s `total = ride_earnings + bonus_total +
incentive_total + cancel_fees + tax_collected` is the exact same five-term
sum `/earnings` computes independently. `/balance` was the only outlier,
missing 3 of the 5 terms and using a different source for the first term.

## 3. Decision

Filed in `ACTION_ITEMS.md` A28 as needing a product decision (not a blind
code change) per CLAUDE.md's escalation guidance for money-visible
behavior changes on a live-tested surface. **User decision (2026-08-12):
`payable_balance` should include everything `/earnings` does** — a driver
who earned an incentive bonus or cancellation fee should be able to
withdraw it, not just see it reported.

## 4. Fix / remediation

- Added a shared `_ride_tax()` helper to `routes/drivers/_shared.py`
  (mirrors `utils/driver_statement.py`'s existing one — that module can't
  import from `routes`, so the two stay independently maintained but now
  both exist as named, documented functions instead of one being inline).
- `get_driver_balance`:
  - Base ride income switched from raw fare-component sum to
    `_ride_income(r)` per ride (Axis 1 fix).
  - Added `_ride_tax(r)` summed across completed rides.
  - Added a `ride_incentive_claims` fetch/sum for the same ride IDs
    (mirrors `/earnings`' existing query).
  - Added a lifetime (no date filter, matching every other sum in this
    endpoint) cancelled-rides fetch, summing `cancellation_fee_driver`.
  - `total_earnings` = ride income + tax + incentives + cancel fees +
    bonuses (previously: ride income + bonuses only).
  - `payable_balance` = that same total − all money-out payouts (formula
    unchanged; only the composition of `total_earnings` feeding it grew).
  - Added `total_incentives`/`total_cancel_fees`/`total_tax` to the
    response for transparency, matching `/earnings`' field names.
- Refactored `/earnings`' own inline tax-computation loop to call the new
  shared `_ride_tax()` helper instead of duplicating the same logic a
  third time (no behavior change there — same logic, same result).

## 5. Risk & impact on existing functionality

- **Blast radius**: isolated to `get_driver_balance` (1 endpoint) plus a
  pure refactor of `get_driver_earnings`' tax computation (same output,
  different code path). Grepped `total_earnings`/`payable_balance` usage
  in `driver-app` — `payout.tsx` is the only consumer with a specific
  reconciliation expectation: "Total Earnings" tile must equal "Available
  Balance" + "Pending" + "Paid Out" (verified this identity holds exactly,
  both before and after this change, by construction — new regression test
  added).
- No other surface (admin dashboard, rider-app, T4A, statements) reads
  `/drivers/balance` — this is a driver-app-only endpoint.
- **This is a real, immediate, user-visible balance increase** for any
  driver who has earned an incentive bonus, a cancellation fee, or tax
  pass-through that wasn't previously reflected in `payable_balance`. Not
  new money being created — it's money the driver already earned
  (verifiable via `/earnings` and their driver statement) that `/balance`
  was previously under-reporting and under-allowing-withdrawal-of.
- No `financial_events`/ledger/wallet table changed — this is purely a
  read-path aggregation fix. No migration, no backfill, no reconciliation
  needed for past periods (the underlying `ride_incentive_claims`,
  cancelled-ride, and `tax_amount` data already existed; `/balance` simply
  wasn't summing it).

## 6. User-experience effect

**Driver-facing, immediate, mid-session if the driver has the payout
screen open.** A driver with existing unclaimed incentive bonuses,
cancellation fees, or tax pass-through will see their "Available Balance"
and "Total Earnings" figures increase the moment this deploys — a genuine,
one-time jump reflecting money they already earned but couldn't
previously see/withdraw via this screen. This should be communicated to
support ahead of deploy in case drivers ask "why did my balance suddenly
go up."

## 7. Before / after

```python
# Before
total_earnings = sum(
    (_d(r.get("base_fare") or 0) + _d(r.get("distance_fare") or 0)
     + _d(r.get("time_fare") or 0) + _d(r.get("tip_amount") or 0)
     for r in rides),
    Decimal("0"),
)
# ... no tax, no incentives, no cancellation fees added anywhere
return {
    "total_earnings": _money_str(total_earnings + total_bonuses),
    "payable_balance": _money_str(total_earnings + total_bonuses - total_payouts),
    ...
}
```

```python
# After
ride_earnings = sum((_ride_income(r) for r in rides), Decimal("0"))
total_tax = sum((_ride_tax(r) for r in rides), Decimal("0"))
total_incentives = sum((_d(c.get("bonus_amount") or 0) for c in ride_incentive_claims), Decimal("0"))
total_cancel_fees = sum((_d(r.get("cancellation_fee_driver") or 0) for r in cancelled_rides), Decimal("0"))
total_earnings = ride_earnings + total_tax + total_incentives + total_cancel_fees
return {
    "total_earnings": _money_str(total_earnings + total_bonuses),
    "payable_balance": _money_str(total_earnings + total_bonuses - total_payouts),
    "total_incentives": _money_str(total_incentives),
    "total_cancel_fees": _money_str(total_cancel_fees),
    "total_tax": _money_str(total_tax),
    ...
}
```

## 8. Rollback plan

`git revert` — pure code change, no schema/data mutation, no migration.
Reverting restores the prior (narrower) `payable_balance` composition;
drivers' actual bank/Stripe transfers already made are unaffected either
way — this only changes what a read-only balance query reports and what
ceiling it puts on a *future* withdrawal request. No data cleanup needed.

## 9. Verification performed

- [x] `pytest backend/tests/test_drivers_extended.py backend/tests/test_earnings_coverage.py -q --no-cov` → 131 passed (127 prior + 4 new)
- [x] Independently verified 2 of the 4 new tests fail pre-fix with the
  exact predicted values (`10.00` vs expected `18.60`; `11.00` vs expected
  `12.00`); the 3rd (`total_earnings == payable_balance + pending +
  paid_out` identity) holds by construction on both sides — kept as a
  standing invariant guard, not a pre/post-fix discriminator; the 4th
  (basic balance summary) is a pre-existing test, updated only to
  discriminate the new cancelled-rides fetch by status filter (was
  previously ambiguous — silently harmless by coincidence, not by design)
- [x] Broader affected-test sweep (`test_drivers_extended.py`,
  `test_earnings_coverage.py`, `test_admin_drivers_coverage.py`,
  `test_t4a_email.py`, `test_payouts_coverage.py`, `test_p1_security.py`,
  `test_driver_deletion_tombstone.py`, `test_p2_payout_t4a.py`,
  `test_instant_payout.py`, `test_previous_app_sunset.py`) → 367 passed
- [ ] Full backend suite — running, will confirm before merge
- [x] Blast-radius grep on `driver-app` for `total_earnings`/
  `payable_balance` consumers — only `payout.tsx`, its reconciliation
  identity confirmed to still hold

## What was NOT verified

- Not tested against a live driver account with real
  `ride_incentive_claims`/cancellation-fee/tax data — verified at the unit
  level with fixture data proving the composition is now correct.
- No visual regression tooling exists for driver-app; the payout screen's
  displayed numbers were reasoned about via the code (`payout.tsx`'s
  reconciliation-identity requirement), not screenshotted.
- Whether any driver has already contacted support confused about a
  balance discrepancy between `/balance` and `/earnings` — not checked
  (would require support-ticket access this sandbox doesn't have).
