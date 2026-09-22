# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | Claude Code (session_01L8WBp4c4HHd7Pq8iysPZuQ) |
| Surface(s) | backend, rider-app |
| Domain (Sentry tag) | payments |
| PR / commit link | srikumarimuddana-lab/spinrvm#5690 |
| Related issue or gap ID | Found during a 2026-09-22 proactive 9-agent audit (spinr-fraud-auditor, spinr-edge-case-reviewer) across charges/insurance/dispatch/compliance — not filed as a separate issue beforehand |

## 1. Issue / gap identified

Two independent gaps: (a) `fare_service.py`'s completion-time fare recalculation could settle a promo-discounted ride at a negative `grand_total`; (b) the rider-app's booking `Idempotency-Key` was regenerated on every call instead of once per booking attempt, risking a duplicate Stripe hold on a client retry after a timeout.

## 2. Root cause

**(a)** `recalculate_fare_for_distance` computes `new_grand_total = new_total_fare + area_fees_total + tax_amount - discount` with no floor. `discount_amount` is capped against the **booking-time** ride portion (`routes/rides/booking.py`), not the **completion-time** one recomputed here. When the actual trip distance comes in much shorter than planned (shortcut, a stop skipped), `new_total_fare` can shrink well below the already-promised discount, and nothing clamps the result before it reaches settlement (`routes/rides/payments.py` has no floor guard either). The sibling function `_reestimate_fare_for_stops` (`routes/rides/_shared.py`) fixed the identical bug class for mid-trip stop edits on 2026-09-05 (finding N3) — its own code comment incorrectly asserted this function "has no clamp because its fare only ever grows relative to the booked one," which `ride_complete.py`'s trigger condition (`abs(actual - planned) > 0.1`, either direction) directly contradicts.

**(b)** `idempotencyKey = \`ride-${userId}-${Date.now()}\`` was generated fresh on every `createRide` call. A manual retry after a genuine network timeout (not a 4xx/5xx, so the axios auto-retry interceptor doesn't apply) mints a new key even if the original request is still in flight server-side — if the original request's card-hold authorization lands after the retry's, the retry's hold has no ride row to attach to.

## 3. Fix / remediation

**(a)** `new_grand_total = max(Decimal("0"), _round(...))` — floors the completion-time recalculation at zero, mirroring the already-shipped `_reestimate_fare_for_stops` clamp.

**(b)** The idempotency key now derives from request content (pickup/dropoff coordinates) plus a 2-minute time bucket (`Math.floor(Date.now() / 120_000)`) instead of raw `Date.now()` — a retry within that window reuses the same key; a genuinely new booking a few minutes later gets its own. Mirrors the bucketed-key pattern already used server-side in `utils/corporate_autotopup.py`.

## 4. Risk & impact on existing functionality

- **(a) Blast radius: isolated.** `recalculate_fare_for_distance` has exactly one caller in production code (`backend/routes/drivers/ride_complete.py`, the trip-completion handler). No other function reads or writes `new_grand_total`.
- **(b) Blast radius: isolated.** Only affects the `Idempotency-Key` header value on `createRide`'s `POST /rides` call; no other code reads this key.
- Neither change touches the ride state machine, background loops, or wallet/corporate settlement logic.
- Could this regress a working flow? Only if a legitimate business case wanted a negative settlement to *credit* the rider mid-completion — no such path exists; an intentional refund goes through the dedicated refund/dispute flow, untouched by this change. For (b), the only behavior change is which requests share an idempotency key; a genuinely distinct booking attempt (different pickup/dropoff, or more than 2 minutes apart) is unaffected.

## 5. User-experience effect

- **(a)** Rider-facing, but only as the absence of a bad outcome: a rider can no longer end up with (or be credited) a negative fare total on trip completion. Not visible mid-session in the sense of any UI change.
- **(b)** No visible UI change. A rider who retries a booking after a timeout is less likely to have two card holds placed instead of one.
- No copy/notification change in either fix.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/fare_service.py` | Added `max(Decimal("0"), ...)` floor to `new_grand_total` in `recalculate_fare_for_distance` | Prevent a negative settlement total on a promo-discounted ride with a shorter-than-planned actual distance |
| `backend/tests/services/test_fare_service.py` | Added `test_recalc_promo_discount_never_takes_grand_total_negative` | Regression coverage for the exact scenario above |
| `rider-app/store/rideStore.ts` | `Idempotency-Key` now derived from pickup/dropoff + a 2-minute time bucket instead of `Date.now()` per call | Prevent a client retry after a timeout from minting a second Stripe hold for the same booking attempt |

## 7. Before / after

```python
# Before
new_grand_total = _round(new_total_fare + area_fees_total + tax_amount - discount)

# After
new_grand_total = max(Decimal("0"), _round(new_total_fare + area_fees_total + tax_amount - discount))
```

```ts
// Before
const idempotencyKey = `ride-${userId}-${Date.now()}`;

// After
const idempotencyBucket = Math.floor(Date.now() / 120_000);
const idempotencyKey =
  `ride-${userId}-${pickup.lat.toFixed(5)}-${pickup.lng.toFixed(5)}-` +
  `${dropoff.lat.toFixed(5)}-${dropoff.lng.toFixed(5)}-${idempotencyBucket}`;
```

## 8. Rollback plan

Pure code changes, no migration, no feature flag, no data mutation in either fix. A plain `git revert` of either commit is a complete rollback — neither fix has touched live data (no Stripe charges, wallet deltas, or ride rows written by this diff itself).

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/services/test_fare_service.py` — 45/45 pass (44 pre-existing + 1 new regression test for the negative-total scenario). `ruff check` / `ruff format --check` clean on both changed backend files.
- [ ] Manual repro steps followed in staging — not done for either fix.
- [x] Blast-radius grep performed: confirmed `recalculate_fare_for_distance`'s only caller is `ride_complete.py`; confirmed no other code reads the `createRide` idempotency key.
- [x] Reviewed against relevant CLAUDE.md conventions: Decimal-only money math (unchanged, clamp uses the same `Decimal`/`_round` helpers already in scope), no ride-state-machine or background-loop interaction.
- [ ] Feature-flagged — not flagged. Both are pure correctness clamps with a trivially safe rollback (revert); a flag was judged unnecessary for either.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (single-commit revert per fix)
- [x] Blast radius is stated, not assumed (one caller each, confirmed by grep)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (Section 5 states the — in both cases minimal — visible effect explicitly)
