# Spinr — User-Facing Message Audit (found → fixed)

**Date:** 2026-09-18
**Question asked:** are all in-app messages understandable by a rider/driver, and do any of them show a
function, field, or internal identifier instead of plain English?
**Scope:** every string that can reach a human — rider-app, driver-app, admin-dashboard, shared, and
every backend `HTTPException` detail the apps render verbatim. 2,639 messages catalogued.

Current per-screen list (post-fix): **`2026-09-18-user-facing-messages-list.md`**.
Change Impact & Risk entry: **`docs/change-log/2026-09-18-user-facing-message-rewrite.md`**.

---

## 1. Headline

**No function name was displayed to a user anywhere.** No `handleX()`, no `claim_driver_atomic`, no
`corporate_wallet_apply_delta`, no stack frames. Every hardcoded in-app message in rider-app and
driver-app was already plain prose.

What *was* leaking: **field names, ride-state names, internal constants and bare machine tokens** —
and all of it came from the **backend 4xx layer**, which the apps print verbatim. Those are now fixed.

| | Before | After |
|---|---:|---:|
| Rider-app messages needing a fix | 20 | 8 (all mapped `ERR_*`, never rendered) |
| Driver-app | 6 | 1 (mapped `ERR_*`) |
| Shared / either app | 34 | 23 (all mapped `ERR_*`) |
| Corporate portal | 5 | 1 (mapped `ERR_*`) |
| Admin (internal staff) | 65 | 65 (out of scope, see §5) |

The remaining rows are all `ERR_*` sentinels, which the client never renders — they now resolve to
real copy via `shared/errors/sentinelMessages.ts`. See F6.

---

## 2. How a message reaches the user

This decides which strings can render and which cannot:

```
backend raise HTTPException(...)
        |
        +-- 5xx --> utils/error_handling.py::http_exception_handler
        |             route text DISCARDED, replaced with one generic sentence
        |             (only a short ERR_* sentinel passes through)
        |
        +-- 4xx --> PII redacted, sentence KEPT and sent to the app
                      |
                      v
        shared/api/client.ts::extractError -> getApiErrorMessage
            drops: "Request failed with status code N", "Network Error",
                   JSON parse errors, engine crashes, raw java.net/ECONNREFUSED
            maps:  ERR_* sentinels -> real copy  (NEW)
            keeps: the backend's 4xx sentence  <-- WHAT THE USER READS
                      |
                      v
        shared/errors/errorPresentation.ts::presentError -> title + severity
```

The sanitisation layer was already good — engine crashes, native network text and 5xx internals
cannot reach a user. The gap was that **a 4xx sentence is trusted completely**, and ~126 of those
sentences had been written for a developer.

---

## 3. Findings and what changed

### F1. Bare machine tokens as the whole message — FIXED

| Was | Now | Source |
|---|---|---|
| `forbidden` | This ride belongs to a different account, so you can't pay for it. | `routes/payments.py` |
| `payment_already_processing` | This payment is already being processed. Give it a moment before trying again. | `routes/payments.py` |
| `company_not_found` | We couldn't find that company account. | `routes/corporate_subscriptions.py` |

### F2. Internal ride-state names quoted at the driver — FIXED

The worst of the set: a driver mid-shift read the state machine's own vocabulary.

| Was | Now |
|---|---|
| `Ride is in status 'driver_arrived'; cannot perform this action from that state (allowed: ['driver_accepted']).` | This ride is waiting for you to start the trip, so that action isn't available right now. Refresh to see its latest status. |
| `Ride is not in driver_accepted state` | This ride has already moved on, so we couldn't mark you as arrived. Refresh to see its current status. |
| `Ride is not in driver_arrived state` | We couldn't start this trip. Make sure you've marked yourself as arrived, then refresh to see the ride's current status. |
| `Cannot decline ride in status 'driver_assigned'` | This ride can no longer be declined — it has already moved on to another driver or been cancelled. |
| `Cannot start ride with status: searching` | This trip can't be started yet. Make sure you've arrived at the pickup first. |

The generic guard now renders through a `_RIDE_STATE_PHRASE` map in `routes/drivers/_shared.py`, so
the raw value can never be interpolated again.

### F3. Internal field names quoted at the user — FIXED

| Was | Now |
|---|---|
| `calc_mode must be flat, per_km, or percentage` | Choose a calculation mode: flat, per kilometre, or percentage. |
| `Invalid scheduled_time format. Use ISO 8601.` | We couldn't read that pickup time. Please choose a date and time again. |
| `discount_type must be 'flat' or 'percentage'` | Choose a discount type: a flat amount or a percentage. |
| `period_type must be weekly or monthly` | Choose either a weekly or a monthly statement. |
| `period_start must be YYYY-MM-DD` | We couldn't read that start date. Please choose it again. |
| `auto_topup_threshold and auto_topup_amount must be set before enabling` | Set both a top-up threshold and a top-up amount before turning auto top-up on. |
| `No payment method on file — provide payment_method_id or save a default card first` | No payment method on file. Add a card, or save one as your default, then try again. |
| `This module requires super_admin` | This section is restricted to super admins. |
| `Access denied — module 'x' not in your role permissions` | You don't have access to this section. Ask a super admin to grant it. |
| `No user_id and admin has no id claim` | We couldn't tell which user to send this to. |
| `password_too_short: …`, `password_complexity_required: …`, `password_too_common: …` | code prefix dropped; sentence kept |
| `UUID is required` / `Invalid UUID format` | A required ID is missing. / That ID is not in a valid format. |
| `Invalid datetime format. Use ISO 8601 format.` | We couldn't read that date and time. Please choose it again. |

### F4. Developer-only instructions in a user-facing error — FIXED

The add-card endpoint told the cardholder to do the integrator's job:

> Raw card data is not accepted. Tokenize card details client-side using Stripe.js /
> @stripe/stripe-react-native and submit only `{'payment_method_id': 'pm_...'}`.

Now: *"For your security, card details have to be entered in the secure card form. Please add your
card again from the payment screen."* The integration contract stays on the endpoint docstring and in
the existing `logger.error`. The PCI posture is unchanged — still a plain 400 raised before the body
is read for logging, still no echo of the offending keys.

One more told a corporate customer to flip a Spinr-internal setting:

> Corporate subscription billing is not yet enabled — turn on
> `corporate_subscription_billing_enabled` in Settings once verified in staging.

Now: *"Subscription billing is not available for your account yet. Please contact Spinr support."*
Where staff actually enable it is kept as a code comment.

### F5. Admin-authored `status_reason` shown to a rider — CHECKED, DELIBERATE

`routes/rides/booking.py:426` and `:444` print whatever an admin typed when suspending an account.
This looked like a leak. It isn't: the admin dialog's own placeholder
(`admin-dashboard/src/app/dashboard/users/page.tsx:1141`) reads **"Shown to the rider and recorded in
the audit log."** — the operator is warned at the point of entry. Left as-is.

### F6. The 17 `ERR_*` sentinels had no copy — FIXED CLIENT-SIDE

These were never a leak: `isMachineErrorSentinel` in `shared/api/client.ts` strips any
`^ERR_[A-Z0-9_]+$` before it can render. But nothing mapped them, so a precise reason collapsed into
whatever generic fallback the call site passed. A rider who double-tapped *Tip* read
*"Something went wrong. Please try again."*

Fixed on the client, not the backend, deliberately: `_should_sanitize_5xx_detail` only lets a 5xx
detail through when it matches the `ERR_*` shape, so that pattern is load-bearing and rewriting those
raises into prose would punch a hole in the 5xx leak guard.

`shared/errors/sentinelMessages.ts` now maps all 17. The OTP sentinels are deliberately excluded —
their call sites already pass better screen-specific copy.

### F7. Every 5xx read "Internal server error" — FIXED

318 raise sites collapsed to that phrase, plus *"An unexpected error occurred"* on the unhandled path
and the same phrase again as `InternalErrorException`'s default — three pieces of system vocabulary
for one situation. All three now read:

> Something went wrong on our end. Please try again in a moment.

Sanitising itself is untouched.

---

## 4. What was already clean

- **No function names anywhere**, in any surface.
- **No raw exception text reaches a rider or driver.** Only 6 app call sites pass a server-supplied
  `.message` through, each a deliberate server-authored sentence with a written fallback behind it.
- **i18n is complete** — 70 rider keys and 209 driver keys all defined, so no screen can render a raw
  `some.dotted.key`.
- **Engine and network noise is filtered** before display.
- **Toast text is length-clamped** (`clampToastMessage`, 140 chars) — every new message fits.

---

## 5. Deliberately not changed

- **Admin-dashboard's 65 flagged rows.** Internal staff are a different audience and `driver_id` is
  their working vocabulary. Worth a separate pass, not folded into a rider/driver copy fix.
- **`routes/webhooks.py`'s `Invalid payload` / `invalid JSON`.** Stripe calls those endpoints, not a
  human. Correctly technical.
- **The `ERR_*` raises themselves** — see F6.
- **Surfacing `X-Request-ID` to the user** so they can quote it to support. The response already
  carries it; displaying it needs app-side work, not a backend string change.

---

## 6. What was NOT verified

- **No tests were run.** PyPI and npm are both blocked by this environment's network policy
  (`pip install pytest` and `npm install` both fail), so the backend suite and the Jest suites could
  not be executed here. Verification was: `ruff check` + `ruff format` clean on every touched file,
  `python -m py_compile` on each, `tsc --noEmit --strict` on the new TypeScript file, and a grep
  sweep for every assertion pinning a changed string (7 found, all updated in the same commits).
  **CI is the real gate for this change.**
- **Nothing was triggered in a running app.** Message reachability is inferred from the route.
- **No visual regression tooling exists for rider-app or driver-app**, so the new copy has to be
  eyeballed on a device — per CLAUDE.md release gate 6.
- **Push/SMS/email copy is out of scope** — this covers in-app toasts, alerts, inline errors and
  backend API messages only.
- **Static screen copy is out of scope** — labels, headings, empty states and button text rendered
  directly in JSX were not catalogued.
- **Non-English locales unchecked** — only `en`/`en-CA` keys were verified present; `fr`, `fr-CA`,
  `es`, `zh` were not audited, and the new English copy is not yet translated.
- **Surface attribution is heuristic** in the message list — derived from route path, not from
  tracing callers.
