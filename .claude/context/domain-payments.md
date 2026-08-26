# Domain — Payments

_Load when working on: fare calculation, surge, Stripe, wallets, corporate billing, driver payouts, tips, receipts._

> **Architecture diagram:** `docs/architecture/payments-rider-stripe.md` — end-to-end rider
> payment flow (booking hold → settlement → webhooks), the three idempotency layers, the
> `financial_events` ledger schema, the async/background-loop model, and known gaps.

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

## Ledger

- `financial_events` (migration `58`) is the append-only money ledger: signed `delta_cents`
  (positive = in, negative = out), `ref` = Stripe PaymentIntent ID, 7-year CRA/SK retention.
  ALL writers route through `ledger_service.record_event` (client-supplied PK, 3 retries,
  duplicate-key = success, Sentry `spinr_alert=ledger_write_failed` on exhaustion, never
  raises) — the header is issued *before* the ride update so a recovery record survives a
  stuck `processing` row.
- **Double-entry legs** live in `financial_event_entries` (migration `286`) and have exactly
  ONE writer: the `ledger_projection` background loop (15 min, migration-287 work queue,
  backfills history). Never write legs from a request path. Flag: `ledger_double_entry_enabled`
  (default off; requires migrations 286+287 applied first).
- **Atomic settle** (`ledger_atomic_settle_enabled`, default off, migration `288`): card
  settlement finalizes paid-flip + header in one transaction via `settle_ride_card_payment`,
  with automatic legacy fallback when the function is absent. The RPC never writes legs; the
  projection never reads this flag.
- The daily reconciliation is the drift control: Stripe-vs-DB sum, unbalanced-legs view, and
  a leg-completeness check (headers >24 h without legs while the flag is on).
- Wallet money moves through `wallet_transactions` (migration `19`, carries running
  `balance_after`); corporate through `corporate_wallet_apply_delta`.
- `financial_events` DELETE is legal only inside `purge_pii_retention` Step H via the
  transaction-local GUC `spinr.financial_events.allow_delete` (migration `289`); UPDATE is
  always blocked.

## Stripe

- **Idempotency — three layers**, not just the webhook one:
  1. **Stripe SDK** — every mutating call in `utils/stripe_charge.py` passes an
     `idempotency_key` (`ride-auth-*`, `ride-charge-*`, `ride-capture-*`, `ride-cancelauth-*`,
     `{fee_type}-*`). Note the keys embed `amount_cents`, so a changed amount is a *new* charge.
  2. **Webhook** — every handler calls `claim_stripe_event(event_id)` first. Silently skip if
     already claimed. On handler failure call `unclaim_stripe_event` or Stripe's retry is
     deduped away and the event is lost.
  3. **DB** — conditional `payment_status` claim (`pending|failed → processing`) in
     `routes/rides/payments.py`; zero rows means another request owns the settlement.
- **Async:** the Stripe SDK is synchronous — every call is wrapped in `asyncio.to_thread()`.
  The settlement charge is awaited inline in the handler, not queued.
- **Keys:** live keys are in the `app_settings` Supabase table, not `.env`. Fetch via `get_setting('stripe_secret_key')` — cached for 5 min.
- **3DS flow:** differs by session. *On-session* (booking, add-card, payment sheet) returns
  `200` + `client_secret` so the app can run the challenge. *Off-session* (end-of-ride
  settlement) has no challenge path — `requires_action` returns **402
  `authentication_required`** and the rider must change card.
- **Disputes:** write-through to `disputes` table on `charge.dispute.created`. Support triages.
- **Refunds:** always via Stripe API, never direct DB manipulation of wallet balance.
- **Test mode:** non-production envs must use `sk_test_*`. Pre-commit hook blocks `sk_live_*` in diffs.

## Corporate billing

- **Payment method is chosen ONCE, upfront, per ride** — rider selection or a corporate/Work-mode
  override (`_is_corporate_paid()` in `routes/rides/_shared.py`) — and stored as
  `ride.payment_method ∈ {"card", "wallet", "company_allowance"}` at booking time
  (`routes/rides/booking.py`). There is no re-selection at settlement.
- **Settlement dispatches on that single field with no cross-method fallback**
  (`routes/rides/payments.py`, the `settle_wallet` / `settle_corporate` / `settle_card` branch).
  If `settle_wallet` hits insufficient funds, or a card charge fails, the settlement **fails and
  leaves the ride `payment_status="pending"`**, requiring a new charge attempt on a different
  `payment_method` — it does not automatically fall through to allowance, master wallet, or card.
- **The one real in-transaction fallback is allowance → master wallet**, scoped strictly to rides
  already tagged `payment_method="company_allowance"` (`services/payment_service.py::settle_corporate`):
  the member's allowance is debited first (up to its remaining cap), and any shortfall is charged
  to the company's master wallet. Even this narrower fallback **terminates in a hard failure**
  (503, ride left `payment_status="pending"`) if the master wallet is also exhausted or at its
  floor — it never reaches the rider's personal card.
- All wallet deltas go through `corporate_wallet_apply_delta` Postgres function (`SECURITY DEFINER`, row lock)
- Allowance reset loop (`allowance_reset.py`) runs monthly; uses `auto_approved_this_period` flag for replay safety
- Never bypass allowance caps — the allowance→master fallback above enforces the per-member cap
  and the master wallet's floor rather than over-spending either; it does not extend to card

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
