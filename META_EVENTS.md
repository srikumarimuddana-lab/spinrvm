# Meta Conversion Events — as built

Contract between the Spinr codebase and Meta Events Manager. Event names and
property names below are **exact strings**. Events Manager matches on them
literally, so a custom conversion built against a name that differs by a
character silently reports zero — treat this file as a published API.

- **Currency:** always `CAD`
- **Timezone context:** America/Regina
- **Business ID:** `1941274209751172` · **Ad account:** `4134572946809004`
- **Web pixel `793865613741737` is NOT used here.** That dataset is for
  spinr.ca website traffic. App events go to each app's own app dataset.

---

## 1. Event dictionary

### Rider app + backend → **rider app dataset**

| When | Event | Type | Fired by | Properties |
|---|---|---|---|---|
| Rider account created | `CompleteRegistration` | standard | **app + CAPI** (deduped) | `registration_method`, `content_category: "rider"`, `status: true` |
| Every completed **paid** ride | `Purchase` | standard | **CAPI only** | `value`, `currency: "CAD"`, `promo_code`, `ride_id`, `content_category: "ride"` |
| Rider's **first** completed paid ride | `FirstRide` | **custom** | **CAPI only** | `value`, `currency: "CAD"`, `promo_code` |

### Driver app + backend → **driver app dataset**

| When | Event | Type | Fired by | Properties |
|---|---|---|---|---|
| Driver account created | `CompleteRegistration` | standard | **app + CAPI** (deduped) | `registration_method`, `content_category: "driver_application"`, `status: true` |
| Documents approved by admin | `DriverApproved` | **custom** | **CAPI only** | `driver_id` |
| Driver completes first trip | `DriverActivated` | **custom** | **CAPI only** | `driver_id`, `value`, `currency: "CAD"` |

The two funnels are separated at the **account level** — each app has its own
Meta App and its own dataset. `content_category` on `CompleteRegistration` is a
second safeguard so reporting can never confuse a rider signup with a driver
application even when looking at a single dataset.

---

## 2. De-duplication

Meta collapses two events into one conversion when **`event_name` and
`event_id` both match**. Only `CompleteRegistration` fires on both sides, so it
is the only event that needs this.

**`CompleteRegistration` — the backend mints the `event_id`, the client receives it.**

**`Purchase` / `FirstRide` — the `event_id` is deterministic**, derived from the
ride id (resp. user id) via UUIDv5. Money can settle through
`process_payment`, the pre-auth capture sweep, the payment-retry loop, or
guest-corporate auto-settle, and a ride can legitimately be reached by more
than one of them. Because every path computes the *same* id for the same ride,
Meta collapses a second emission instead of double-counting revenue — no
cross-path locking required. Never change `_EVENT_NS` in
`meta_conversions_service`: it would re-issue new ids for rides already
reported and let Meta count them twice.

```
POST /auth/verify-otp
      │
      ├─ backend creates the user row  (the real "registration succeeded" moment)
      ├─ backend mints event_id = uuid4()
      ├─ backend sends CompleteRegistration via CAPI  ── event_id ──┐
      └─ backend returns { is_new_user: true,                       │  same id
                           meta_event_id: "<uuid>" }                │
                    │                                               │
                    └─ app fires SDK CompleteRegistration ──────────┘
```

Rules that make this hold:

- The client fires **only** when `is_new_user === true` **and**
  `meta_event_id` is present. No id ⇒ the client fires nothing, so the server
  copy is counted once rather than risking a double count.
- The server copy is **not** conditional on the client. An offline device, an
  old build, or a missing native module still produces exactly one conversion.
- `Purchase` / `FirstRide` / `DriverApproved` / `DriverActivated` have **no
  client counterpart at all**, so no shared id is needed.

### Why `Purchase` is server-only

The authoritative "money moved" moment is not visible to the device: settlement
has three payment methods, a background retry sweep, and a guest-corporate
auto-settle path that never touches a phone. A client-fired `Purchase` would
either double-count against the server copy or miss whole classes of ride.

---

## 3. Property semantics — read before building a custom conversion

**`value`** — the amount the rider was **actually charged**: post-promo,
including tip. It is taken from the settlement result, not from a fare column,
so it cannot drift from the real charge. Note that tip is included because it
is part of what the rider paid; if you want ex-tip revenue, that is a different
number and is not currently exported.

**`promo_code`** — present on **every** `Purchase` and `FirstRide`, as an empty
string `""` when no promo was used. This matters: an Events Manager filter
cannot match events that omit the key, so the property is never dropped.

> **Building the SPINR75 custom conversion:** source = rider app dataset, event
> = `Purchase`, filter `promo_code` **equals** `SPINR75`. Pair it with
> `FirstRide` filtered the same way to get promotional cost per *newly
> acquired* rider rather than per discounted ride.

A **$0 ride still fires** `Purchase` with `value: 0`. A 100%-subsidised ride is
the most expensive acquisition there is — suppressing it would hide exactly
what `promo_code` exists to measure.

**`registration_method`** — currently always `"phone"` (Spinr signup is
OTP-only). The property exists so `google` / `apple` can be added without an
Events Manager change.

**`ride_id` / `driver_id`** — internal Spinr UUIDs, for reconciliation only.
They are not PII and not user identifiers.

---

## 3a. Consent gate (PIPEDA) — read this before interpreting match quality

Identity is attached **only** for users who have opted in to advertising
attribution: `marketing_preferences.ad_attribution_opt_in`.

- **Opted in** → full Advanced Matching (hashed `em` / `ph` / `external_id`,
  plus client IP and user agent).
- **Not opted in** → the conversion is still sent, so campaign counts and
  optimisation keep working, but `user_data` is **empty**. No hashed contact
  details, and no IP or user agent either — both are personal information and
  neither may be exported for a purpose the user has not accepted.
- **Fails closed.** No row, no opt-in, or a DB error all count as no consent.
  Silence is never consent (the stance migration 190 already takes), so
  enabling the access token cannot retroactively export the identity of an
  existing user who never accepted this purpose.

**Consequence for reporting:** Event Match Quality will track opt-in rate, not
just field coverage. A low EMQ with healthy event volume means low consent
uptake, not a broken integration — check the opt-in rate before debugging the
hashing.

## 4. Advanced Matching — server-side only

Hashed identity is attached **on the backend**, never on the device.

| Field | Source | Normalization before SHA-256 |
|---|---|---|
| `em` | `users.email` | lowercase, trim |
| `ph` | `users.phone` | E.164, digits only (no `+`) |
| `external_id` | `users.id` | lowercase, trim — **always hashed** |

- Everything is SHA-256 hashed in `backend/utils/meta_capi.py` before it leaves
  the process. Raw email/phone never reaches Meta.
- Missing values are **omitted**, never sent blank — Meta reads a
  present-but-empty field as a failed match, which lowers Event Match Quality
  rather than leaving it neutral.
- Emails are **not** dot-stripped or plus-tag-stripped. Meta hashes what the
  user typed on their side too, so canonicalising harder produces a digest that
  matches nothing.
- `fbp` / `fbc` pass through **unhashed** — they are opaque Meta-issued tokens
  and hashing makes them unmatchable. Not currently forwarded by either app;
  wiring them is a future match-rate improvement.

### Why this is not on the device

Doing Advanced Matching in the mobile SDK would mean IDFA access and App
Tracking Transparency on iOS, which would make both apps'
`NSPrivacyTracking: false` declaration untrue and change the App Store privacy
label. Server-side matching gets the Event Match Quality lift with **no ATT
prompt and no store-submission change**.

**Do not** add `Settings.setUserData(...)` or enable
`setAdvertiserTrackingEnabled` without also flipping `NSPrivacyTracking` to
`true`, adding `NSUserTrackingUsageDescription` and an ATT prompt, and
re-answering the App Store Connect privacy questionnaire.

---

## 5. Configuration

Nothing below is committed. All values are pasted into running systems.

### App (client-side identifiers — ship inside the binary by design)

EAS environment variables, per app:

| Key | Notes |
|---|---|
| `EXPO_PUBLIC_FB_APP_ID` | **Different value per app.** Rider and driver each have their own Meta App. |
| `EXPO_PUBLIC_FB_CLIENT_TOKEN` | Per app. |

### Backend (secret — `app_settings` DB row, editable in the admin dashboard)

| Key | Notes |
|---|---|
| `meta_rider_dataset_id` | Rider app dataset. Not the web pixel. |
| `meta_driver_dataset_id` | Driver app dataset. |
| `meta_capi_access_token` | **Secret.** Masked on admin GET. |
| `meta_test_event_code` | Non-empty ⇒ events go to Test Events and **do not count as live conversions**. Clear it after verifying. |

These live in `app_settings` rather than `.env` so the token can be rotated in
Events Manager without a backend redeploy — the same pattern as the Stripe,
Twilio, and Maps credentials.

**Empty token = tracking disabled.** That is the default and it is fully
supported: no events are sent, and — importantly — the once-per-user
`FirstRide` gate and the once-per-driver dedup keys are **not** consumed, so
nothing is silently burned before the credentials land.

---

## 6. Verifying

1. Set `meta_test_event_code` in admin → Settings to the code from Events
   Manager → Test Events.
2. Trigger each event; confirm arrival in the Test Events tab.
3. **Clear `meta_test_event_code`** — while it is set, live conversions do not
   count.
4. For `CompleteRegistration`, confirm Events Manager shows the app and server
   copies as **deduplicated**, not as two conversions. If it shows two, the
   app-event de-duplication parameter name is the first thing to check —
   see `shared/analytics/meta.ts`.

---

## 7. Code map

| Concern | File |
|---|---|
| Hashing, HTTP transport, retry | `backend/utils/meta_capi.py` |
| Event construction, dedup gates | `backend/services/meta_conversions_service.py` |
| `CompleteRegistration` + `event_id` | `backend/routes/auth.py` |
| `Purchase` / `FirstRide` | `backend/routes/rides/payments.py`, `backend/services/payment_service.py`, `backend/utils/preauth_capture.py`, `backend/utils/payment_retry.py` |
| `DriverApproved` | `backend/routes/admin/drivers.py` |
| `DriverActivated` | `backend/routes/drivers/ride_complete.py` |
| Client SDK wrapper | `shared/analytics/meta.ts` |
| SDK init | `rider-app/app/_layout.tsx`, `driver-app/app/_layout.tsx` |
| Client `CompleteRegistration` | `rider-app/app/otp.tsx`, `driver-app/app/otp.tsx` |
| Consent gate | `meta_conversions_service.has_ad_attribution_consent` |
| Schema | `backend/migrations/266_meta_conversions_tracking.sql` |

---

## 7a. Recorded deviation — third-party ad SDK

`CLAUDE.md` / `AGENTS.md` ("What Spinr Is NOT") state: *"Never add third-party
ad SDKs or behavioral retargeting."* Adding `react-native-fbsdk-next` to both
apps is a deliberate, owner-approved exception to that guardrail, taken on
2026-07-28 after the conflict was raised explicitly.

Scope of the exception, so it does not widen by drift:

- The SDK is used for **app lifecycle + install/activation attribution** and
  the client half of `CompleteRegistration`. Nothing else.
- Advertiser tracking and IDFA collection are **pinned off**
  (`setAdvertiserTrackingEnabled(false)`, `advertiserIDCollectionEnabled: false`).
- **No user data is attached on-device** — no `Settings.setUserData`, no
  Advanced Matching in the app.
- No behavioural retargeting, no ad-profile analytics, no other ad SDK.

Anything beyond this list is a fresh decision, not covered by this exception.

## 8. Known limitations

- **`FirstRide` is lost if its send fails.** The gate
  (`users.first_ride_completed_at`) is claimed before the send and never
  released, so a Meta outage at that exact moment costs that one user's
  `FirstRide`. Releasing it on failure was rejected: the column is business
  truth ("this rider has completed a paid ride"), and redefining it as "Meta
  acknowledged our event" would mislead every future reader. `Purchase` still
  fires for that ride, so only the first-ride segmentation is lost.
- **Riders who completed rides before migration 266** start with a NULL flag
  and will fire `FirstRide` on their next paid ride. Backfilling from ride
  history was rejected — it would emit a burst of historical conversions
  stamped with today's timestamps, corrupting attribution windows.
- **No retry sweep for failed backend-only events.** `meta_capi_deliveries` rows
  are left at `status = 'failed'` with their `event_id` preserved, so a sweep
  can be added later and will de-duplicate correctly. There is no loop today.
- **Driver `CompleteRegistration` fires at account creation**, not at vehicle/
  document submission. `POST /drivers/register` is an upsert that re-fires on
  every resubmission, so it has no natural once-per-driver moment; account
  creation does. If the analyst needs "application submitted" as a distinct
  funnel step, it needs its own event name and its own idempotency key.
- **`fbp` / `fbc` are not forwarded** from either app. Supported by the
  backend, not yet populated by the clients.
