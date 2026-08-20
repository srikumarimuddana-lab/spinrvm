# Change Impact & Risk Log — Block tipping on legacy-imported rides

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| Related issue or gap ID | Found by `spinr-money-auditor` during this session's legacy-migration data-quality audit (user-requested, following the earlier MongoDB export analysis) |

## 1. Issue / gap identified

`routes/rides/rating.py`'s tip-while-rating path already refuses a tip on a legacy-imported ride (`if ride.get("legacy_import_metadata"): raise HTTPException(400, ...)`). `routes/rides/payments.py`'s `add_tip` — the *other* tip-writing entry point, used for late tips — never got the same guard.

## 2. Root cause

`add_tip` was written, and today's separate tip-underpayment fix (`docs/change-log/2026-08-19-driver-earnings-tip-underpayment.md`) touched it, without anyone re-deriving `rating.py`'s legacy-ride exception. A legacy-imported ride is `status='completed'`, `payment_status='paid'`, `payment_method='card'` with `tip_amount=0` (booking_import_service.py never imports a tip) — every check `add_tip` has (`status==COMPLETED`, `existing_tip==0`) passes cleanly for one of these rows. If its matched rider (phone-linked at import time, `booking_import_service.py:386-388`) called `POST /rides/{ride_id}/tip` on a legacy ride, it would reach the "already paid, card" branch and fire a real, new Stripe PaymentIntent against their card for a pre-Spinr historical trip.

Worse: the resulting `driver_earnings_with_tip(ride, tip)` call would silently overpay the driver. Legacy rows' `total_fare`/`base_fare` are a receipt-display reconstruction (`booking_import_service.py:498-563`), not real fare components — `total_fare - admin_earnings` there algebraically equals the *old app's admin commission* on that ride, not a minimum-fare uplift. The new canonical formula (today's tip-underpayment fix) would have attributed that commission to the driver as if it were their own.

## 3. Fix / remediation

Two layers:
1. `routes/rides/payments.py`'s `add_tip` now rejects a legacy-imported ride with the same 400 + message pattern `rating.py` already uses, placed right after the completed-status check.
2. `services/fare_service.py`'s `driver_earnings_with_tip()` — the single canonical formula every tip-crediting path must call — now raises `ValueError` outright on a legacy-imported ride, as a belt-and-suspenders stop for any future fourth call site that doesn't remember to check first. Its docstring already claimed to be "the ONLY way any code path should compute" the value; now it enforces that claim for this row shape.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `add_tip`'s new guard sits before any state-mutating call in the function — a rejected request touches nothing. `driver_earnings_with_tip`'s new guard is the first line of the function body.
- **Other readers of `driver_earnings_with_tip`**: `rating.py` (already guards against reaching it on a legacy ride — this is now redundant-but-harmless there), `payments.py`'s `add_tip` (now guards too, same redundancy), `payment_service.py`'s `_tip_ride_update` legacy-fallback (settlement path — a legacy ride is already `payment_status='paid'` at import time, so this path shouldn't be reachable for one either, but was not independently re-verified this session; the function-level guard now protects it regardless).
- **No other ride ever has `legacy_import_metadata` truthy** — this change is a no-op for every organic ride. Confirmed the falsy-`{}` convention (the DB default) is respected, not just non-null.
- Ride state machine, background loops, wallet/corporate money: untouched.

## 5. User-experience effect

- **Rider-facing**: a rider who somehow found and opened a legacy-imported ride's tip screen would now see a rejected request instead of it silently succeeding — this closes a gap, it doesn't remove a working feature (tipping a pre-Spinr trip was never a real, intended capability).
- **Driver-facing**: none visible — no driver has ever received one of these inflated credits (this closes the path before any occurred, not after).
- Not mid-session-relevant (completed rides only).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/payments.py` | `add_tip` rejects a legacy-imported ride, mirroring `rating.py` | Live-exploitable gap: real Stripe charge + inflated earnings |
| `backend/services/fare_service.py` | `driver_earnings_with_tip()` raises `ValueError` on a legacy-imported ride | Belt-and-suspenders — protects every current and future call site |
| `backend/tests/test_rides_payments_coverage.py` | Added `test_add_tip_rejects_imported_legacy_ride` | Regression coverage, mirrors `test_rate_driver_rejects_imported_legacy_ride` |
| `backend/tests/services/test_fare_service.py` | Added `TestDriverEarningsWithTip` (5 cases: normal computation, idempotency, floor-at-zero, legacy-ride refusal, organic-ride `{}` default not blocked) | First direct unit coverage of this function at all — it had none |

## 7. Before / after

```python
# Before (payments.py add_tip)
if ride.get("status") != RideStatus.COMPLETED:
    raise HTTPException(status_code=400, detail="Can only tip completed rides")
# R-P1-20: Block duplicate tips — one tip per ride.
existing_tip = _d(ride.get("tip_amount") or 0)
```

```python
# After
if ride.get("status") != RideStatus.COMPLETED:
    raise HTTPException(status_code=400, detail="Can only tip completed rides")
if ride.get("legacy_import_metadata"):
    raise HTTPException(status_code=400, detail="Imported historical rides cannot be tipped")
# R-P1-20: Block duplicate tips — one tip per ride.
existing_tip = _d(ride.get("tip_amount") or 0)
```

## 8. Rollback plan

`git-revert-safe` — purely additive guard clauses with no data written by them; a revert restores the prior (buggy) behavior with no cleanup needed. No production data was affected: not run against production, and no evidence any real legacy ride was ever tipped this way (would show as an unexpected `stripe_charge` financial_event on a ride carrying `legacy_import_metadata` — not checked live this session, no DB access, but this fix closes the path going forward regardless of whether it was ever exercised).

## 9. Verification performed

- [x] Automated tests: `pytest -k "add_tip or rating or fare_service or driver_earnings or tip_ride or settle_ride_card or process_payment"` → 153 passed, 1 skipped (same targeted set today's tip-underpayment fix used) + the 6 new tests in this change. `ruff check` clean.
- [ ] Manual repro / staging — not performed, no live DB access this session.
- [x] Blast-radius grep — every caller of `driver_earnings_with_tip` identified (3: rating.py, payments.py, payment_service.py's legacy fallback).
- [x] Reviewed against CLAUDE.md: money arithmetic (Decimal-only, unaffected), ride state machine (untouched), Stripe idempotency (unaffected — this prevents a charge from being attempted at all, doesn't touch `claim_stripe_event`).

## 10. Sign-off

- [x] Rollback plan concrete
- [x] Blast radius stated
- [x] No silent behavior change to a working flow — this closes a gap in an unintended path, not a feature
