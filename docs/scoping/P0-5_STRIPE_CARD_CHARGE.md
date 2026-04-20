# P0-5 Scoping — Wire Real Stripe Card Charge at Ride Completion

**Branch:** `claude/p0-5-stripe-card-charge`
**Status:** scoping (no implementation yet)
**Owner:** TBD
**Ship-blocker for:** production launch with card payments

---

## 1. Problem

When a rider finishes a ride paid with a card, the backend **does not
actually charge the card**. The stub at `backend/routes/rides.py:1107,
1149` sets `payment_status="paid"` regardless of payment outcome, so:

- A rider with an invalid / expired / insufficient-funds card rides free
- No dunning / retry flow exists
- No 3DS / SCA challenge flow for EU cards
- Driver earnings are credited against money that was never collected

## 2. Good news — we are NOT starting from scratch

A prior investigation (`docs/scoping/P0-5_STRIPE_CARD_CHARGE.md` → see
commit log of `claude/plan-e2e-testing-SK3bX` for research trail) shows
the following **already exists and works**:

| Capability | File:Line |
|---|---|
| `stripe` Python SDK installed | `backend/requirements.txt:59` |
| `@stripe/stripe-react-native` installed | `rider-app/package.json:35` |
| Stripe secrets load from DB-backed settings | `backend/settings_loader.py:22-38` |
| `stripe.Customer.create()` — rider customer on first payment | `payments.py:40-45` |
| `stripe.SetupIntent.create()` — save card off-session | `payments.py:171-175` |
| `stripe.PaymentMethod.attach/list/detach/modify` — card-on-file CRUD | `payments.py:205-421` |
| `stripe.PaymentIntent.create()` — already used by wallet top-up path | `payments.py:112-116`; `corporate_wallet.py:72-117` |
| Card tokenization on-device (PCI boundary) | `rider-app/app/manage-cards.tsx:28,92-97` |
| "Manage Cards" screen — add/set-default/delete | `rider-app/app/manage-cards.tsx` |
| `stripe.Webhook.construct_event()` with signature verification | `backend/routes/webhooks.py:50` |
| Webhook idempotency via `stripe_events` table | `backend/routes/webhooks.py:67-88` |
| Webhook dispatch for `payment_intent.succeeded` → flips ride to paid | `webhooks.py:94-128` |
| Webhook dispatch for `payment_intent.payment_failed` → flips to failed + notifies | `webhooks.py:138-188` |
| Driver Stripe Connect onboarding | `backend/routes/drivers.py:1282-1309` |
| Refund flow (disputes) | `backend/routes/disputes.py:181-198` |
| `payment_retry.py` with `requires_action` status handling | `backend/routes/payment_retry.py:81, 117, 134` |
| PCI guard tests (reject raw PAN/CVC) | `backend/tests/test_payments_pci_guard.py` |

**Conclusion**: the infrastructure is in place. This is not a greenfield
integration — it's a ~50-line surgical wire-up at one known location,
plus the client-side UX for decline / 3DS.

## 3. Gap — the narrow thing we actually need to build

### 3.1 Backend

**Single stub location**: `backend/routes/rides.py:1107-1149`.

The `process_payment` handler currently:
```python
if payment_method == "wallet":
    # ... real wallet debit ...
    pass
# Card path is still a stub — Stripe charge to be wired separately.
await db_supabase.update_ride(ride_id, {"payment_status": "paid", ...})
```

**Replacement behavior (card branch)**:

```
1. Load rider's stripe_customer_id (already populated)
2. Load default_payment_method (already populated)
3. stripe.PaymentIntent.create(
       amount=total_charge_cents,
       currency="cad",
       customer=customer_id,
       payment_method=default_pm,
       off_session=True,
       confirm=True,
       metadata={"ride_id": ride_id, "rider_id": rider_id},
       idempotency_key=f"ride-charge-{ride_id}",
   )
4. Branch on pi.status:
     "succeeded"         → payment_status="paid", store pi.id in payment_intent_id
     "requires_action"   → payment_status="requires_action",
                           return {client_secret, status} so rider app can confirm
     "requires_payment_method" / exception on create
                         → payment_status="failed",
                           release processing lock, return 402
5. If Stripe raises stripe.error.CardError:
     → payment_status="failed", release lock, return 402 with decline_code
```

The existing webhook at `webhooks.py:138-188` will also catch async failures
(e.g. disputed charges, late fraud rules). Those code paths do not need
to change.

**Additional backend touches (minimal):**

- `drivers.py:1997` — stop hardcoding `payment_status="completed"` in
  `/rides/{id}/complete`. Leave it as `"pending"` so `process_payment`
  is the authoritative writer. (Currently it pretends to be paid
  before the charge even runs.)
- `payment_retry.py:134` — already has `requires_action` handling;
  just plug the new code path into it on retry

### 3.2 Rider-app

Currently `process_payment` is **not called from the rider-app** in the
card path. We need to wire the completion flow:

```
Ride status flips to 'completed' (via WS or poll)
  ↓
RideCompletedScreen: user enters tip, taps "Pay"
  ↓
Call POST /rides/{id}/process-payment { tip_amount }
  ↓
Response variants:
  { success: true, charged_amount: 20.5, already_paid: false }
    → Show success screen
  { status: "requires_action", client_secret: "pi_..._secret_..." }
    → useStripe().confirmPayment(client_secret)
       (renders 3DS sheet; user authenticates; SDK resolves)
    → Retry POST /rides/{id}/process-payment once to confirm status
  HTTP 402 { detail: "card_declined", decline_code: "..." }
    → Show "Card declined — try another card" sheet linking to
      /manage-cards
```

**Files to touch**:
- `rider-app/app/ride-completed.tsx` (or equivalent) — call
  `/process-payment` after tip selection, handle three response variants
- New or existing util for `useStripe().confirmPayment()` wiring

### 3.3 Stripe publishable key delivery to client

Currently the client uses `useStripe()` but I did not find the
publishable key being loaded. Either:
- It's initialized in the Stripe provider at app root (unchecked) — good, no work
- It isn't — need to add a small `/payments/config` endpoint returning
  `{ publishable_key }` and wire `<StripeProvider publishableKey={...}>`
  at the rider-app root

**Action during implementation**: grep `StripeProvider` in rider-app
first. If present, skip. If not, add.

## 4. Out of scope (explicitly NOT this ticket)

- Refund flow (exists in `disputes.py`; unchanged)
- Driver payouts (Stripe Connect flow; unchanged)
- Corporate wallet top-up (already charges via Stripe; unchanged)
- Splitting fare across riders (doesn't involve Stripe differently)
- Apple Pay / Google Pay (P1 — handled via same `confirmPayment()` API)
- Stored-credential variant (off-session MIT/CIT mandates — P2; using
  `off_session=True` + `confirm=True` on a saved PM is the MVP)
- Receipt emails (already sent from `process_payment` end — unchanged)

## 5. Work breakdown & sequencing

Single PR, unless Stripe config needs separate landing first.

### Phase A — backend (half-day)
1. Replace stub in `rides.py:1107-1149` with real PaymentIntent flow
2. Stop premature "paid" write in `drivers.py:1997`
3. Introduce helper `backend/utils/stripe_charge.py::charge_ride(ride, tip)`
   that encapsulates the 5-branch outcome above so `process_payment` and
   `payment_retry.py` share one implementation
4. Map `stripe.error.CardError` to HTTP 402 with structured body
5. Idempotency: `idempotency_key=f"ride-charge-{ride_id}"` prevents
   double-charge on retry (complements existing atomic `processing` lock)

### Phase B — tests (half-day)
1. `backend/tests/test_stripe_charge_success.py` — mock
   `stripe.PaymentIntent.create` returning `status="succeeded"`; assert
   `payment_status="paid"`, `payment_intent_id` persisted
2. `backend/tests/test_stripe_charge_decline.py` — raise
   `stripe.error.CardError`; assert 402, `payment_status="failed"`,
   `processing` lock released for retry
3. `backend/tests/test_stripe_charge_requires_action.py` — return
   `status="requires_action"`; assert response includes `client_secret`,
   ride stays in `requires_action` state
4. `backend/tests/test_stripe_charge_idempotent.py` — two concurrent
   `process_payment` calls with the same ride → one Stripe call, one
   returns `already_paid=True`
5. **Flip the xfail** at `test_p0_ship_blockers.py:686-705` → real pass

### Phase C — rider-app (half-day)
1. Add `/payments/config` endpoint if publishable key wiring missing
2. Wrap app root with `<StripeProvider>` if needed
3. In `ride-completed.tsx`, call `/process-payment` after tip confirm
4. Handle 3 response variants (success, requires_action, 402 decline)
5. On decline: surface "Try another card" CTA linking to `/manage-cards`
6. Jest tests for the three branches

### Phase D — E2E (quarter-day)
1. Update rider-app Playwright spec to mock the 3 payment variants
2. Add one spec asserting decline → retry-card CTA shows

### Phase E — staging validation (external, not code)
1. Point staging at Stripe test mode with real test cards:
   - `4242 4242 4242 4242` — success
   - `4000 0000 0000 0002` — generic decline
   - `4000 0025 0000 3155` — requires 3DS auth
2. Run a real rider → driver → complete → pay → 3DS flow manually
3. Verify webhook fires and idempotency holds (replay the webhook)

**Total estimate**: 2-3 engineer-days, end-to-end. Most risk is in
the client-side 3DS confirmation UX (Expo + Stripe React Native has
known rough edges on web export).

## 6. Design decisions (with rationale)

### 6.1 Use PaymentIntent, not Charge
`Charge` API is legacy; `PaymentIntent` is required for SCA / 3DS and
is what the rest of the codebase already uses (`payments.py:112-116`).

### 6.2 `off_session=True, confirm=True`
Matches the corporate wallet top-up pattern already in `corporate_wallet.py:72-117`.
Charges the rider's saved default card without prompting — the prompt
already happened at ride-request time ("confirm fare estimate"). If
Stripe decides 3DS is needed, the response comes back `requires_action`
and the client runs the 3DS sheet.

### 6.3 Idempotency key = `ride-charge-{ride_id}`
Stripe dedupes by idempotency key for 24h. Combined with the existing
atomic "processing" DB lock, double-charge is prevented even under
network retries + race conditions.

### 6.4 HTTP 402 on decline (not 400 or 500)
402 is the canonical "payment required" status. The rider-app can
switch on status code cleanly without string-matching error details.
Body includes `{ detail, decline_code, suggested_action: "change_card" }`.

### 6.5 Do NOT hardcode `payment_status="completed"` in `/rides/complete`
Today `drivers.py:1997` sets it. This is a lie — the charge hasn't run
yet. The driver's complete action should leave `payment_status` as
`"pending"` (or whatever it was) and let `process_payment` be the
single writer of terminal payment states. Otherwise the webhook
handlers at `webhooks.py:120` would be flipping `paid → paid` no-ops
and genuine failures get hidden.

### 6.6 Webhook remains the async safety net
The current webhook handlers for `payment_intent.succeeded` and
`payment_intent.payment_failed` do not need to change. They exist for
the async path (refunds, disputes, fraud-rule callbacks) and for the
case where our sync `confirm=True` response is lost in transit. Both
paths must be idempotent — and they already are via `stripe_events`
table.

## 7. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| 3DS flow broken on Expo Web | High | Medium | E2E test on web export; document native-only UX if needed |
| Wrong `currency` hardcoded | Low | High | Read from `settings` table same as other calls |
| Duplicate charge on webhook + sync success | Low | High | Idempotency key + `stripe_events` dedupe + DB `processing` lock |
| Stripe key rotation breaks webhook | Medium | Medium | Operational runbook entry; stripe_webhook_secret already in settings |
| Rider stuck with `requires_action` but never completes 3DS | Medium | Medium | Background job: if `requires_action` > 10 min, cancel PI and mark failed |
| Tip added post-charge can't be captured | Medium | Low | MVP: collect tip *before* calling process-payment; P1: capture + update |
| Card on file expired since ride-start | Medium | Medium | Decline → retry UX handles it |
| Rider has no default PM at completion | Medium | High | Check at ride-request time, not at complete; block confirm if no PM |

## 8. Observability

- Stripe's Payments dashboard is the source of truth
- Log every PaymentIntent lifecycle transition with `ride_id`, `rider_id`
- Alert on: decline rate >10% in any 15-min window; `requires_action`
  stuck > 15 min; webhook signature failures
- Add a dashboard query in `admin-dashboard` for "rides with
  payment_status in ['failed', 'requires_action'] > 30 min" — these
  are stuck and need operator attention

## 9. Open questions for the user

1. **3DS UX parity**: do we need card charge to work on the Expo Web
   build, or is native-only acceptable for MVP? (Web 3DS with Stripe
   React Native requires `@stripe/stripe-js` + a different provider
   — ~1 extra day if web is required.)
2. **Currency**: Canada-only CAD, or multi-currency from the start?
3. **Tip-after-charge**: MVP captures tip before Stripe call (simple).
   If we want post-charge tipping, we need `capture_method="manual"` +
   capture flow. Preference?
4. **Decline retry policy**: how many attempts? Auto or only manual?
   (Recommendation: manual only — auto-retry on a declined card just
   racks up processor fees.)
5. **Payment at ride-request vs completion**: many ride-share apps
   authorize at request and capture at completion. We currently
   charge-at-completion. Keep as-is, or move to auth-then-capture?
   (Recommendation: keep as-is for MVP; auth-then-capture is a P1.)

## 10. Acceptance criteria

A PR closes P0-5 when all of the following are true:

- [ ] Card-path `process_payment` calls real `stripe.PaymentIntent.create`
- [ ] `test_p0_ship_blockers.py::test_card_decline_marks_payment_failed_and_allows_retry`
      passes (xfail removed)
- [ ] New tests for success, decline, requires_action, idempotency all green
- [ ] Rider-app E2E spec covers card-decline → retry UI
- [ ] Manual staging run against Stripe test mode: success, decline,
      and 3DS cards each yield the correct final state + receipt
- [ ] Webhook dedupe verified: replay `payment_intent.succeeded` →
      second call is a no-op
- [ ] No hardcoded `payment_status="completed"` in `drivers.py::complete_ride`
- [ ] Runbook entry added for "rider stuck on requires_action"
- [ ] Zero changes to the driver-app (payments are rider-side only)

---

## Appendix — useful code references

- Stripe helpers pattern: `backend/routes/payments.py:31-49`
  (get_or_create_stripe_customer)
- Idempotent webhook pattern: `backend/routes/webhooks.py:67-88`
- Decimal money arithmetic: `backend/routes/rides.py` (`_d`, `_round`,
  `_f` helpers) — use these, not float math
- Existing receipt email path: `backend/utils/email_receipt.py`
  (unchanged)
- Existing wallet debit (parallel implementation to model): `backend/routes/rides.py:1118-1147`
