# Change Impact & Risk Log — Exclude previous-app imported rides from driver money math

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude Code session (operator: srikumarimuddana) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/stripe-payout-refresh-bgz9s4` |
| Related issue or gap ID | Operator report: "in driver payout the earning statements are not refreshed — why is the legacy import amount still showing and adding to the earnings" |

## 1. Issue / gap identified

Driver earnings, statements and history counted rides imported from the previous app as Spinr income. The driver payout screen's "Total earnings" included money the old app had already paid, and earnings statements showed inflated totals — which read to the operator as statements failing to refresh.

## 2. Root cause

`services/booking_import_service.py` imported the old app's completed bookings into `rides` with real `driver_earnings` (so trip history was preserved), and wrote one offsetting `payouts` row per driver (`payout_type='legacy_import'`, `status='completed'`) equal to the imported earnings. That made `payable_balance` net to $0 — correct — but left every **total** wrong on both sides: earnings counted previous-app money, and "total paid out" counted a synthetic offset that was never a bank transfer.

The statement bug is worse than a display inflation: an imported ride keeps its **original** `ride_completed_at` while the offset payout is stamped with the **import** date. The two halves therefore land in different statement periods — one period shows inflated earnings with no offset, another shows a large payout with no matching earnings.

Separately, `routes/drivers/tax_exports.py` and `utils/t4a_annual_job.py` both carried a comment asserting "the old app's rides were never imported" as justification for adding `stripe_sync` transfer totals on top of ride earnings. That claim was false once the booking importer ran: a driver with both imported rides and Stripe transfers covering the same legacy period had that income reported to the CRA **twice**, and could be pushed over the $500 T4A threshold on money Spinr never paid.

## 3. Fix / remediation

New `backend/utils/legacy_rides.py` centralizes the rule. Legacy-imported rides are excluded from money math (server-side PostgREST `is.null` filter on `legacy_import_metadata`), and their paired `legacy_import` offset payouts are dropped alongside them.

**Both halves always move together.** Since the offset equals the imported earnings by construction:

```
before:  (real rides + legacy rides) - (real payouts + legacy offset)
after:    real rides                 -  real payouts
```

`payable_balance` is therefore **unchanged**, while totals stop reporting previous-app money. Dropping only one half would silently move a driver's balance, which is why both helpers are documented as a pair.

Applied to: driver balance, `/earnings` (all periods), daily/weekly/monthly/trips/comparison/forecast, earnings statements (driver + admin download/email), the admin driver Payouts tab, T4A summary, and the annual T4A eligibility job.

Imported rides remain fully visible in ride history — this governs money math only.

## 4. Risk & impact on existing functionality

- **Blast radius**: backend read paths only. No writes, no migration, no ride-state, dispatch, wallet-delta or Stripe interaction.
- **`payable_balance` / payout gating**: unchanged by construction (see above). The `_require_sin_for_payout` and insufficient-funds checks read the same balance, so payout eligibility does not move.
- **`driver_daily_stats`**: verified the booking importer never writes it, so the pre-aggregated weekly/monthly paths were already legacy-free; only their rides-table fallbacks needed the filter.
- **Cancelled-ride fees**: untouched — the importer only imports `completed` bookings, so cancellation-fee queries can't contain legacy rows.
- **T4A**: totals **decrease** for any driver with imported rides. This is the correction of a CRA over-reporting bug, but if a slip has already been filed on the old numbers, the filed figure and a re-download will now differ — see "What was NOT verified".
- **Admin vs driver parity**: the admin Payouts tab was changed in the same commit so the two views cannot disagree about what Spinr owes.
- Consumers checked by grep: every `rides` query in `routes/drivers/earnings.py` (audited programmatically, 8/8 covered), `utils/driver_statement.py` (feeds driver self-serve, the scheduled statement job, and admin download/email), `routes/admin/drivers.py` payouts summary, `routes/drivers/tax_exports.py`, `utils/t4a_annual_job.py`.

## 5. User-experience effect

- **Driver-facing, visible immediately** (no app release needed — these are API responses): "Total earnings" on the payout screen, earnings summaries for every period, and earnings statements now show Spinr money only. For a migrated driver these numbers **drop**.
- **Available balance does not change** — drivers cannot withdraw more or less than before.
- **Internal admin**: the driver Payouts tab moves in step with the driver's own screen.
- Not visible mid-ride; no copy or notification changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/legacy_rides.py` | New — filter constant + drop helpers, with the pair-invariant documented | Single source of truth |
| `backend/routes/drivers/earnings.py` | Exclude legacy rides from all 8 ride queries; drop offset payouts in balance | Driver earnings + balance |
| `backend/utils/driver_statement.py` | Exclude legacy rides and offset payouts from the period window | Fixes the split-period statement bug |
| `backend/routes/admin/drivers.py` | Same exclusion in the payouts summary | Admin/driver parity |
| `backend/routes/drivers/tax_exports.py` | Exclude imported rides from the T4A slip; corrected the false comment | CRA double-count |
| `backend/utils/t4a_annual_job.py` | Same exclusion for the ≥$500 eligibility check | CRA double-count |
| `backend/tests/test_drivers_extended.py` | Rewrote the old-contract test; new test asserts both halves drop together | Contract change |
| `backend/tests/test_p2_payout_t4a.py` | New test: imported rides absent from slip total and trip count | Regression |

## 7. Before / after

```python
# Before — legacy rides counted as income, offset counted as money out
rides = await db_supabase.get_rows(
    "rides", {"driver_id": driver["id"], "status": RideStatus.COMPLETED}, limit=10000)
payout_rows = await db_supabase.get_rows("payouts", {"driver_id": driver["id"]}, limit=5000)
# 'legacy_import' offset rows still deduct — they pair with imported rides
```

```python
# After — both halves dropped together; payable_balance identical
rides = await db_supabase.get_rows(
    "rides",
    {"driver_id": driver["id"], "status": RideStatus.COMPLETED, **EXCLUDE_LEGACY_RIDES},
    limit=10000)
payout_rows = drop_legacy_offset_payouts(
    await db_supabase.get_rows("payouts", {"driver_id": driver["id"]}, limit=5000))
```

## 8. Rollback plan

Read-path only — **no data was written or migrated**, so a `git revert` is a complete rollback here (the usual "revert is not a rollback" caveat applies to applied data changes; there are none). Reverting restores the previous totals immediately on redeploy, with no remediation.

If a partial rollback is wanted, the exclusion is one import and one dict-spread per call site, all greppable via `EXCLUDE_LEGACY_RIDES` / `drop_legacy_offset_payouts`.

## 9. Verification performed

- [x] Automated tests: full backend suite run; targeted sweep `-k "t4a or tax or statement or earning or payout or balance or import"` — 913 passed, 1 skipped
- [x] New regression tests: balance drops both halves together (asserting the server-side filter is actually sent, not assumed); T4A excludes imported rides
- [x] Updated the pre-existing test that encoded the **old** contract (`legacy_import` offsets deduct) rather than leaving a stale assertion green
- [x] Blast-radius grep: programmatic audit of every `rides` query in `earnings.py`; `legacy_import` / `legacy_import_metadata` across backend; `driver_daily_stats` writers; `build_statement` callers
- [x] Reviewed against CLAUDE.md money rules (Decimal-only — no arithmetic changed, only which rows are summed; no error-swallowing added)
- [ ] Manual repro in staging — NOT performed (no staging DB with legacy-imported rows in this environment)
- [ ] Feature flag: not added — read-path correction with an unchanged payable balance; a flag would mean serving two different earnings numbers

## What was NOT verified

- **Not tested against real imported data.** All tests use mocked Supabase. The exact magnitude of the drop for any real driver is unknown from here — worth spot-checking one migrated driver in production after deploy.
- **T4A slips already filed or downloaded on the old numbers will differ from a re-download.** Whether any 2025 slip has been issued on the double-counted figure was not checked, and correcting an already-filed slip is a CRA process decision outside this change.
- The assumption that `stripe_sync` transfers fully cover the legacy income previously supplied by imported rides was **not** verified against real data — if some legacy income was paid outside Stripe, T4A totals for those drivers now under-report rather than over-report. Flagged for the operator.
- No frontend change was needed, so no build/visual verification applies; driver-app and admin-dashboard consume the corrected API responses as-is.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
