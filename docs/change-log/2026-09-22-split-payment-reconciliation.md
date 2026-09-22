# Change Impact & Risk Log — split card-payment reconciliation

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | Codex |
| Surface(s) | backend |
| Domain (Sentry tag) | payments / rides |
| PR / commit link | `a98a017d8`, `fe53cc552`, `700fa19cc`, `64fcf69a4`; no PR opened by this agent |
| Related issue or gap ID | Combined review: split fare/tip PaymentIntents were each classified underpaid; paid ride retained `paid_at=NULL` |

## 1. Issue / gap identified

A ride settled through a fare-hold PaymentIntent and a separate overflow-tip PaymentIntent could have $2.61 received as $2.11 + $0.50, while each individual `payment_intent.succeeded` event was compared to the full $2.61 obligation and marked underpaid. Separately, the main booking-hold event could arrive while the ride still stored a zero tip and mark it paid before the app finalized the overflow; card settlement did not stamp `paid_at` on the app finalizer path.

## 2. Root cause

Final integration review caught a metadata-contract mismatch in the new early-event guard: `charge_ride` emits `rider_id`, while the first guard/test used `user_id`. The regression was changed to the actual Stripe metadata shape and failed because the webhook finalized instead of deferring. The handler now matches `rider_id`, and all 17 success-webhook tests pass. This verifies the guard against the actual producer's payload.

The ledger recorded the combined capture as one aggregate `stripe_charge` row but only referenced its primary PI; it did not identify the second successful PI and its cents. The webhook had no safe component proof, so it compared each PI's `amount_received` to the full ride obligation. It also checked the amount before deferring signed booking-hold/fresh-completion PI events while `payment_status=processing`. The app writes the requested tip during final settlement, so the webhook could read a stale fare-only ride. Finally, the legacy card finalizer and the atomic RPC's current migration-337 body did not write `paid_at`.

## 3. Fix / remediation

When hold capture plus overflow charging succeeds, the single aggregate ledger row now stores a versioned `component_payment_intents` map with the exact PI IDs and cents. The webhook accepts an individually under-sized PI only if the ride's primary ledger row has the ride's primary PI as its `ref`, all component PI IDs are unique, the event amount matches its component, and component cents sum exactly to both the ledger aggregate and the fare-plus-tip obligation. It only acknowledges a verified component after the ride has an authoritative settled status; non-settled events are unclaimed and retried. Before the amount fast path, signed booking-hold and fresh completion-charge webhooks are also deferred while the app owns the ride in `processing`.

Legacy card settlement now writes `paid_at` with the paid flip. Migration 443 updates the atomic settlement RPC while preserving migration 337's canonical driver-earnings calculation and access grants.

## 4. Risk & impact on existing functionality

- **Blast radius: backend payment settlement and Stripe webhook handling.** Touched readers/writers are `settle_card`, `_settle_against_hold`, `record_payment_event`, the atomic `settle_ride_card_payment` RPC, the `financial_events` ledger, `rides.payment_status`/`payment_intent_id`/`paid_at`, and `payment_intent.succeeded` claim/unclaim processing.
- `payment_intent.succeeded` also handles PaymentSheet / Google Pay rides. Those single-PI events that cover the entire obligation keep the existing path. The new component check is reached only for an individual PI whose amount is below the obligation; it cannot infer components from PI metadata or `payment_status` alone.
- A captured booking PI or fresh completion PI received during app settlement now causes an unclaim + 503/retry. The app finalizer must complete before the webhook acknowledges it. If the app finalizer stalls, Stripe retries only for its configured delivery window; existing payment retry/reconciliation and operator review remain recovery paths. If unclaim itself fails, the handler logs critical and requires manual replay.
- A valid component map on a ride still marked pending, failed, or processing does not mark the ride paid. This prevents the webhook from racing the app's tip credit and finalization; it can also delay a webhook acknowledgement while app settlement is genuinely stuck.
- Ordinary single-PI underpayment on settled rides still fails closed. No wallet or corporate charge, second ledger header, or historical ledger mutation is introduced. Migration 443 only alters the atomic card-settlement function; it is additive to the existing migration history.

## 5. User-experience effect

- **Who sees a difference:** no new visible screen or notification; rider payment completion, receipt timing, and support reconciliation can be affected.
- The rider still receives the normal process-payment result from the app finalizer. The component webhook itself does not replace the primary PI or create another charge. The fix removes false underpayment records and sets `paid_at` at settlement; when a webhook races the finalizer, its acknowledgement may be retried instead of prematurely marking the ride paid.
- The change can occur mid-payment but does not change booking price, tip policy, or rider choice. No notification copy changed. `ledger_atomic_settle_enabled` behavior remains the same; migration 443 must be applied before relying on its updated timestamp write.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/payment_service.py` | Stores exact capture + overflow PI component IDs/cents in aggregate ledger metadata; legacy finalizer stamps `paid_at` | Makes the combined amount auditable and records the settlement time |
| `backend/routes/webhooks.py` | Verifies component map, defers in-flight hold/fresh completion events, retries unsettled component events | Prevents false underpayment and early paid flips without trusting status alone |
| `backend/migrations/443_settle_card_paid_at.sql` | Replaces atomic RPC from migration 337 with its canonical earnings logic plus `paid_at` | Keeps timestamp inside the atomic paid flip and preserves the reviewed earnings formula |
| `backend/tests/test_settle_card_capture.py` | Asserts exact component map and finalizer timestamp | Guards the aggregate writer |
| `backend/tests/test_webhooks_main.py` | Covers component order, mismatched amounts, pending/failed/processing retry, and stale-tip races | Guards webhook validation and app-finalizer ownership |
| `docs/change-log/2026-09-22-split-payment-reconciliation.md` | This impact and risk record | Documents the behavior and boundaries |

## 7. Before / after

```python
# Before: each PI is compared with the full fare-plus-tip; an under-sized
# component is permanently marked underpaid.
if received_cents < owed_cents:
    await mark_stripe_event_processed(event_id)
    return {"underpaid": True}
```

```python
# After: defer signed app settlement; otherwise require exact aggregate proof.
_booking_hold_in_flight = source == "ride_booking_authorization" and pi_id == ride_pi
_completion_charge_in_flight = source == "ride_completion_charge" and meta_user_id == rider_id
if ride_status == "processing" and (_booking_hold_in_flight or _completion_charge_in_flight):
    await unclaim_stripe_event(event_id)
    raise HTTPException(status_code=503, detail="Payment settlement is still being finalized; retry")
if received_cents < owed_cents:
    _component_verified = _verified_split_component(...)
    _settlement_finalized = ride_status in SETTLED_PAYMENT_STATUSES
    if _component_verified and _settlement_finalized:
        await mark_stripe_event_processed(event_id)
        return {"component_payment": True}
    if _settlement_pending:
        await unclaim_stripe_event(event_id)
        raise HTTPException(status_code=503, detail="Payment settlement is still being finalized; retry")
    # Invalid proof on a settled ride remains underpaid.
```

The implementation checks map version, unique PI IDs, exact event cents, primary ledger `ref`, aggregate cents, and authoritative owed cents.

## 8. Rollback plan

No new feature flag was introduced. If the webhook logic must be rolled back, keep any event whose component proof is uncertain fail-closed; do not restore an unconditional paid/processing bypass. If migration 443 has been applied, restore the exact `settle_ride_card_payment` function body from migration 337 using `CREATE OR REPLACE` and preserve its service-role-only grants. That function rollback does not remove timestamps already written. Do not edit prior `financial_events`, refund or recharge Stripe PIs, or backfill paid state during code rollback; review each ride against Stripe and the ledger first. No historical data repair was performed here.

## 9. Verification performed

- [x] Targeted tests: `python3 -m pytest backend/tests/test_cancel_already_captured_refund.py backend/tests/test_settle_card_capture.py backend/tests/test_payment_retry.py backend/tests/test_webhooks_main.py::TestStripeWebhookPaymentIntentSucceeded -q -o addopts=''` — 66 passed, one Starlette deprecation warning.
- [x] Webhook regressions cover fare PI arriving before stored tip, fresh full completion PI arriving against stale tip, either split component arriving first, true partial/mismatched map, missing aggregate row, and pending/failed/processing rides refusing component acknowledgement.
- [x] Disposable PGlite PostgreSQL 18.3 fixture executed migration 443: `paid_at` stamped; canonical earnings returned $2.51 for $2.11 fare / $0.10 fees / $0.50 tip despite stale prior earnings; replay returned `NULL` with one ledger row; `anon` could not execute while `service_role` could. No production schema or data was used.
- [ ] Manual repro in staging: not performed; no Stripe event was replayed and no live PI was charged/refunded.
- [x] Blast-radius search: `settle_card`, `_settle_against_hold`, `record_payment_event`, atomic RPC definition and call site, `payment_intent.succeeded`, ride paid/status fields, webhook claim/unclaim, and payment retry/reconciliation references.
- [x] Reviewed against `CLAUDE.md` money, Stripe idempotency, webhook-claim, append-only-ledger, and migration conventions.
- [x] Feature flag: no new flag. Existing atomic-settlement flag is unchanged; migration 443 is required for the atomic timestamp behavior.
- Production build: not applicable; no app files changed in this split-payment implementation.

## 10. Sign-off and unverified boundaries

- No real Stripe endpoint retry sequence, live Supabase call, or native PostgreSQL concurrency/race test was run. PGlite validates function semantics and grants with fixture tables but does not prove production concurrency behavior.
- Webhook claim/unclaim behavior was tested with mocks; event delivery ordering and the Stripe retry window still need a staging integration exercise.
- No visual tooling applies to this backend-only change.
