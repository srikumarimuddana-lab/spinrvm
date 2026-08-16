# Change Impact & Risk Log — booking toast leaked a raw JS engine error

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-16 |
| Author | Claude Code (session: stripe-booking-error) |
| Surface(s) | rider-app (+ `shared/api/client.ts`, also consumed by driver-app) |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/stripe-booking-error-551scp` |
| Related issue or gap ID | Live-testing report: "Booking Failed — undefined is not a function" after switching to live Stripe keys |

## 1. Issue / gap identified

A rider tapping **Confirm** on the "Choose a ride" screen saw a red toast reading
**"Booking Failed — undefined is not a function"**. That string is Hermes crash text, not a
backend rejection: a client-side throw inside the booking handler was rendered to the rider as
the reason their booking failed, and the real cause was recorded nowhere.

## 2. Root cause

Two separate defects, both on the booking error path.

**(a) `getApiErrorMessage` passes engine crash text through as toast copy.**
`shared/api/client.ts::getApiErrorMessage` prefers `err.response.data`, and when an error has no
HTTP body it falls back to `err.message`. It already filtered Axios noise
(`Request failed with status code N`, `Network Error`, `timeout of …`) and JSON-parse
`SyntaxError`s — but not engine-generated `TypeError` / `ReferenceError` / `RangeError`. A
`TypeError` carries no `.response`, so its message ("undefined is not a function") went straight
to the toast. Any client-side crash anywhere in the app that lands in a catch block using this
helper produced the same class of user-facing gibberish; booking is just where it was noticed.

**(b) The crash was never captured, so it could not be diagnosed.**
`rideStore.createRide` calls `recordNonFatal` inside its own `try` (`rider-app/store/rideStore.ts:790`),
so an error thrown in `ride-options.tsx::proceedWithBooking` **after** `createRide` resolves —
`Analytics.*`, `scheduleReminder`, `router.replace`, the `'requires_action' in ride` check — never
reached Crashlytics at all. The catch block only toasted. There is no stack, no request id, and
no Crashlytics record for the reported occurrence, which is why the exact throwing line could not
be identified from the repo alone (see §11).

Why it surfaced now: under **test** Stripe keys the standard test cards never return
`requires_action`, so the SCA branch of the booking response was effectively dead code. Live keys
exercise it (and the rest of the real Stripe error surface) for the first time, which is what
changed the shape of what comes back from `POST /rides` (§11).

## 3. Fix / remediation

- Added `isEngineError()` to `shared/api/client.ts` and wired it into `getApiErrorMessage`, so an
  engine crash yields the **caller's own fallback** ("Failed to book ride. Please try again.")
  instead of engine text. Backend messages, deliberately-thrown domain messages
  (`'A ride is already active'`, the dropoff-proximity guard), and `RateLimitError` copy are all
  unaffected — the response-body branch still runs first.
- Added crash capture to both booking catch blocks (`ride-options.tsx::proceedWithBooking`,
  `payment-confirm.tsx::handleBookRide`): when the error is an engine crash, it is logged with
  `console.error` and sent to Crashlytics via `recordNonFatal` with `{screen, action}` context.

This is deliberately **not** error-softening: the message the rider sees becomes honest, and the
error becomes *more* visible to engineering (Crashlytics record + stack where there was none).

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface, but strictly narrowing.**

`getApiErrorMessage` has **54 non-test consumers** — 26 in `rider-app/app`, 3 in `rider-app/store`,
1 in `rider-app/lib` (`notifyError`), 9 in `driver-app/app/driver`, 8 in `driver-app/app`, 1 in
`driver-app/app/driver/(tabs)`, 2 in `driver-app/store`, 1 in `driver-app/hooks`, 1 in
`shared/errors` (`errorPresentation`), 1 in `shared/api`. Every one of them is affected.

The change only alters the outcome for errors that are engine crashes — cases where the current
behaviour is to print `TypeError` text into a user-facing toast. Those are already broken; the new
outcome is the fallback that caller already supplies. No path that currently produces a correct
message changes: the `response.data` branch is evaluated **before** the `.message` branch, so a
crash-shaped error that also carries a backend body still surfaces the backend reason (covered by
a test). `notifyError` and `presentError` are unaffected — they choose title/severity from the
error *code*, not the message.

Not touched: ride state machine, dispatch, money arithmetic, wallet/allowance deltas, background
loops, migrations, RLS. No backend change. `recordNonFatal` is already guarded (no-ops when the
Firebase native module is absent) and swallows its own failures, so adding two call sites cannot
introduce a new throw.

Residual risk: a genuine, user-actionable message that happens to match the engine-message regex
would be suppressed to the fallback. The patterns are anchored engine phrasings
(`^(undefined|null) is not (a function|an object|iterable)`, `is not a function`, `is not iterable`,
`is not defined`, `^Cannot read propert…`), and a test asserts the two real thrown domain messages
survive. Judged low.

## 5. User-experience effect

**Rider-facing, visible mid-session** to a rider on the booking screen at the moment a booking
crashes client-side.

- Before: red toast **"Booking Failed — undefined is not a function"**.
- After: red toast **"Booking Failed — Failed to book ride. Please try again."**
  (`payment-confirm`: "Could not complete your booking. Please try again.")

No new copy was written — the fallback strings already existed at both call sites and already ship
in other branches of the same handler. Driver-app riders/drivers see the equivalent improvement on
any screen where a crash previously leaked into a toast. No notification copy changed.

Not feature-flagged: the change replaces a developer-only error string with copy already shipping
at the same call site, and flagging it would mean deliberately keeping engine text in front of live
testers. Flagging is reserved here for the §11 follow-up, which is a real flow change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/api/client.ts` | Added `ENGINE_ERROR_NAMES`, `ENGINE_ERROR_MESSAGE`, exported `isEngineError()`; `getApiErrorMessage` now skips the `.message` branch for engine crashes | Stop engine crash text becoming user-facing toast copy |
| `rider-app/app/ride-options.tsx` | Import `isEngineError` + `recordNonFatal`; capture engine crashes in `proceedWithBooking`'s catch | The reported crash had no Crashlytics record and no stack |
| `rider-app/app/payment-confirm.tsx` | Same capture in `handleBookRide`'s catch | Same handler shape; `confirmPayment()` is the likeliest crash site here |
| `rider-app/__tests__/getApiErrorMessage.test.ts` | +12 cases: Hermes/JSC and V8 crash shapes, name-stripped errors, `ReferenceError`/`RangeError`, backend-body precedence, domain-message survival | Regression cover for both directions of the filter |

## 7. Before / after

```ts
// Before — shared/api/client.ts::getApiErrorMessage
const raw = anyErr?.message;
if (
  raw &&
  anyErr?.name !== 'SyntaxError' &&
  raw !== 'Request failed' &&
  !/^Request failed with status code/i.test(raw) &&
  // … network / timeout / JSON-parse filters …
) {
  return clampToastMessage(raw);   // TypeError message reaches the rider
}
return fallback;
```

```ts
// After
const raw = anyErr?.message;
if (
  raw &&
  !isEngineError(anyErr) &&        // engine crash → fall through to `fallback`
  anyErr?.name !== 'SyntaxError' &&
  raw !== 'Request failed' &&
  !/^Request failed with status code/i.test(raw) &&
  // … unchanged …
) {
  return clampToastMessage(raw);
}
return fallback;
```

```ts
// After — rider-app/app/ride-options.tsx::proceedWithBooking catch (new, additive)
if (isEngineError(error)) {
  console.error('[booking] client-side crash during booking', error);
  recordNonFatal(error, { screen: 'ride-options', action: 'proceedWithBooking' });
}
```

## 8. Rollback plan

`git revert` is a complete rollback here: the change is presentation + telemetry only. It writes
no DB rows, moves no money, creates no Stripe objects, and changes no ride state, so there is no
live data to remediate — the condition CLAUDE.md warns about does not arise. Reverting restores
the previous toast text exactly.

No feature flag exists for this path and none was added (see §5). If the message filter ever needs
to be disabled without a redeploy, that would require a new `app_settings` flag — not present today,
and called out as a limitation rather than implied.

## 9. Verification performed

- [x] **Automated tests run** — `rider-app`: full Jest suite, **492/492 passed** (57 suites), run
      twice consecutively. Includes 12 new unit cases in `__tests__/getApiErrorMessage.test.ts`.
      Targeted run of `getApiErrorMessage` + `notifyError` + `errorPresentation`: 38/38.
- [x] **TypeScript check** — `yarn tsc --noEmit` clean in `rider-app`.
- [x] **Cross-surface regression check** — `driver-app` full suite: 422/423. The single failure
      (`CarPlay … creates ONE live MapTemplate`) reproduces **identically on a clean tree** with the
      changes stashed — pre-existing, unrelated to this diff.
- [x] **Blast-radius grep performed** — searched every non-test importer of `getApiErrorMessage`
      (54 files, breakdown in §4); read `shared/errors/errorPresentation.ts` and
      `rider-app/lib/notifyError.ts` to confirm they key off error *code*, not message; traced all
      `createRide` callers (`ride-options.tsx`, `payment-confirm.tsx`,
      `components/BookingProposalCard.tsx`).
- [x] **Reviewed against CLAUDE.md conventions** — observability (no `print`, `console.error` for an
      actionable failure, non-fatal recorded with context); "do not silently swallow errors" (the
      error becomes *more* visible, not less); PIPEDA (no PII added — `{screen, action}` only, no
      ids, no coordinates, no addresses).
- [x] **Feature-flagged if user-visible and non-trivial** — not flagged; justified in §5.

## 10. What was NOT verified

- **No production build was run.** `npm run build` / EAS has no meaningful analogue for this diff and
  was not executed — verification is `yarn tsc --noEmit` + the full Jest suite, stated explicitly per
  CLAUDE.md rather than implied.
- **Not tested against live Supabase or live Stripe.** All tests use mocked API responses. The fix
  was not exercised against a real `requires_action` response from live Stripe keys.
- **The originally-reported crash was not reproduced.** This change makes the *next* occurrence
  diagnosable; it does not identify the throwing line of the reported one (see §11).
- **No visual regression check.** This repo has no snapshot/visual regression tooling for rider-app
  (standing gap — `ACTION_ITEMS.md`), so the toast-copy change was reasoned about, not screenshotted.
- **Driver-app was not manually exercised**, though it consumes the changed helper; coverage there is
  its automated suite only.
- **One cold-run flake observed and not reproduced**: on the first full rider-app run,
  `verifyEmailScreen.test.tsx › fires POST /users/verify-email/request on mount` timed out at 5000 ms.
  It passed in isolation both with and without the changes, and passed in both subsequent full runs
  (492/492 twice). That test touches neither booking nor `getApiErrorMessage`. Recorded rather than
  dismissed.

## 11. Open finding — NOT fixed here (needs a decision)

**On live Stripe keys, a card that requires 3-D Secure cannot complete a booking from any reachable
rider screen.**

`backend/routes/rides/booking.py:1026-1038` returns `{requires_action, payment_authorization}` and
creates **no ride** when the pre-auth needs SCA. Every rider entry point dead-ends on that response:

| Entry point | Behaviour on `requires_action` |
|---|---|
| `rider-app/app/ride-options.tsx:598` (the main "Choose a ride" screen) | Toast "Card authentication needed… pick another payment method" — no `confirmPayment` on this screen |
| `rider-app/components/BookingProposalCard.tsx:205` (AI assistant) | "finish booking on the payment screen", deep-links to `/(tabs)` → back to `ride-options` — a loop |
| `rider-app/app/payment-confirm.tsx:179-192` | **Has the complete two-step SCA flow** — but nothing in the app navigates to it. It is registered at `_layout.tsx:920` and otherwise orphaned (grep: no `router.push`/`replace` to `payment-confirm` anywhere) |

Under test keys this never fired (test cards don't require SCA), which is why it appeared only after
the live cutover. Wiring 3-D Secure into the live booking screen is a payments-architecture change on
a live-tested surface, so per CLAUDE.md pre-merge gate 9 it is escalated rather than shipped alongside
a presentation fix. Two candidate approaches: mirror `payment-confirm`'s two-step into `ride-options`
via `useStripe()`, or route `ride-options` → `payment-confirm` carrying the `client_secret`.

## 12. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`; justified as sufficient — no live data written)
- [x] Blast radius is stated, not assumed (54 consumers enumerated in §4)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5)
