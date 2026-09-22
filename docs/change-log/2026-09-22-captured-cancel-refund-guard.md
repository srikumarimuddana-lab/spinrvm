# Change Impact & Risk Log — captured cancellation refund guard

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | Codex |
| Surface(s) | backend |
| Domain (Sentry tag) | payments / rides |
| PR / commit link | `25357e588`, `f9dc45784`, `e7cfd0b66`; no PR opened by this agent |
| Related issue or gap ID | Combined review, finding: captured-refund failure can fall through to another cancellation-fee charge |

## 1. Issue / gap identified

When a booking PaymentIntent was already captured, a failed or ambiguous refund of the excess could still be followed by a new cancellation-fee charge. The rider could therefore have the unrefunded capture plus a duplicate fee charge while the driver cancellation cleanup continued.

## 2. Root cause

The captured-refund error branch correctly left `fee_taken_from_hold` at zero because it could not prove how much was retained. The generic fresh-fee guard treated that zero as proof that no fee had been collected and allowed another charge. A zero amount alone cannot represent an unresolved refund outcome.

## 3. Fix / remediation

The route now tracks unresolved captured-refund state separately from the retained fee amount. A failed, raised, or unknown refund result blocks the separate fee charge. The ride remains cancelled, driver availability / insurance-period / notification cleanup continues, and captured money remains available to the existing reconciliation process. No refund is represented as complete unless Stripe confirmed it.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface, payment behavior within the rider-cancel route.** The guard applies only to a ride whose booking PI was already captured and whose excess-refund outcome is unresolved.
- Other readers/writers checked: live-hold partial capture and release in `backend/routes/rides/cancellation.py`; `cancel_fee_payment_intent_id` and cancellation fee status writes; `financial_events` refund recording; driver release and insurance-period cleanup; the webhook refund readers; and `backend/scripts/reconcile_cancelled_captured_refunds.py`, which selects cancelled / captured rides with no recorded refund and checks Stripe before repair.
- A rider cancellation fee that previously could be charged despite the unresolved capture will now remain uncharged through this route. This avoids duplicate money movement but may leave a cancellation fee outstanding until the refund state is reconciled. The route still pays the configured driver cancellation share and releases/notifies the driver; this change does not make the rider refund or fee state appear resolved.
- No ride state transition, schema, setting, background loop, wallet delta, or insurance-period write was added. The existing manual reconciler is not run automatically.

## 5. User-experience effect

- **Who sees a difference:** rider and support/reconciliation operators; drivers retain existing cleanup and notifications.
- The rider still receives the normal successful cancellation response. The unresolved Stripe outcome is surfaced in backend error logs for reconciliation; this patch does not add a rider-facing refund-status message. A rider may therefore need support if Stripe's refund result remains ambiguous.
- The behavior can occur during an active cancellation request but does not change an already-running session or require an app update. No notification copy changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/cancellation.py` | Tracks unresolved captured-refund outcome and blocks the generic fee charge | Avoids charging separately while the captured amount may still include the fee |
| `backend/tests/test_cancel_already_captured_refund.py` | Adds nonzero-fee definite-failure and ambiguous-response cases; asserts driver availability and notification continue | Protects the double-charge guard and required cancellation cleanup |
| `docs/change-log/2026-09-22-captured-cancel-refund-guard.md` | This impact and risk record | Records behavior, scope, and verification |

## 7. Before / after

```python
# Before
if total_cancel_fee > 0 and fee_taken_from_hold <= 0:
    charge_separate_cancellation_fee()
```

```python
# After
if total_cancel_fee > 0 and fee_taken_from_hold <= 0 and not _captured_refund_unresolved:
    charge_separate_cancellation_fee()
```

The unresolved flag is set for a raised, missing, or non-success refund result; it does not assert that a refund failed at Stripe.

## 8. Rollback plan

There is no feature flag or configuration switch for this narrow guard; runtime rollback requires a code redeploy. A code revert alone does not reverse Stripe money. If reverting, do not issue another cancellation fee or label a refund complete based on the old route behavior; reconcile each cancelled/captured PI and any Stripe refund against the ledger first. The existing operator-run `reconcile_cancelled_captured_refunds.py` is the data-level repair path and checks Stripe state before applying a refund.

## 9. Verification performed

- [x] Automated tests: `python3 -m pytest backend/tests/test_cancel_already_captured_refund.py -q -o addopts=''` — 7 passed.
- [x] Definite refund failure and ambiguous/lost-response tests use a nonzero cancellation fee; they assert no second fee charge, driver availability, and driver notification.
- [ ] Manual repro in staging: not performed; no Stripe refund or charge was attempted.
- [x] Blast-radius search: cancellation route fee guards and captured-hold branch, refund ledger path, driver release/insurance cleanup, webhooks, and the cancelled-captured-refund reconciler.
- [x] Reviewed against `CLAUDE.md` money, Stripe-refund, cancellation-state, and error-surfacing conventions.
- [x] Feature flag: not added. This prevents a duplicate charge only for an unresolved captured-refund outcome; no setting or fee policy changes.
- Production build: not applicable; no app files changed.

## 10. Sign-off and unverified boundaries

- The mocked route regression passes; no live Supabase, Stripe, or staging exercise was performed. The existing manual reconciler was inspected but not run.
- Driver cleanup is asserted through mocks, not a live insurance-period transition or push delivery.
- No visual tooling applies to this backend-only change.
