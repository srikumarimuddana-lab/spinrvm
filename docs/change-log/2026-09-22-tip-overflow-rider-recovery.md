# Tip overflow: rider recovery

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Surface / domain | rider-app / payments |
| Related gap | PR #5712 investigation: small tips silently dropped |

## Issue and root cause

A hold can cover the fare but leave a separate tip remainder below the processor minimum. The backend remedy rejects this before capture. The rider's existing generic HTTP 400 handler would incorrectly suggest changing cards instead of explaining how to adjust the tip.

## Remediation and alternative

Recognize the structured `tip_overflow_below_minimum` response and show the server's exact adjustment guidance with **Edit tip** and **Contact Support**. The alternative of automatically increasing, dropping, or retrying the tip changes the rider's payment instruction; an explicit edit preserves their choice. No automatic request follows this validation failure.

## Risk, consumers, and user experience

The only runtime consumer is `rider-app/app/ride-completed.tsx`. Its existing `!paymentOk` return keeps the screen mounted, its `cancel` alert action dismisses without navigation, and a later submit reads the edited amount. Grep also found unit tests, the screen test, the payment-completion E2E test, and a documentation reference in `components/bookingProposal.ts`; these are not additional runtime consumers. This helper does not write balances, ride state, or Stripe objects. The backend must release its processing claim and preserve the existing authorization on this error.

Riders see a specific warning after submission instead of an unrelated card error. Their entered tip stays visible. Success, 3DS, decline, invoice, and review-hold branches are unchanged. This is handling for an explicit new backend validation response; it needs no separate client feature flag. Coordinate client release with backend validation so installed older clients do not show the generic message indefinitely.

## Files

| File | Change | Reason |
|---|---|---|
| `rider-app/utils/attemptRidePayment.ts` | Structured validation alert | Keep tip correction actionable |
| `rider-app/utils/__tests__/attemptRidePayment.test.ts` | Wrapped/direct error and edited retry regressions | Prevent automatic retry or false success |
| This document | Impact and verification record | Release gate |

## Before / after

```ts
// Before: any non-policy 400
return { ok: false, alert: UNKNOWN_ERROR_ALERT }; // Change Card
// After: this specific code
return { ok: false, alert: { title: 'Adjust your tip', message,
  variant: 'warning', buttons: [{ text: 'Edit tip', kind: 'cancel' }, SUPPORT_BTN] } };
```

## Verification

The new wrapped/direct response tests first failed against the generic card alert. After implementation, the pure utility suite passed **23 tests** using Jest/ts-jest with a Node environment and injected API/Stripe dependencies. This exercised actual utility code, with no screen or native renderer. An adjusted second attempt sends the new tip amount; the rejected attempt makes exactly one API request and never invokes 3DS.

The cached test toolchain emitted a missing Expo tsconfig warning; transpilation and assertions completed. This is not a full mobile typecheck. No production app build, screenshot comparison, device test, staging payment, or live charge was run for this slice. Rider/driver apps have no active visual regression tooling. The notification/mobile verification record and combined release checklist track further app checks.

Integration follow-up: with the locked mobile dependencies installed, the actual rider Jest configuration passed all 102 tests across payment orchestration, custom-tip validation, the ride-completed screen, and notifications. The production Android JavaScript/Hermes export also compiled the new `tip_overflow_below_minimum`/`Adjust your tip` branch. This supersedes the earlier cached-toolchain limitation; no signed native app or device test was run.

## Rollback

This alert-only change has no persistent effects and no remote switch. Reverting the mobile release restores generic error display; retain backend safeguards so rollback never re-enables silent tip loss. No refund/data reversal is needed for the rejected request because the coordinated backend guard must run before money movement. Existing historical charges require separate read-only reconciliation before any authorized correction.
