# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude Code |
| Surface(s) | backend, driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (this branch, follow-up to #3896) |
| Related issue or gap ID | `ACTION_ITEMS.md` A33 — same-day follow-up to A32, live user report against A32's own fix |

## 1. Issue / gap identified

A32 (#3896, merged earlier the same day) blended previous-app money into
the driver-app Activity screen's Total Earned by adding
`driverBalance.previous_app_paid_total` (a sum of `payouts` rows with
`payout_type='stripe_sync'`) on top of Spinr-only earnings. Testing that
fix against a real migrated driver account, the user reported it didn't
work: Total Earned, Fare, Tips, Bonus, Referral, Tax, and Avg per Trip all
still showed `$0.00`, directly under a correctly-populated Total Trips /
KM Driven / Online Time.

## 2. Root cause

The A32 blend depended on the previous-app *payout* ledger
(`payouts.payout_type='stripe_sync'`) having a row for this driver's
legacy earnings. That ledger is a record of Stripe transfers, backfilled
separately from the ride import (`booking_import_service.py` imports
rides; a different sync populates `stripe_sync` payout rows). This
driver has real legacy **rides** (with real `driver_earnings`/`tip_amount`
values) but an incomplete payout backfill, so
`previous_app_paid_total` — and therefore A32's blended total — came back
`$0.00` even though the money was genuinely earned and visible on every
individual ride card.

The deeper issue: `get_driver_earnings`'s own money query still filtered
with `EXCLUDE_LEGACY_RIDES` — the exact pattern A31 (earlier that day)
had already fixed for `total_rides`/`total_distance_km`/
`total_duration_minutes`, left unfixed for money at the time because A30
Finding 3 had deliberately excluded legacy money from this endpoint by
design. A32 patched around that exclusion from the outside (adding a
second, less-complete number) instead of removing it at the source.

## 3. Fix / remediation

**Backend** (`routes/drivers/earnings.py::get_driver_earnings`):
- Removed the second, `EXCLUDE_LEGACY_RIDES`-filtered `rides` query
  entirely. A single unfiltered query (`all_completed_rides`, which A31
  already introduced for activity stats) now drives Fare/Tips/Bonus/
  Referral/Tax/Total Earned/Avg per Trip too — the same list, not two
  separately-fetched ones that have to agree.
- This blends legacy rides' money using each ride's own
  `driver_earnings`/`tip_amount`/tax fields — the same source individual
  ride cards already read — so it can't disagree with what a driver sees
  in their trip list, and doesn't depend on a separate payout-ledger
  backfill being complete.
- Correctly period-sliced: each ride carries its own real
  `ride_completed_at` (confirmed: legacy rides keep their ORIGINAL
  completion date on import, only the offsetting payout row is stamped
  with the import date — see `utils/legacy_rides.py`), so Today/Week/
  Month blending is honest, not fabricated precision.
- New `elapsed_days` field: fixed (1/7/30) for today/week/month; for
  "all", measured from the earliest completed ride in view to now (not
  account creation — a long pre-first-trip gap shouldn't dilute a daily
  average).
- `get_driver_balance` / `payable_balance` (the Payout screen's real
  withdrawable-balance math) is **untouched** — still legacy-excluded by
  design. That money was already paid out by the old app; making it
  withdrawable again would be a double-payment bug, not a display fix.
  This change only ever affects `/drivers/earnings`, which was already
  decoupled from `payable_balance`'s reconciliation identity (per A32's
  Change Impact Log).

**driver-app** (`components/activity/ActivityView.tsx`):
- Total Earned and Avg per Trip now read the backend's already-blended
  `total_earnings`/`total_rides` directly — no more client-side
  `previous_app_paid_total` addition (would now double-count, since the
  backend includes it already).
- Removed the "Previously Paid" breakdown row A32 added — redundant now
  that Fare/Tips/Bonus/Tax include legacy money directly. Removed the
  screen's `fetchDriverBalance()` call along with it (nothing on this
  screen reads `driverBalance` anymore).
- **New, requested directly alongside this fix:**
  - "Avg Trips/Day" tile next to Total Trips (`total_rides / elapsed_days`).
  - "Avg KM/Day" tile next to Total KM Driven (`total_distance_km / elapsed_days`).
  - "Online Time" tile split into "Total Online Time" + "Avg Online
    Time/Day", both formatted as `Xh Ym` (a whole-hours-only "0h" reads as
    no time online for a daily average under an hour, which is common and
    not the same as zero).

## 4. Risk & impact on existing functionality

- **Blast radius: backend (1 function) + driver-app (1 screen),
  presentation/aggregation only.** No ride state, migration, or write
  path touched.
- **`payable_balance` unaffected** — `get_driver_balance` is a separate
  function with its own, still-filtered, `EXCLUDE_LEGACY_RIDES` query;
  this change doesn't touch it. Confirmed by the full `test_drivers_extended.py`
  + `test_earnings_coverage.py` run passing unmodified for every
  `/balance`-specific test.
- **Grepped every consumer of `get_driver_earnings`'s response fields**
  (`total_earnings`, `average_per_ride`, `total_rides`): driver-app
  `ActivityView.tsx` is the only frontend consumer (`DriverTopBar.tsx`
  reads a different call — same endpoint, "today" period, for its
  earnings pill; unaffected by the aggregation change since "today" was
  already period-accurate and rarely has legacy rides in view for an
  actively-driving migrated driver, and if it does, now correctly
  includes them instead of hiding them). No other backend module calls
  `get_driver_earnings` directly.
- **T4A / tax exports** (`routes/drivers/tax_exports.py`) and stored
  admin statement totals: untouched, don't call this function.
- **`average_per_ride`'s docstring/behavior changed**: previously
  "deliberately divided by the money-rides count, not diluted by $0
  legacy trips" — now a simple `total / total_rides` over the same
  (now-blended) numerator and denominator. This is an intentional
  reversal, explicit in both the code comment and this log, not a
  regression — matches the user's own stated formula ("average is just a
  simple calculation from the total earned money").

## 5. User-experience effect

- **Driver-facing.** A migrated driver's Activity screen Total Earned,
  Fare, Tips, Bonus, Referral, Tax, and Avg per Trip now correctly reflect
  every completed ride (legacy included) instead of showing `$0.00` next
  to a populated trip count — this was reported as broken by a real user
  testing the just-shipped A32 fix, so this is a direct bug fix, not a
  new feature. Three new stat tiles (Avg Trips/Day, Avg KM/Day, Avg Online
  Time/Day) give a per-day picture alongside the existing totals, per
  direct user request in the same conversation.
- **Visible mid-session?** Historical-summary screen, not live
  dispatch/ride-state — no effect on an active trip.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/earnings.py` | `get_driver_earnings`: single unfiltered ride query drives both activity and money; new `elapsed_days` field | Fix the root cause; back the new per-day tiles |
| `backend/tests/test_earnings_coverage.py` | `TestGetDriverEarningsLegacyActivityStats` rewritten for blended-money behavior | Regression coverage |
| `driver-app/components/activity/ActivityView.tsx` | Total Earned/Avg per Trip read the blended backend total directly; removed the "Previously Paid" row and `fetchDriverBalance()` call; added Avg Trips/Day, Avg KM/Day, Total+Avg Online Time tiles | Fix + requested additions |
| `driver-app/__tests__/components/ActivityView.test.tsx` | Updated for backend-driven blend and new tiles | Regression coverage |
| `driver-app/store/driverStore.ts` | `EarningsSummary` type: added `elapsed_days: number` | Type support for the new field |
| `ACTION_ITEMS.md` | A33 entry added; A32 follow-up note added | Record the fix |

## 7. Before / after

```python
# Before (backend/routes/drivers/earnings.py::get_driver_earnings)
filters = {"driver_id": driver["id"], "status": RideStatus.COMPLETED, **EXCLUDE_LEGACY_RIDES}
rides = await db_supabase.get_rows("rides", filters, limit=10000)
...
all_completed_rides = await db_supabase.get_rows("rides", _activity_filters, limit=10000)
# money summed over `rides` (legacy excluded); activity stats over `all_completed_rides`
```
```python
# After
all_completed_rides = await db_supabase.get_rows("rides", _activity_filters, limit=10000)
rides = all_completed_rides
# money AND activity stats both summed over the same unfiltered list
```

```tsx
{/* Before (driver-app ActivityView.tsx) */}
const previousAppPaid = period === 'all' ? parseMoney(driverBalance?.previous_app_paid_total) : 0;
const totalEarnings = spinrEarnings + previousAppPaid;
```
```tsx
{/* After */}
const totalEarnings = parseMoney(shownEarnings?.total_earnings); // already blended by the backend
```

## 8. Rollback plan

Plain `git revert`. No data mutation, no migration. Reverts to A32's
behavior (payout-ledger-based blend) — still imperfect for drivers with
an incomplete payout backfill, but not a regression from before A32
shipped.

## 9. Verification performed

- [x] Automated tests run: backend —
  `pytest tests/test_earnings_coverage.py tests/test_drivers_extended.py tests/test_previous_app_sunset.py tests/test_payouts_coverage.py tests/test_driver_statement.py tests/test_driver_statement_pdf.py -q`
  — 193/193 pass (42/42 in `test_earnings_coverage.py` specifically).
  driver-app — `jest __tests__/components/ActivityView.test.tsx` — 8/8
  pass. `tsc --noEmit` clean on driver-app.
- [ ] Manual repro against the reporting driver's real account — not
      available in this session; the fix is reasoned from the reported
      symptom (real rides, real trip count, $0 money) matching exactly
      what an `EXCLUDE_LEGACY_RIDES`-filtered-but-empty `rides` list with
      a non-empty `all_completed_rides` list would produce.
- [x] Blast-radius grep performed: every consumer of
      `get_driver_earnings`'s response fields (§4).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no data touched)
- [x] Blast radius is stated: backend (1 function) + driver-app (1
      screen); `payable_balance` explicitly confirmed untouched
- [x] No silent behavior change: `average_per_ride`'s changed semantics
      (no longer "not diluted by $0 legacy trips") is called out
      explicitly in §4, not left for a reader to discover
