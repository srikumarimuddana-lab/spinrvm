# Change Impact & Risk Log — sub-$0.50 tip overflow

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | Codex |
| Surface(s) | backend / rider-app |
| Domain (Sentry tag) | payments / rides |
| PR / commit link | `4d61a593d`, `e37001a69`, `112d5d656`; rider response handling in `4fbb3c96c`; no PR opened by these agents |
| Related issue or gap ID | Combined review: 1–49¢ tip overflow is sent as a real Stripe charge, rejected, then omitted from the final collected tip |

## 1. Issue / gap identified

When a tip exceeded a booking hold by $0.01–$0.49 and the authorization could not be incremented, settlement captured the hold and tried to charge the smaller remainder as a separate PaymentIntent. Stripe rejects CAD charges below $0.50, after which the ride could be finalized for less than the submitted tip.

## 2. Root cause

The hold settlement treated every positive remainder as chargeable. It captured the authorization before learning that a sub-$0.50 overflow PaymentIntent could not be collected; the decline path then reduced the recorded tip to the amount covered by the hold. Validation of the tip amount alone did not protect this boundary because the overflow depends on both the tip and the fare authorization.

## 3. Fix / remediation

After the existing optional increment attempt and before capture, the service detects a 1–49¢ remainder caused by a tip when the hold covers the fare. It returns `400 tip_overflow_below_minimum`, restores `payment_status` to pending, and leaves the booking PI, authorization, and requested tip untouched. The response gives the largest tip fitting the current hold and the smallest tip making the separate charge at least $0.50. The rider can choose either amount and retry. `min_tip_amount` stays unchanged.

## 4. Risk & impact on existing functionality

- **Blast radius: cross-surface payment attempt, narrowly limited to a Stripe-impossible overflow.** The backend branch is in `settle_card`; the rider utility interprets the typed 400 response so the rider stays in the editable payment flow.
- Other callers/readers checked: `POST /rides/{ride_id}/process-payment`, `settle_card` and `_settle_against_hold`, `payment_status` claim/retry path, `payment_intent.succeeded`, and `rider-app/utils/attemptRidePayment.ts`. The rider helper is also used by the ride-completed payment screen. See companion rider impact record `docs/change-log/2026-09-22-tip-overflow-rider-recovery.md`.
- A previously silent partial collection now requires an explicit rider choice. For tip overflow `r` where `0.01 ≤ r ≤ 0.49`, a $10.00 tip on a $25.00 fare with a $35.00 hold now returns a choice of tip ≤$10.00 (fits the hold) or tip ≥$10.50 (leaves at least $0.50 to charge); it does not capture $35.00 and discard the remainder.
- The guard runs only when the optional increment does not remove the remainder and when the authorization already covers the fare. A valid 50¢ remainder continues through the existing capture + separate charge path. An impossible fare-only under-hold is outside this tip-specific guard and remains subject to existing decline/reconciliation behavior.
- No wallet, corporate billing, fare calculation, driver payout, receipt line, schema, setting, or background-loop behavior changed. A successful incremental authorization still proceeds to one capture.

## 5. User-experience effect

- **Who sees a difference:** a rider whose submitted tip would require an unchargeable 1–49¢ follow-on payment; drivers see no change.
- Before, the endpoint could report settlement success with less than the submitted tip collected. Now the rider sees a specific adjustment choice and the pay screen remains editable; the hold is preserved while they decide. This is visible during the active payment attempt.
- No new notification copy was introduced. The companion app change maps the structured backend error to an actionable in-flow alert. It is not a pricing-policy minimum: `min_tip_amount` is not raised or enabled here.
- No screenshot or visual-regression test exists for the rider app surface; the utility/alert mapping was verified by its focused tests, not by a physical-device session.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/payment_service.py` | Adds pre-capture 1–49¢ tip-overflow validation and exact suggested bounds | Avoids capturing a hold when the remaining tip cannot be charged |
| `backend/tests/test_settle_card_capture.py` | Tests 1¢/49¢ rejection, failed increment preserving the hold, and the valid 50¢ boundary | Pins the Stripe minimum boundary and authorization safety |
| `rider-app/utils/attemptRidePayment.ts` | Handles the structured error as an editable tip-recovery outcome | Keeps the rider in the payment adjustment flow |
| `rider-app/utils/__tests__/attemptRidePayment.test.ts` | Tests the rider utility's error response handling | Prevents regression to a generic dead-end payment alert |
| `docs/change-log/2026-09-22-subminimum-tip-overflow.md` | This impact and risk record | Documents backend behavior and cross-surface risk |
| `docs/change-log/2026-09-22-tip-overflow-rider-recovery.md` | Companion app impact record | Documents rider-side response behavior |

## 7. Before / after

```python
# Before: capture the hold first, then try any positive remainder.
capture_amount = min(total_charge, authorized)
cap = await capture_ride(..., amount=capture_amount)
if total_charge - capture_amount > 0:
    over = await charge_ride(..., total_amount=total_charge - capture_amount)
```

```python
# After: reject an impossible tip remainder while the hold is still open.
remainder = _round(total_charge - authorized)
if 0 < remainder < Decimal("0.50") and tip_amount > 0 and authorized >= fare_amount:
    await db_supabase.update_ride(
        ride_id,
        {"payment_status": "pending", "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    return PaymentResult(
        success=False, error_code="tip_overflow_below_minimum", status_code=400
    )
capture_amount = min(total_charge, authorized)
cap = await capture_ride(..., amount=capture_amount)
```

The production helper uses Decimal rounding and also returns the maximum hold-covered tip and minimum separately chargeable tip.

## 8. Rollback plan

No feature flag or configuration switch exists for this fixed network-minimum guard; runtime rollback requires a code redeploy. A rollback must not restore the previous capture-then-drop behavior. If this branch needs replacement, keep the rider's hold open and return a retryable failure until a safe collection path is available. Do not release or refund historical holds automatically; compare each PI and captured amount with Stripe before repair. Do not change `min_tip_amount` as a rollback mechanism.

## 9. Verification performed

- [x] Backend tests: `python3 -m pytest backend/tests/test_settle_card_capture.py -q -o addopts=''` — 23 passed.
- [x] Rider helper tests: root reports 23 focused `attemptRidePayment` utility tests passed for companion commit `4fbb3c96c`.
- [x] Regression cases include 1¢ and 49¢ with zero capture/charge, failed incremental authorization with no release, and a successful 50¢ separate charge.
- [ ] Manual repro in staging: not performed; no Stripe charge or authorization was attempted.
- [x] Blast-radius search: route caller, service callers, payment status retry path, webhook, completed-ride payment UI, and imports of `attemptRidePayment`.
- [x] Reviewed against `CLAUDE.md` Decimal arithmetic, Stripe charge minimum, idempotency, and held-payment conventions.
- [x] Feature flag: not added. This blocks a network-invalid partial charge; the existing min-tip setting remains the product policy control and was not changed.
- Production build: **not run** for rider-app. The focused utility tests do not replace a native release build.

## 10. Sign-off and unverified boundaries

- Backend behavior is covered by mocked settlement tests. The app utility tests verify response mapping, but no live Supabase/Stripe call or physical-device UX test was performed.
- No active rider-app visual regression tooling exists; the screen was reasoned about and tested at its utility boundary, not screenshotted.
- The 1–49¢ remainder path is covered; currency/network behavior was not exercised against live Stripe.
