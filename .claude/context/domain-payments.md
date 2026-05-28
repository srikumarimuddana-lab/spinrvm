# Domain — Payments

_Load when working on: fare calculation, surge, Stripe, wallets, corporate billing, driver payouts, tips, receipts._

## Key files

- `backend/services/fare_service.py` — fare calculation entry point
- `backend/routes/payments.py`, `backend/routes/wallet.py` — payment flows
- `backend/routes/webhooks.py` — Stripe event handler
- `backend/utils/surge_engine.py` — surge tier engine (every 2 min)
- `backend/utils/payment_retry.py` — retry background loop
- `backend/utils/stripe_charge.py` — charge wrapper
- `backend/services/corporate_wallet_service.py` + `corporate_allowance_service.py`

## Money arithmetic — hard rules

- **Decimal only.** Use `_d()`, `_round()`, `_f()` helpers. No float anywhere in fare code.
- Integer cents at the Stripe boundary: `int((Decimal(fare) * 100).quantize(Decimal('1')))`
- GST (5%) and PST (where applicable) are separate Decimal line items on receipts. Never roll into the fare.
- Rounding: `ROUND_HALF_UP` to 2 decimal places for display, `quantize` explicitly — never Python's `round()`.
- Pre-commit hook blocks `float` arithmetic in `fare_service.py` and `routes/payments.py`.

## Fare breakdown

```
# Surge multiplies the distance and time components ONLY — never the base
# fare, booking fee, or airport fee (see services/fare_service.py::calculate_fare).
distance_fare = (per_km  * distance_km)  * surge_multiplier
time_fare     = (per_min * duration_min) * surge_multiplier
subtotal      = base_fare + distance_fare + time_fare + booking_fee + airport_fee
subtotal      = max(subtotal, minimum_fare)        # minimum floor applied AFTER surge
taxed         = subtotal + gst + pst               # corporate-paid rides: surge never applies
total         = taxed + tip
driver_payout = base_fare + distance_fare + time_fare   # 100% to driver; booking/airport are platform
```

- Spinr driver share: **100%** of fare (minus Stripe processing). Driver sees gross, platform takes 0%.
- Booking fee is a fixed rider-side charge (funds platform operations, disclosed as line item).

## Surge rules

See CLAUDE.md for the auto-mode tier table. Additional payment rules:

- **Per-area admin gate:** surge applies (pricing or UI) only when `service_areas.surge_enabled`
  is true for that area — default **off**. The surge engine skips areas that aren't enabled, the
  fare paths price at 1.0× regardless of any parked multiplier, and the public `/service-areas`
  read reports `surge_active=false` / `surge_multiplier=1.0` so no client renders a surge badge.
  Disabling surge in the admin panel clears `surge_active` and resets the multiplier to 1.0.
- Surge multiplies distance + time only — **not** base fare, booking fee, or airport fee
- Surge is applied **before** tax
- Surge never applies to corporate-paid rides (policy)
- Surge never applies retroactively — it's locked at fare estimate time
- `SURGE_CAP = 2.5` is the auto cap; manual admin override up to 10× requires documented justification

## Stripe

- **Idempotency:** every webhook handler calls `claim_stripe_event(event_id)` first. Silently skip if already claimed.
- **Keys:** live keys are in the `app_settings` Supabase table, not `.env`. Fetch via `get_setting('stripe_secret_key')` — cached for 5 min.
- **3DS flow:** captured via PaymentIntent `requires_action` status. Rider must complete before fare settles.
- **Disputes:** write-through to `disputes` table on `charge.dispute.created`. Support triages.
- **Refunds:** always via Stripe API, never direct DB manipulation of wallet balance.
- **Test mode:** non-production envs must use `sk_test_*`. Pre-commit hook blocks `sk_live_*` in diffs.

## Corporate billing

- Payment source priority: rider wallet → corporate allowance → master wallet → rider card
- All wallet deltas go through `corporate_wallet_apply_delta` Postgres function (`SECURITY DEFINER`, row lock)
- Allowance reset loop (`allowance_reset.py`) runs monthly; uses `auto_approved_this_period` flag for replay safety
- Never bypass allowance caps — if a ride exceeds cap, fall through to next payment source, not over-spend

## Receipts

Every rider receipt must show as separate line items:
- Base fare
- Distance fare
- Time fare
- Booking fee
- Surge (if applied)
- GST (5%)
- PST (6% where applicable)
- Tip (if given post-ride)
- Discount / promo (if applied, as negative line)

Never bundle into "service fee" or "other". Transparency is a differentiator.

## Tax reporting

- Rider receipt data retained 7 years (CRA + SK requirement)
- Driver T4A-compatible earnings summary generated annually (when threshold hit)
- Tax line items stored separately in `ride_fare_breakdown` — don't sum into `total`

## Common pitfalls

- Don't use `float` anywhere — even one multiplication off a Decimal returns float
- Don't let tip round the total — tip is separate from taxed fare
- Don't retry a failed Stripe charge without checking the retry loop's idempotency key
- Don't skip the `app_settings` cache TTL — rotating keys fail silently if cached forever
