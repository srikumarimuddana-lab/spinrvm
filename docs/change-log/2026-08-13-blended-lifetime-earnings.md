# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude Code |
| Surface(s) | backend, driver-app, rider-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (this branch) |
| Related issue or gap ID | Direct product decision, follow-on from A31 (`ACTION_ITEMS.md`); tracked as A32 |

## 1. Issue / gap identified

Two related gaps in how migrated drivers' previous-app money was
presented, both raised by the user after A31 shipped:

1. A driver's Activity screen "Total Earned" and "Avg per Trip" correctly
   excluded previous-app money (by design — that money was already paid
   out), but the exclusion was communicated with technical "legacy" /
   "imported" language and a footnote, rather than a clear, verifiable
   accounting of where the money went.
2. The previous-app money was scheduled to disappear entirely from every
   driver-facing surface after 2026-08-31 (`PREVIOUS_APP_VISIBLE_UNTIL`),
   which would make a driver's own lifetime earnings figure shrink on a
   calendar date — the same trust problem A31 fixed for trip counts, left
   open for the dollar figure.

## 2. Root cause

Not a bug — a product decision superseding two earlier ones (A30 Finding
3/4's badge+explainer, and the `PREVIOUS_APP_VISIBLE_UNTIL` sunset). Both
were deliberate at the time; the user decided, after seeing A31's fix, that
blending + permanence is the better long-term design for a driver-facing
money surface: one honest number, always available, verifiable line item
by line item.

## 3. Fix / remediation

**Backend** — `previous_app_history_visible()` (the sunset gate,
`utils/legacy_rides.py`) is no longer called by any of its three
call sites:
- `get_driver_balance` (`routes/drivers/earnings.py`) — `previous_app_paid_total`
  is now always the real amount, not zeroed after 2026-08-31.
- `get_payout_history` (`routes/drivers/payouts.py`) — `stripe_sync`
  (previous-app transfer) rows are never filtered out of the payout list.
- `build_statement` (`utils/driver_statement.py`) — `previous_app_visible`
  is now always `True`, so statement PDFs/emails keep showing previous-app
  rows and notes permanently. `utils/driver_statement_pdf.py` already
  branches correctly on this flag — no renderer change needed.

The helper function and `PREVIOUS_APP_VISIBLE_UNTIL` constant are
**unchanged** — still correct as a pure date function, just no longer
invoked by these three call sites.

**driver-app Activity screen** (`components/activity/ActivityView.tsx`):
- "All Time" Total Earned = Spinr earnings (`shownEarnings.total_earnings`)
  + `driverBalance.previous_app_paid_total`. Not blended for
  Today/Week/Month — previous-app transfer rows carry an import-batch
  date, not the ride's original earn date, so there's no honest way to
  slice that money into a calendar week/month without fabricating
  precision.
- New "Previously Paid" row in the earnings breakdown, rendered only when
  the amount is non-zero — every row (Fare/Tips/Bonus/Referral/Tax/
  Previously Paid) sums exactly to the Total Earned figure above it.
- Avg per Trip is now `totalEarnings / totalRides` computed client-side
  from the same blended total and the (already all-inclusive, per A31)
  trip count — a simple, honest per-trip average, matching how a driver
  would do the math themselves.
- New "Avg Distance/Trip" stat tile (`totalDistanceKm / totalRides`).
- Removed the ride-card "Imported from your previous account" badge and
  the "N rides ... not counted here" explainer — both are now false or
  redundant once the total is blended.

**driver-app Payout screen** (`app/driver/payout.tsx`):
- "Total Earnings" breakdown item blends the same way (Spinr +
  previous-app), so it matches the Activity screen's figure instead of
  showing a second, different "Total Earnings" number.
- "Previously Paid" added as a 4th breakdown item, additive — only
  rendered for a driver with real previous-app money; a driver with none
  sees the original 3-item row, byte-identical.
- `AVAILABLE BALANCE` (`driverBalance.payable_balance`, the hero figure)
  is **untouched** — it's the real withdrawable amount and must never
  include money already paid by the old app (double-withdraw risk).

**driver-app Payout History screen** (`app/driver/payout-history.tsx`):
- "Previous app" section copy no longer says "shown for your records
  until Aug 31, 2026" or "Not part of your Spinr earnings" — both are now
  false. New copy: "included in your Total Earnings on the balance
  screen."

**rider-app Activity tab** (`app/(tabs)/activity.tsx`):
- Removed the same badge. No total/average change — riders have no
  earnings-exclusion figure to blend.

**admin-dashboard**: **no change.** Per explicit instruction, the
"Imported" badges A30 Finding 4 added to the admin rides list, rider
detail panel, and driver detail tab stay — that surface is Spinr-internal
(support/audit), not customer-facing.

**Backend data model** (`rides.legacy_import_metadata`,
`payouts.payout_type='stripe_sync'`, `PREVIOUS_APP_VISIBLE_UNTIL` constant):
**no change.** The distinction is still fully recorded — required for the
7-year Saskatchewan trip/tax retention rules regardless of what the app
displays — this is a presentation-only decision.

## 4. Risk & impact on existing functionality

- **Blast radius: multi-surface (backend + driver-app + rider-app),
  presentation-only.** No ride state, migration, or write path touched.
- **Money-math grep performed:**
  - `payable_balance` (the actual withdrawable figure, bounds the Stripe
    payout Transfer) is computed identically to before — this change never
    touches it. Confirmed by re-running `test_drivers_extended.py`'s
    `test_balance_excludes_stripe_synced_legacy_payouts` (still passes
    unmodified): `stripe_sync` payouts still never reduce
    `payable_balance`.
  - `total_earnings` on `/drivers/balance` (the field used in the
    documented reconciliation identity `total_earnings == payable_balance
    + pending_payouts + total_paid_out`) is **unchanged** — the blend
    happens only in the driver-app's own client-side display arithmetic
    (`parseFloat(total_earnings) + parseFloat(previous_app_paid_total)`),
    never in the backend field itself. The identity still holds exactly as
    documented in `docs/change-log/2026-08-12-balance-earnings-composition-parity.md`.
  - `get_driver_earnings` (`/drivers/earnings`, the period-summary
    endpoint A31 already fixed) is untouched by this change — its
    `total_earnings`/`average_per_ride` fields stay Spinr-only; the blend
    happens client-side in `ActivityView.tsx` on top of that response.
- **Grepped every caller of `previous_app_history_visible`:** three call
  sites (`get_driver_balance`, `get_payout_history`, `build_statement`),
  all three updated consistently. No other backend module calls it.
- **Grepped every caller of `driverBalance.previous_app_paid_total`** and
  `driverBalance.total_earnings` in driver-app: `ActivityView.tsx` and
  `payout.tsx` are the only two consumers of the balance object's earnings
  fields; both updated. `DriverTopBar.tsx` reads `earnings?.total_earnings`
  (the `/drivers/earnings` object, not `/drivers/balance`) — unaffected,
  still Spinr-only for its "today's earnings" pill, which is correct
  (today's money, not a lifetime figure).
- **Tax/regulatory:** T4A exports (`routes/drivers/tax_exports.py`) and
  admin stored statement totals are untouched — they never read
  `previous_app_history_visible()` in the first place (per
  `utils/legacy_rides.py`'s own docstring: "the sunset is PRESENTATION
  ONLY — admin surfaces, T4A/tax exports, and stored statement totals keep
  the full picture forever").

## 5. User-experience effect

- **Driver-facing.** A migrated driver's Activity and Payout screens now
  show one blended, permanent lifetime-earnings figure instead of a
  Spinr-only figure with technical "legacy"/"imported" framing that was
  scheduled to disappear on a date. Avg per Trip and the new Avg
  Distance/Trip tile give a simple per-trip picture using the same
  numbers a driver would compute themselves. No more ride-card badges on
  driver-app or rider-app.
- **Rider-facing.** Only the badge removal — riders never had an
  earnings-exclusion figure.
- **Admin-facing.** No change — admin retains the full "Imported"
  identification for support/audit use.
- **Visible mid-session?** These are historical-summary screens (Activity,
  Payout, Payout History), not live dispatch/ride-state surfaces — no
  effect on an active trip.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/earnings.py` | `get_driver_balance`: `previous_app_history_visible()` gate removed, `previous_app_paid` always computed | Retire the sunset |
| `backend/routes/drivers/payouts.py` | `get_payout_history`: sunset filter removed, unused import dropped | Retire the sunset |
| `backend/utils/driver_statement.py` | `build_statement`: `previous_app_visible` hardcoded `True`, unused import dropped | Retire the sunset |
| `backend/tests/test_previous_app_sunset.py` | Rewritten: pins "always visible" behavior at all 3 call sites instead of the two-branch cutoff; kept the pure date-helper tests | Regression coverage for the reversal |
| `driver-app/components/activity/ActivityView.tsx` | Blended Total Earned (All Time), new "Previously Paid" row, blended Avg per Trip, new Avg Distance/Trip tile, removed badge + explainer | Core UX change |
| `driver-app/__tests__/components/ActivityView.test.tsx` | Updated/added tests for badge removal + blended totals | Regression coverage |
| `driver-app/app/driver/payout.tsx` | Blended "Total Earnings" breakdown item, new "Previously Paid" 4th item, removed footnote | Consistency with Activity screen |
| `driver-app/app/driver/payout-history.tsx` | "Previous app" section copy: dropped sunset date + exclusion language | Consistency |
| `rider-app/app/(tabs)/activity.tsx` | Removed ride-card badge + its styles | Consistency |
| `ACTION_ITEMS.md` | A32 entry added; A31's now-superseded note flagged | Record the decision |

## 7. Before / after

```python
# Before (backend/routes/drivers/earnings.py::get_driver_balance)
previous_app_paid = Decimal("0")
if previous_app_history_visible():
    previous_app_paid = sum(...)
```
```python
# After
previous_app_paid = sum(...)  # always computed, no date gate
```

```tsx
{/* Before (driver-app ActivityView.tsx) */}
<Text style={styles.totalValue}>${toMoney(totalEarnings)}</Text>
{legacyRideCountInView > 0 && (
  <Text style={styles.legacyExplainer}>
    {legacyRideCountInView} rides from your previous account are shown below
    but not counted here — those were already paid out.
  </Text>
)}
```
```tsx
{/* After */}
<Text style={styles.totalValue}>${toMoney(totalEarnings)}</Text>
{/* totalEarnings = spinrEarnings + previousAppPaid (All Time only) */}
{previousAppPaid > 0 && (
  <View style={[styles.breakdownRow, styles.breakdownRowBorder]}>
    <Text style={styles.label}>Previously Paid</Text>
    <Text style={styles.value}>${toMoney(previousAppPaid)}</Text>
  </View>
)}
```

## 8. Rollback plan

Plain `git revert` on all commits in this change. No data mutation, no
migration, no Stripe/wallet interaction — every change is either a
display-arithmetic tweak (frontend) or the removal of a `if` gate around
an existing, already-correct value (backend). A revert restores the
sunset gate and the badge/explainer exactly as A30/A31 left them, with no
cleanup needed.

## 9. Verification performed

- [x] Automated tests run: backend —
  `pytest tests/test_previous_app_sunset.py tests/test_earnings_coverage.py tests/test_drivers_extended.py tests/test_payouts_coverage.py tests/test_driver_statement.py tests/test_driver_statement_pdf.py -q`
  — 193/193 pass. driver-app — `jest __tests__/components/ActivityView.test.tsx`
  — 9/9 pass.
- [ ] Manual repro steps followed in staging — not available in this
      session.
- [x] Blast-radius grep performed: every caller of
      `previous_app_history_visible`, `driverBalance.previous_app_paid_total`,
      `driverBalance.total_earnings`, and `shownEarnings.total_earnings` in
      driver-app (§4).
- [x] Reviewed against relevant CLAUDE.md conventions: money arithmetic
      (`payable_balance` untouched, verified by the existing
      `test_balance_excludes_stripe_synced_legacy_payouts` still passing
      unmodified); "additive over destructive" (Payout screen's 4th
      breakdown item only renders when non-zero — a driver with no
      previous-app money sees byte-identical UI).
- [ ] Feature-flagged — not applicable per CLAUDE.md's own guidance
      ("this project's `app_settings`-in-DB pattern... ask the user if
      unsure whether an equivalent mechanism exists"); this is a direct,
      explicit product decision from the user in this conversation, not an
      experimental rollout, and — unlike A30/A31 — money-*display* only,
      with `payable_balance` (the only figure with real withdrawal
      consequences) unchanged.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no data touched)
- [x] Blast radius is stated: multi-surface (backend, driver-app,
      rider-app), presentation-only; every consumer of the touched fields
      named in §4
- [x] No silent behavior change: this entire change *is* a deliberate,
      user-directed behavior change to an already-shipped flow (superseding
      A30 Finding 3/4 and the `PREVIOUS_APP_VISIBLE_UNTIL` sunset), and is
      documented as such rather than presented as a bugfix
