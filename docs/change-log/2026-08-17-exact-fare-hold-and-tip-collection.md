# Change Impact & Risk Log — exact-fare hold + tip collection rework

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude Code (session-driven, on `claude/uber-lyft-payment-tips-uyrv75`) |
| Surface(s) | backend, rider-app |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/uber-lyft-payment-tips-uyrv75` (7 commits, `8a93c85`..`834f054` + loop commit) |
| Related issue or gap ID | none filed — originated from a live-testing complaint that a $5 ride showed a $15 pending charge |

## 1. Issue / gap identified

Two problems, one root:

1. **The hold was 3× the fare on small rides.** Booking authorized
   `grand_total + RIDE_AUTH_BUFFER_CAD` (flat **$10**), so a $5 ride put a $15
   pending charge on the rider's bank feed. Riders read that as an overcharge.
2. **Tips arriving after settlement were credited but never charged.**
   `POST /rides/{id}/tip` had no settled-payment guard (unlike `rating.py`,
   which had one). It wrote `tip_amount` and `driver_earnings`, returned
   success, and attempted no charge — so the driver was credited, and T4A
   reported, money nobody paid. Spinr absorbed it silently.

## 2. Root cause

The $10 buffer existed so a post-trip tip could be captured on the *same*
PaymentIntent, saving Stripe's $0.30 fixed fee. Two things made that a bad
trade:

- Migration 248 set `fare_lock_enabled = TRUE`, so settlement keeps the
  booking-time fare. The buffer was therefore ~100% **tip** headroom, not
  fare-variance headroom — it was covering a risk that no longer existed.
- The buffer's *size* never mattered. Combining fare and tip saves exactly one
  fixed fee ($0.30) whether the buffer is $2 or $10. The extra $8 of reserved
  funds bought nothing and cost the rider's trust.

For (2), the gap was simply an unguarded endpoint. `rating.py` had a
payment-status guard; `add_tip` never got one. The rider-app offline replay
queue (`store/rideStore.ts`) made it reachable in ordinary use, since a queued
tip POST can replay long after settlement.

## 3. Fix / remediation

- Hold the **exact fare**. `RIDE_AUTH_BUFFER_CAD` → `0.00`.
- A tip is folded into the existing hold via **incremental authorization**
  (raise the hold, capture once) where the card supports it, and charged
  separately where it does not.
- A tip that arrives **after** capture cannot be folded in at all — Stripe
  cannot increment a captured PaymentIntent — so it is recorded as an
  uncollected receivable in a new `pending_tips` table and collected by a new
  batched background charge. **The driver is credited only when a charge
  succeeds.**
- The **cancellation fee is now a partial capture of the hold** instead of
  cancel-the-hold-then-charge-a-new-PI.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface (backend payments + rider-app).** This is the
money path; it is not isolated.

Consumers checked by grep before changing:

| Thing changed | Other readers/writers found | Assessment |
|---|---|---|
| `RIDE_AUTH_BUFFER_CAD` | `routes/rides/booking.py` (only real consumer); referenced in comments by `utils/orphaned_hold_reconciler.py`, `utils/card_hold_release.py`, `docs/change-log/2026-07-30-*` | Single call site. Reconciler/release read `payment_intent_id`/`auth_status`, not the buffer. |
| `authorize_ride` | `routes/rides/booking.py`, `utils/scheduled_rides.py` (via `_preauthorize_ride_card`) | Both go through the same helper. Scheduled rides authorize at dispatch, unchanged. |
| `ChargeOutcome` | every Stripe helper caller | New field is additive with a default; no positional construction found. |
| `capture_ride` | `services/payment_service.py`, `utils/preauth_capture.py` | Behaviour unchanged; only its docstring's stale "buffer" wording. |
| Hold release on cancel | `routes/rides/cancellation.py` (rider), `routes/drivers/ride_cancel.py` (driver), `routes/rides/matching.py` (auto-cancel) | **Only the rider path changed.** Driver-cancel and no-driver auto-cancel still release in full, which is correct — a rider is never charged for those. |
| `pending_tips` | new table, no existing readers | Additive. |
| Background loops | now 19, was 18 | New loop follows the replay-safety contract (leader lock + atomic DB claim + Stripe idempotency key). No loop-count assertion exists in the suite. |

Regression risks accepted and mitigated:

- **A zero buffer under-covers settlement if `fare_lock_enabled` is turned
  off.** Mitigated: the buffer is resolved at runtime, and falls back to a
  proportional buffer (25% of fare, floored $2, capped $10) when the lock is
  off. A settings-lookup failure assumes *unlocked* — over-holding is
  recoverable at capture, under-holding fails settlement.
- **`insufficient_funds` no longer retries at a lower amount** when the buffer
  is zero, because there is no lower amount. Booking now declines instead of
  silently retrying an identical authorization.
- **A cancellation fee larger than the hold is capped, not chased.** A $5 fee
  on a $4 fare collects $4. Deliberate: a cancel fee exceeding the ride is not
  defensible, and the shortfall is logged.
- **A tip recorded but not yet collected leaves the driver uncredited until the
  batch runs** (up to 7 days). This is a deliberate trade against the previous
  behaviour of crediting money we never took.

## 5. User-experience effect

**Rider (visible, and the point of the change):**
- A $5 ride now shows a **$5** hold, not $15.
- `payment-confirm.tsx` copy changed: it no longer claims "estimated fare +
  $10". It had a hardcoded `totalFare + 10`, which after this change would have
  displayed a number the server does not use.
- Tip presets are now 15/18/20% of fare (whole dollars) instead of flat
  $2/$5/$10 — a $10 preset on a $5 ride was part of the same problem.
- A rider may now tip after settlement. Previously `/rate` returned a 400.
- Post-capture tips appear as a **second** charge on the statement.

**Driver:** a tip collected via the batch path appears when collected, not when
the rider taps. Not yet surfaced as "pending" in the driver app — see
*Not verified* below.

**Mid-session impact:** a rider mid-ride at deploy time already has a hold
placed under the old rules; nothing re-reads the buffer mid-ride, so their
settlement is unaffected. Rides booked after deploy get the new hold.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/config.py` | `RIDE_AUTH_BUFFER_CAD` 10.00 → 0.00 | Hold the quoted fare, not more |
| `backend/routes/rides/booking.py` | `_resolve_auth_buffer`; exact-fare hold; retry gated on `buffer > 0`; persist `auth_incrementable` | Zero buffer, safely |
| `backend/utils/stripe_charge.py` | `+increment_authorization`, `+capture_cancellation_fee`, `_reads_incremental_support`; `ChargeOutcome.incremental_authorization_supported` | Fold tips into the hold; take fees from it |
| `backend/services/payment_service.py` | Increment-then-capture before the overflow branch | One fee where the card allows |
| `backend/routes/rides/cancellation.py` | Fee hoisted above hold handling; partial capture | Fee cannot decline |
| `backend/routes/rides/payments.py` | `add_tip` routes settled tips to `pending_tips` | Fixes the uncharged-tip leak |
| `backend/routes/rides/rating.py` | 400 replaced with the deferral path | Collect the tip rather than refuse it |
| `backend/services/pending_tip_service.py` | **new** — record a tip as owed, never credit | Shared by both tip entry points |
| `backend/utils/tip_batch_charge.py` | **new** — batched collection loop | Collect owed tips; credit on success |
| `backend/utils/preauth_capture.py` | Window 20 → 5 min; select `auth_incrementable` | Window no longer buys anything |
| `backend/core/lifespan.py` | Register the new loop | — |
| `backend/migrations/317_tip_collection.sql` | **new** — `rides.auth_incrementable`, `pending_tips` + RLS | Schema for the above |
| `rider-app/app/payment-confirm.tsx` | Removed hardcoded `+ 10` | Screen was announcing a hold we no longer place |
| `rider-app/app/ride-completed.tsx` | Fare-scaled tip presets | $10 preset on a $5 ride |

## 7. Before / after

```python
# Before — booking.py: flat buffer, $5 ride holds $15
buffer = _round(_d(_deps._settings.RIDE_AUTH_BUFFER_CAD))   # 10.00
hold_amount = _round(_d(grand_total) + buffer)
```

```python
# After — fare-lock aware; normally zero, so hold == quoted fare
buffer = await _resolve_auth_buffer(_round(_d(grand_total)))  # 0.00 when locked
hold_amount = _round(_d(grand_total) + buffer)
```

```python
# Before — cancellation.py: release the hold, then charge a NEW PI (can decline)
_released = await _deps.cancel_authorization(ride_id=..., payment_intent_id=_booking_pi)
...
outcome = await _deps.charge_ancillary_fee(amount=total_cancel_fee, ...)
```

```python
# After — take the fee from the hold; funds are already reserved so it cannot
# be declined for insufficient funds. Remainder auto-released by Stripe.
_fee_outcome = await _deps.capture_cancellation_fee(
    ride_id=ride_id, payment_intent_id=_booking_pi,
    fee=total_cancel_fee, authorized_amount=_held_amount,
)
```

```python
# Before — payments.py add_tip: credited the driver, charged nobody
update_payload = {"tip_amount": _f(new_tip), "driver_earnings": _f(new_driver_earnings)}
await _deps.db_supabase.update_ride(ride_id, update_payload)
return {"success": True, ...}          # no charge attempted, ever
```

```python
# After — record the debt; credit only when the batch charge succeeds
if payment_is_settled(ride):
    await record_pending_tip(ride=ride, rider_id=current_user["id"], amount=tip_amount)
    return {"success": True, "tip_amount": _money_str(tip_amount), "collection": "pending"}
```

## 8. Rollback plan

Ordered least- to most-invasive, none requiring a redeploy except the last:

1. **Hold size** — set `RIDE_AUTH_BUFFER_CAD=10.00` in the backend environment.
   Restores the old hold immediately; the code path is unchanged.
2. **Increment path** — self-disabling. If `auth_incrementable` is false
   everywhere (e.g. set the column default to `false` and clear it), every ride
   takes the two-charge fallback, which is the pre-existing overflow path.
3. **Tip batching** — the loop is registered inside its own `try/except` in
   `lifespan.py`. To drain rather than revert: leave it running until
   `pending_tips` has no `owed`/`failed` rows, then remove the registration.
   **Do not force-revert with rows outstanding** — those are debts owed to
   drivers. Export first:
   `SELECT * FROM public.pending_tips WHERE status IN ('owed','charging','failed');`
4. **Migration** — rollback SQL is in the header of
   `backend/migrations/317_tip_collection.sql`.

**`git revert` is not sufficient** for anything already applied to live data:
captured cancellation fees, incremented authorizations, and collected tip
batches are real Stripe movements. Reverting the code does not reverse them;
they need refunds.

## 9. Verification performed

- [x] **Automated tests** — full backend suite run (see result below). New:
      `test_cancel_fee_from_hold.py` (7), `test_pending_tip.py` (16),
      `test_tip_batch_charge.py` (10). Updated:
      `test_ride_preauth_booking.py`, `test_settle_card_capture.py`,
      `test_preauth_capture.py`, `test_rate_tip_abuse.py`.
- [x] **Real production build** of rider-app — `npm run build:web`, exit 0.
      Also `tsc --noEmit`, clean. (CLAUDE.md requires the real build; the
      typecheck alone would not have counted.)
- [x] **Blast-radius grep** — searched for consumers of
      `RIDE_AUTH_BUFFER_CAD`, `authorize_ride`, `capture_ride`,
      `cancel_authorization`, `ChargeOutcome`, `charge_ancillary_fee`,
      `tip_amount`, `driver_earnings`, and every `_spawn(` loop registration.
      Results in §4.
- [x] **Conventions reviewed** — Decimal-only money (pre-commit money check
      passed on every commit), Stripe idempotency keys namespaced per
      operation, RLS on the new table, replay-safety contract for the new loop,
      `logger.error` (not warning) on payment-path failures.
- [ ] **Manual staging repro** — NOT done; see below.
- [ ] **Feature flag** — NOT added; see below.

## 10. What was NOT verified

State this plainly rather than letting the checklist imply full coverage.

- **No Stripe test-mode run.** Everything Stripe-facing is verified against
  mocks only. `increment_authorization` and `capture_cancellation_fee` have
  **never executed against real Stripe**. Before enabling on live traffic, book
  a test-mode ride and confirm: a $5 ride holds $5; cancelling past the
  threshold captures the fee and releases the remainder; a tip increments the
  hold.
- **Canadian eligibility for incremental authorization is unconfirmed.** Stripe
  documents Visa/Mastercard support for all card types and user categories
  globally, and Visa's rules name MCC 4121 (taxicabs/limousines) for card-absent
  incremental authorizations — but `docs.stripe.com` was unreachable from the
  build environment, so the per-brand availability table was not read directly.
  Stripe's docs say the authoritative answer is the Dashboard ("find your user
  category"). **If it turns out unavailable, nothing breaks** — every tip takes
  the two-charge fallback at a cost of $0.30 each.
- **Not feature-flagged.** The hold size is env-tunable and the increment path
  self-disables, but the `pending_tips` routing has no kill switch. Adding one
  behind `app_settings` would be the safer shape if this goes to live traffic
  before a staging pass.
- **Driver app not updated.** `pending_tips` rows are invisible to drivers, so
  a tip collected days later appears with no prior "pending" indication. Left
  out deliberately to keep this change reviewable; it is a real UX gap.
- **No visual-regression tooling exists for rider-app**, so the copy and
  tip-preset changes were reasoned about and type/build-checked, **not
  screenshotted**. Standing repo gap, not specific to this change.
- **The no-show fee path (`routes/drivers/ride_cancel.py`) is wallet-only** — a
  card-paying rider is never charged a no-show fee today. That is a
  **pre-existing** gap, unrelated to this change and deliberately not fixed
  here, but it is the same shape as the cancellation fee and could take the
  same partial-capture treatment.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
