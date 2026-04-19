# Dimension 08 — Payments & Earnings

**Question:** Is money handled safely? Can a rider be charged twice? Can a driver lose a payout?

---

## Checklist

### PCI-DSS Compliance
- [ ] No raw card data (card number, CVV, expiry) ever reaches the backend
- [ ] Stripe tokenisation handles all card data — only `payment_method_id` stored
- [ ] PCI field guard rejects requests containing raw card field names:
  - snake_case variants: `card_number`, `cvv`, `cvv2`, `cvc`, `security_code`
  - camelCase variants: `cardNumber`, `pan`, `primaryAccountNumber`
- [ ] SetupIntent customer ID derived server-side — not client-supplied

### PaymentIntent Security
- [ ] Idempotency key on every PaymentIntent creation (prevents double-charge on retry)
  ```python
  idempotency_key=f"ride-{ride_id}-{driver_id}"
  ```
- [ ] Payment amount validated against ride fare record — not blindly trusting client amount
- [ ] Amount type is Decimal — not float (float causes rounding errors)
- [ ] Minimum/maximum amount enforced

### Webhook Security
- [ ] Stripe webhook signature verified with `construct_event()` — not just trusting POST body
- [ ] HMAC-SHA256 secret from environment variable (not hardcoded)
- [ ] Idempotency: event processed at most once (dedup table with `stripe_event_id`)
- [ ] Event type allowlist — unexpected events logged and ignored (not processed)
- [ ] Webhook endpoint returns 200 quickly — heavy processing done async

### Payout Flow
- [ ] Minimum payout amount enforced at backend schema level (not just UI)
- [ ] Transfer failure handled — payout record status updated to `failed` (not left as `pending`)
- [ ] Driver notified on payment retry exhaustion
- [ ] Payout history paginated — not loading all records
- [ ] Tax documents (T4A) generated and accessible to drivers

### Testing
- [ ] Test uses Stripe test mode keys — not live keys
- [ ] PCI guard test covers all field name variants (snake_case + camelCase)
- [ ] Idempotency test: two identical requests produce one payment
- [ ] Webhook replay test: same event sent twice is processed once

---

## Severity Guide

| Finding | Severity |
|---|---|
| Raw card data accepted by backend | CRITICAL |
| No idempotency key — double-charge on network retry | HIGH |
| Webhook signature not verified — attacker can fake events | CRITICAL |
| Payment amount not validated against ride fare | HIGH |
| Monetary field is float type | MEDIUM |
| Transfer failure leaves payout as "pending" forever | MEDIUM |
| Minimum payout not enforced at backend | MEDIUM |
| PCI guard misses camelCase variants | HIGH |
| Tax documents not accessible to drivers | LOW |
