# Spinr — User-Facing Message Audit & Manual-Validation Checklist

**Date:** 2026-09-18  
**Question asked:** are all in-app messages understandable by a rider/driver, and do any of them leak a function, field, or internal identifier?  
**Scope:** every string that can reach a human — rider-app, driver-app, admin-dashboard, shared, and every backend `HTTPException` detail that the apps render verbatim.

Full row-by-row list for the manual pass: **`docs/audit/2026-09-18-user-facing-messages-list.md`** (2639 rows, grouped by surface then screen, with a tick box per message).

---

## 1. Headline answer

**No function name is displayed to a user anywhere in the apps.** Every hardcoded in-app message in
rider-app and driver-app is plain English prose. The leaks that do exist are *field names*, *state names*,
*constants* and *bare machine tokens* — and all of them originate in the **backend 4xx layer**, which the
apps print verbatim.

| Surface | Messages catalogued | Reads cleanly | Needs a wording fix |
|---|---:|---:|---:|
| RIDER APP | 429 | 409 | 20 |
| DRIVER APP | 471 | 465 | 6 |
| SHARED / EITHER APP | 216 | 182 | 34 |
| ALL APPS | 318 | 0 | 0 (+318 review) |
| CORPORATE PORTAL | 70 | 65 | 5 |
| ADMIN (internal staff) | 1135 | 1070 | 65 |

---

## 2. How a message actually reaches the user

This matters because it decides which strings are real and which never render:

```
backend raise HTTPException(...)
        |
        +-- 5xx --> utils/error_handling.py::http_exception_handler
        |             route text DISCARDED, replaced with "Internal server error"
        |             (only a short ERR_* sentinel passes through)
        |
        +-- 4xx --> PII redacted, sentence KEPT and sent to the app
                      |
                      v
        shared/api/client.ts::extractError  -> getApiErrorMessage
            drops: ERR_* sentinels, "Request failed with status code N",
                   "Network Error", JSON parse errors, engine crashes,
                   raw java.net/ECONNREFUSED text
            keeps: the backend's 4xx sentence  <-- THIS IS WHAT THE USER READS
                      |
                      v
        shared/errors/errorPresentation.ts::presentError  -> toast title + severity
```

So the sanitisation layer is genuinely good — engine crashes, network exception text and 5xx internals
can't reach a user. The gap is that a **4xx sentence is trusted completely**, and ~126 of those sentences
were written for a developer.

---

## 3. Findings — messages a user should never see

### F1. Bare machine tokens shown as the whole message

The user sees a lowercase snake_case token with no sentence around it. Nothing tells them what happened.

| Surface | What the user sees | Source |
|---|---|---|
| CORPORATE PORTAL | `company_not_found` | `backend/routes/corporate_subscriptions.py:161` |
| RIDER APP | `forbidden` | `backend/routes/payments.py:627` |
| RIDER APP | `payment_already_processing` | `backend/routes/payments.py:661` |

### F2. Internal ride-state names quoted at the user

Ride state-machine values are printed as if the rider or driver knows them. This is the worst live-testing artifact in the set — a driver reads it mid-shift.

| Surface | What the user sees | Source |
|---|---|---|
| DRIVER APP | `Ride is not in driver_accepted state` | `backend/routes/drivers/ride_flow.py:930` |
| RIDER APP | `Ride is not in driver_arrived state` | `backend/routes/rides/lifecycle.py:133` |

### F3. Internal field names quoted at the user

The message names a JSON field or DB column the user has no concept of.

| Surface | What the user sees | Source |
|---|---|---|
| CORPORATE PORTAL | `Corporate subscription billing is not yet enabled — turn on corporate_subscription_billing_enabled in Settings once verified in staging.` | `backend/routes/corporate_subscriptions.py:116` |
| SHARED / EITHER APP | `Invalid scheduled_time format. Use ISO 8601.` | `backend/features.py:1031` |
| CORPORATE PORTAL | `No payment method on file — provide payment_method_id or save a default card first` | `backend/routes/corporate_company.py:1242` |
| SHARED / EITHER APP | `No user_id and admin has no id claim` | `backend/routes/notifications.py:97` |
| SHARED / EITHER APP | `This module requires super_admin` | `backend/dependencies/__init__.py:824` |
| CORPORATE PORTAL | `auto_topup_threshold and auto_topup_amount must be set before enabling` | `backend/routes/corporate_wallet.py:349` |
| SHARED / EITHER APP | `calc_mode must be flat, per_km, or percentage` | `backend/features.py:556` |
| SHARED / EITHER APP | `calc_mode must be one of: {valid_modes}` | `backend/features.py:524` |
| RIDER APP | `discount_type must be 'flat' or 'percentage'` | `backend/routes/promotions.py:708` |
| SHARED / EITHER APP | `password_complexity_required: must contain at least one uppercase letter, one digit, and one symbol` | `backend/utils/password_policy.py:176` |
| SHARED / EITHER APP | `password_too_common: choose a less predictable password` | `backend/utils/password_policy.py:182` |
| SHARED / EITHER APP | `password_too_short: admin passwords must be at least 20 characters` | `backend/utils/password_policy.py:166` |
| DRIVER APP | `period_start must be YYYY-MM-DD` | `backend/routes/drivers/tax_exports.py:483` |
| DRIVER APP | `period_type must be weekly or monthly` | `backend/routes/drivers/tax_exports.py:479` |

### F4. Developer-only instructions in a user-facing error

The message tells the user to do something only an engineer could do.

| Surface | What the user sees | Source |
|---|---|---|
| SHARED / EITHER APP | `Invalid datetime format. Use ISO 8601 format.` | `backend/validators.py:414` |
| RIDER APP | `Raw card data is not accepted. Tokenize card details client-side using Stripe.js / @stripe/stripe-react-native and submit only {'payment_method_id': '` | `backend/routes/payments.py:996` |

### F5. Admin-authored free text is shown to a rider unfiltered

`backend/routes/rides/booking.py:426` and `:444` build the block message as
`(rider_row or {}).get('status_reason') or '<written fallback>'` — so whatever an internal admin typed
into `status_reason` when deactivating or suspending the account is printed to the rider verbatim.
The written fallbacks are fine (*"Your account is currently suspended. Please contact support."*); the
risk is the override path, which no wording review covers. Worth a manual look at what real
`status_reason` values look like in production.

### F6. The 17 `ERR_*` sentinels resolve to a generic message in the apps

These are **not** a leak — `shared/api/client.ts::isMachineErrorSentinel` strips any `^ERR_[A-Z0-9_]+$`
before it can render. But nothing in rider-app or driver-app maps them to a specific sentence either
(only `ERR_TOKEN_AUDIENCE` is referenced, and only in admin-dashboard), so the user gets the call site's
generic fallback — usually *"Something went wrong. Please try again."* — when a precise reason existed.

| Sentinel | Real reason the user never learns |
|---|---|
| `ERR_ACCOUNT_DELETED` | Account was deleted |
| `ERR_ACCOUNT_INACTIVE` | Account is inactive |
| `ERR_AUTH_UNAVAILABLE` | Sign-in service is down |
| `ERR_DATABASE` | Database failure |
| `ERR_DRIVER_ONLY` | Action is driver-only |
| `ERR_EMAIL_UNVERIFIED` | Email not verified yet |
| `ERR_FARE_EXCEEDED` | Payment is more than the fare |
| `ERR_FARE_UNDERPAID` | Payment is less than the fare |
| `ERR_FORBIDDEN` | Not allowed on this resource |
| `ERR_IDENTITY_INELIGIBLE` | Identity check failed / ineligible |
| `ERR_IDLE_TIMEOUT` | Signed out for inactivity |
| `ERR_REACTIVATION_EXPIRED` | Reactivation window expired |
| `ERR_RIDE_NOT_PAYABLE` | This ride can't be paid right now |
| `ERR_SESSION_REVOKED` | Session was revoked (signed in elsewhere) |
| `ERR_TIP_DUPLICATE` | You already tipped for this ride |
| `ERR_TOKEN_AUDIENCE` | Wrong app for this token |
| `ERR_TOKEN_REVOKED` | Session token revoked |

`ERR_TIP_DUPLICATE` is the clearest example: a rider who double-taps *Tip* is told
*"Something went wrong"* instead of *"You've already added a tip for this ride."*

### F7. Every 5xx reads "Internal server error"

318 backend 5xx raise sites all collapse to the literal phrase **"Internal server error"** 
(`utils/error_handling.py::http_exception_handler`). The suppression is correct and deliberate — it is what
stops `str(e)` leaking Stripe ids and Postgres constraint names. But the replacement phrase itself is
developer vocabulary. A rider mid-booking should read something like *"Something went wrong on our end.
Please try again in a moment."* plus the request ID already returned in `X-Request-ID`.

---

## 4. What came back clean

- **No function names anywhere.** No `handleX()`, no `claim_driver_atomic`, no `corporate_wallet_apply_delta`,
  no `.mutateAsync`, no stack frames in any user-visible string.
- **No raw exception text reaches a rider or driver.** Only 6 app call sites pass a server-supplied
  `.message` through, and each is a deliberate server-authored sentence with a written fallback behind it.
- **i18n is complete.** 70 rider keys and 209 driver keys are all defined — no screen can render a raw
  `some.dotted.key` because a translation is missing.
- **Engine/network noise is filtered.** `undefined is not a function`, `Network Error`,
  `java.net.NoRouteToHostException`, `JSON Parse error` are all suppressed before display.
- **Toast text is length-clamped** (`clampToastMessage`) so no message can overflow the banner.

---

## 5. Manual-validation checklist by scenario

Walk these in the message list (grouped by the same `scenario` headings). Counts are messages you can trigger per area.

| Journey stage | Rider | Driver | Shared/backend | Flagged |
|---|---:|---:|---:|---:|
| Sign-up / Sign-in / OTP | 63 | 32 | 23 | 5 |
| Booking a ride | 54 | 20 | 23 | 2 |
| Dispatch / matching | 15 | 3 | 0 | 0 |
| Active ride | 39 | 22 | 3 | 5 |
| Ride completion & rating | 26 | 3 | 6 | 0 |
| Payments / wallet / tips | 102 | 2 | 82 | 19 |
| Driver onboarding & docs | 11 | 64 | 35 | 0 |
| Driver status / earnings | 0 | 130 | 63 | 5 |
| Safety / SOS | 17 | 22 | 14 | 0 |
| Corporate / company | 6 | 0 | 61 | 5 |
| Account / privacy / settings | 45 | 74 | 43 | 1 |
| Other / cross-cutting | 51 | 99 | 251 | 23 |

---

## 6. Suggested fix order

1. **F1 bare tokens** — `forbidden`, `payment_already_processing`, `company_not_found`,
   `data_export_request_failed`. Highest embarrassment, lowest effort, zero blast radius.
2. **F2 ride-state names** — a driver reading *"Ride is not in driver_accepted state"* mid-shift is the
   worst live-testing artifact in the set.
3. **F6 `ERR_*` mapping** — add one sentinel→sentence map in `shared/errors/` so 17 precise reasons stop
   collapsing into "Something went wrong".
4. **F7 the 5xx phrase** — one string change in `http_exception_handler`, covers 318 raise sites at once.
5. **F3/F4 field names and dev instructions** — mostly validation paths; rewrite as the user's own
   vocabulary ("Choose weekly or monthly", not "period_type must be weekly or monthly").

None of these are behaviour changes — they are wording changes to strings the apps already render.

---

## 7. What was NOT verified

- **Static analysis only.** Nothing was triggered in a running app; no screenshots were taken. Whether a
  given message is reachable in practice was inferred from the route, not observed.
- **Surface attribution is heuristic.** The `surface` column is derived from the route path, not from
  tracing real callers — e.g. `routes/rides/lifecycle.py` serves the driver's start-ride call but is
  labelled RIDER APP. Confirm the owning app during the manual pass before rewording.
- **Push/SMS/email copy is out of scope.** This audit covers in-app toasts, alerts, inline errors and
  backend API messages. FCM payloads, Twilio SMS bodies and email templates were not swept.
- **Static screen copy is out of scope** — labels, headings, empty states and button text rendered
  directly in JSX were not catalogued, only messages raised through a toast/alert/error channel.
- **Non-English locales unchecked.** Only `en`/`en-CA` keys were verified as present; `fr`, `fr-CA`,
  `es`, `zh` were not audited for completeness or tone.
- **No visual regression tooling exists for rider-app or driver-app**, so any wording change made off the
  back of this audit has to be eyeballed on a device — per CLAUDE.md release gate 6.
- **Admin-dashboard rows are catalogued but not judged** against a rider/driver readability bar; internal
  staff are a different audience and `e?.message` pass-through there may be intentional.
