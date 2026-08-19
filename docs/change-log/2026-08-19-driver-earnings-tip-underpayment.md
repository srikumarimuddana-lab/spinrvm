# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | Live-testing incident, traced from real Stripe receipts for rides `f3fe8061-...` (SPR-PE7TTB) and `4a8a0767-...` (SPR-8X2GTY) |

## 1. Issue / gap identified

A real, live-charged ride (SPR-8X2GTY) settled with `driver_earnings = 0.17`
even though the rider was correctly charged $0.68 (fare + tax + $0.50 tip)
via Stripe. The driver was underpaid by exactly the $0.50 tip. A second
ride (SPR-PE7TTB) on the same day settled correctly. The driver Activity
panel's "Fare $0.00" / mismatched "Total Earned" symptoms the user reported
trace entirely to this one bad `driver_earnings` value — not a UI bug.

## 2. Root cause

Three independent code paths can write `rides.tip_amount` /
`rides.driver_earnings` for the same ride, depending on when the rider
tips: `routes/rides/rating.py` (tip while rating, pre-payment),
`routes/rides/payments.py`'s `add_tip` (late tip, post-payment), and the
`settle_ride_card_payment` Postgres RPC / `payment_service.py`'s
`_tip_ride_update` fallback (tip collected at settlement time). All three
used **delta** math — `existing driver_earnings + tip` (or the RPC's
`COALESCE(driver_earnings,0) + (p_tip_amount - COALESCE(tip_amount,0))`).
That's only correct if `tip_amount`/`driver_earnings` always move together
through the *same* code path. When a ride touches more than one path (or a
path runs against a stale in-memory/DB read), the delta is computed against
the wrong baseline and the tip credit is silently dropped. Confirmed via
`financial_events`: exactly one `stripe_charge` row per ride with the
correct `delta_cents` in both cases — the Stripe charge was always right,
only the earnings bookkeeping diverged.

## 3. Fix / remediation

Added one canonical, idempotent function,
`fare_service.driver_earnings_with_tip(ride, tip_amount)`, computing
`driver_earnings` fresh every time from the ride's own persisted columns:
`max(total_fare - (booking_fee + airport_fee), 0) + tip_amount`. No delta,
no dependency on call order or which path ran last. Replaced all three
write sites (`rating.py`, `payments.py`'s `add_tip` and `process_payment`'s
in-memory receipt mirror, `payment_service.py`'s `_tip_ride_update`) and
the `settle_ride_card_payment` RPC (migration 337) to use this formula.
Migration 335 is a narrow, one-off data correction for the single affected
production ride (SPR-8X2GTY), guarded on its exact stale values so it can
only ever match that one row.

## 4. Risk & impact on existing functionality

- **Blast radius of the fix (writers)**: isolated to the 3 write sites +
  1 RPC listed above — no other code writes `driver_earnings`.
- **Blast radius of `driver_earnings` (readers)**: wide — grepped and found
  40+ files reading `driver_earnings` (payout sync, T4A tax exports, driver
  statements, admin analytics/compliance/rides dashboards, AI driver tools,
  ledger projection, quest tracking, auto-payout, promotions, webhooks,
  offer cards). None of these were changed — they all just read whatever
  value ends up in the column, and the fix makes that value *more*
  correct, never structurally different in shape (still a Decimal-rounded
  currency amount on the same column). No regression expected to any
  reader; a driver whose earnings were previously silently short will now
  see the corrected total.
- Interaction with background loops: none of the 18 startup loops write
  `driver_earnings` directly (payout/statement loops only read it).
- Ride state machine: untouched — no status transition logic changed.
- Money/wallet deltas: this changes `rides.driver_earnings` only, not any
  Stripe charge amount, not `corporate_wallet_apply_delta`, not
  `financial_events` (append-only, untouched).

## 5. User-experience effect

- **Driver-facing**: a driver whose ride previously under-credited a tip
  will now see the correct (higher) `driver_earnings` reflected in the
  Activity panel and earnings statements once this ships and the one-off
  correction (migration 338) is applied. For all other rides, no visible
  change — the new formula reproduces the same output as the old delta
  math whenever a ride only ever went through one tip-writing path (the
  overwhelming majority of rides).
- **Rider-facing**: none. Riders were never overcharged; nothing on the
  rider side reads `driver_earnings`.
- Not visible mid-session — `driver_earnings` is only read after a ride
  completes and settles.
- No copy/notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/fare_service.py` | Added `driver_earnings_with_tip()` | Canonical idempotent formula |
| `backend/routes/rides/rating.py` | Tip-while-rating path now calls the canonical helper instead of accumulating | Root-cause fix, path 1 |
| `backend/routes/rides/payments.py` | `add_tip` and `process_payment`'s in-memory receipt mirror now call the canonical helper | Root-cause fix, path 2 |
| `backend/services/payment_service.py` | `_tip_ride_update()` now calls the canonical helper | Root-cause fix, path 3 (legacy fallback) |
| `backend/migrations/337_settle_ride_card_payment_idempotent_earnings.sql` | `settle_ride_card_payment` RPC recomputes `driver_earnings` fresh instead of via delta | Root-cause fix, path 4 (RPC/primary settlement) |
| `backend/migrations/338_fix_ride2_underpaid_tip_2026_08_19.sql` | One-off, narrowly-guarded `UPDATE` correcting SPR-8X2GTY's `driver_earnings` from 0.17 to 0.67 | Data correction for the one real underpaid ride |
| `backend/tests/test_e2e_rating_regression.py` | Updated `test_tip_added_to_driver_earnings` fixture to set `total_fare` so the new fresh-computation formula still produces the test's intended $16 base | Test now encodes new (correct) behavior instead of old delta behavior |

## 7. Before / after

```python
# Before (routes/rides/payments.py add_tip, and equivalent pattern in rating.py/_tip_ride_update)
existing_earnings = _d(ride.get("driver_earnings") or 0)
new_driver_earnings = _round(existing_earnings + tip_amount)
```

```python
# After
new_driver_earnings = driver_earnings_with_tip(ride, new_tip)
# = max(total_fare - (booking_fee + airport_fee), 0) + tip, computed fresh
# from the ride row every time — never a delta against a possibly-stale
# driver_earnings/tip_amount pair.
```

```sql
-- Before (migration 288's settle_ride_card_payment)
v_earnings := COALESCE(v_driver_earnings, 0) + (p_tip_amount - COALESCE(v_tip_amount, 0));

-- After (migration 337)
v_base_earnings := GREATEST(v_total_fare - (v_booking_fee + v_airport_fee), 0);
v_earnings := v_base_earnings + COALESCE(p_tip_amount, 0);
```

## 8. Rollback plan

- Code paths (rating.py / payments.py / payment_service.py): straightforward
  `git revert` is safe here — these are pure computation-path changes with
  no data migration attached, so a code revert alone reverts behavior.
- Migration 334 (RPC): `app_settings.ledger_atomic_settle_enabled = false`
  routes callers to the legacy Python fallback (`_tip_ride_update`, fixed
  in the same change) without a second deploy. To fully remove the new RPC
  version: `DROP FUNCTION IF EXISTS settle_ride_card_payment(text, uuid, text, bigint, text, numeric, jsonb, text);` then re-apply migration 288's body (not recommended — reintroduces the bug).
- Migration 335 (one-off data fix): re-run the same `UPDATE` with
  `driver_earnings = 0.17` in place of `0.67` to revert the single
  corrected row back to its prior (wrong) value. This is a data-level
  remediation, not a code revert — `git revert` alone does not undo an
  already-applied `UPDATE`.

## 9. Verification performed

- [x] Automated tests run — full targeted suite: `pytest backend/tests/ -k "rating or _tip_ride_update or add_tip or fare_service or tip_ride or settle_ride_card or driver_earnings or process_payment"` → 153 passed, 1 skipped.
- [x] `ruff check` on all touched Python files → all checks passed.
- [ ] Manual repro steps followed in staging — **not verified**, no staging DB access in this session; verified only against real production `financial_events`/`rides` data pasted by the user from Supabase SQL Editor.
- [x] Blast-radius grep performed — see §4 (writers vs. 40+ readers of `driver_earnings`).
- [x] Reviewed against relevant `CLAUDE.md` conventions — money arithmetic (Decimal-only, `_d`/`_round`/`_f`), migration numbering/append-only, Stripe idempotency (unaffected — `financial_events` untouched).
- [ ] Feature-flagged — not flagged; this is a bugfix to money-correctness math with no new user-visible behavior to gate, and the delta-vs-fresh computation is not something a flag can safely dual-run (there's no meaningful "old" behavior to preserve, it was silently wrong). Migration 334's RPC already sits behind the pre-existing `ledger_atomic_settle_enabled` flag as its rollback path.
- Migration 334 has **not been applied to any database** (no live DB access in this session) — it is a code change pending the normal migration pipeline. Migration 335 likewise unapplied.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
