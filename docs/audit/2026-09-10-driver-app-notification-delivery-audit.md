# Driver-App Push Notification Delivery Audit

**Date:** 2026-09-10
**Trigger:** User report — "notifications are not visible in the driver app," described as a
long-standing problem.
**Scope:** Full pipeline, backend send → client receive/display. Two parallel research passes
(one per side), each reading real code and citing file:line evidence. No code changed by this
audit; see "Recommendations" for what should be fixed and by whom.
**Tracked as:** `ACTION_ITEMS.md` C97.

## Correction (2026-09-10, same day, before any fix shipped for finding #9)

Follow-up direct reading of `backend/features.py::_deliver_push_now` (not part of either
original research pass, done while scoping the client-side fix) narrows finding #9 below
significantly. **Only `new_ride_assignment` and `live_activity` are sent as data-only FCM
messages** (`is_data_only = is_dispatch or is_live_activity`, `features.py:1310-1315`) — every
other type (`ride_cancelled`, `subscription_expiring`, `document_expiry_warning`,
`auto_offline`, and any other generic driver notification) carries a real
`messaging.Notification(title=title, body=body)` block, targeting a channel
(`android_channel = "ride-offers"` for driver, `features.py:1320`) that **is confirmed to exist**
on-device — created reliably at cold start via `expo-notifications`' own
`Notifications.setNotificationChannelAsync('ride-offers', {...})` (`driver-app/app/_layout.tsx:449`,
plus a `'default'` channel at `:460`), independent of the Notifee channels used for the
data-only dispatch path.

**Practical effect:** for background/killed app state, Android's own FCM SDK auto-displays a
message that carries a real `notification` block, with no app JS code needing to run at all —
so finding #9's "background handler only branches on 3 types, else returns" is true of the
*custom* handling that function does (offer persistence, in-process event bridging), but does
**not** mean these notifications are invisible in background/killed state, since the OS handles
display for them independently of that function. **This significantly weakens finding #9 as an
explanation for "notifications not visible" in background/killed state specifically.**

Finding #7 (foreground) is **not** weakened by this — Android does not auto-display a
`notification`-block FCM message while the app is in the foreground by default; that part of the
original finding holds. What's now clear is more precise than the original framing: for the 4
types the foreground handler already special-cases (`new_ride_assignment`, `auto_offline`,
`ride_cancelled`, `subscription_expiring`, `document_expiry_warning`), the app shows an in-app
Alert/Toast — a real, if different-in-kind, form of visibility while the driver is actively in
the app. The genuine, narrower gap is: **a message type not in that explicit list falls through
to a silent `router.push` with no toast/alert at all** — a driver not already looking at the
right screen would not notice anything happened. This is the gap the driver-app fix in this
audit's follow-up PR actually closes; the broader "everything but ride offers is silently
dropped, foreground and background alike" framing in the original findings below overstated the
background/killed portion specifically. Left the original finding tables below unedited (rather
than rewritten) so the correction is visible against what was originally claimed, per this
project's own convention of correcting transparently rather than silently.

## Executive summary

There is **not one root cause** — the two research passes independently confirmed **two
separate, compounding gaps**, one on each side of the pipeline. Either alone could produce the
symptom; together they make it worse and harder to diagnose from logs alone.

1. **Backend (infra-shaped): Firebase Admin SDK can silently fail to initialize.**
   If `FIREBASE_SERVICE_ACCOUNT_JSON` is unset or the SDK falls through to Application Default
   Credentials on a non-GCP host (Fly.io/Railway, both non-GCP), the fallback path is wrapped in
   a bare `except Exception: pass` — zero log output, no startup failure, no metric. The backend
   boots looking completely healthy. The SDK only visibly fails later, per-push, at the first
   real send attempt — and even then, nothing increments a metric or fires an alert; it's a
   scattered log line. **This would silently break every push notification type, including ride
   offers**, on whichever host has the gap.

2. **Client (code-structural): every FCM message type except three has no display code path on
   Android.** `@react-native-firebase/messaging`'s manifest priority means it exclusively
   receives FCM messages on Android — `expo-notifications`' handler is effectively dead code for
   FCM-originated pushes. The app's actual foreground listener only branches on
   `new_ride_assignment`; the background/killed handler only branches on
   `new_ride_assignment` / `ride_cancelled` / `location_health`. Every other data-only
   notification type (chat, document/license expiry reminders, generic driver alerts, promos)
   is silently dropped — no Notifee call, nothing rendered, no error anywhere. **Ride-offer
   notifications specifically are correctly built and displayed end-to-end** (bespoke, well-
   engineered path on both sides) — this gap affects every *other* type.

**The practical implication:** "notifications not visible" means something different depending
on which notifications the user means. If ride offers themselves are invisible, that points at
cause #1 (infra) — the client-side ride-offer path is confirmed correct, so an invisible ride
offer has to be a send-side failure. If ride offers arrive fine but other things (chat,
reminders, alerts) never show, that's cause #2 (client) on its own, no infra involvement needed.
**Both can be true simultaneously** and would look identical from a driver's perspective
("I don't get notifications") while needing completely different fixes.

## Part 1 — Strategy & architecture review

### What exists today

- **Transport:** Firebase Cloud Messaging (FCM) via the Firebase Admin SDK server-side, and
  `@react-native-firebase/messaging` client-side. `expo-notifications` is also present in the
  dependency tree but — per finding below — is not actually in the live delivery path on
  Android for FCM-originated messages, because of a manifest-priority conflict between the two
  libraries' bundled services.
- **Server-side send:** one central `send_push_notification(user_id, ...)` function
  (`backend/features.py:1500-1673`) that all ~15+ call sites across `routes/` and `utils/` funnel
  through. Token is looked up from `users` by `id`, keyed by `target_app`
  (`fcm_token_driver`/`fcm_token_rider`/`fcm_token`). Priority levels (`normal`, `dispatch`,
  `safety`, `account`) determine whether a failed send is dropped or queued for retry.
- **Retry:** a dedicated `push_retry_queue` table drained by `push_retry_loop`
  (`backend/utils/push_retry.py`) every 30s with exponential backoff, capped at 5 attempts. Only
  reached for priorities the inline send path treats as retry-eligible.
- **Client display:** a dedicated `notifeeService.ts` (the `notifee` library) for rich,
  heads-up-capable Android notifications, wired specifically for ride offers with a dedicated
  high-importance channel, DND bypass, and sound — clearly built with real engineering care for
  the one notification type judged safety/earnings-critical.
- **Client-side history/UI:** a separate in-app "notifications" list screen
  (`app/driver/notifications.tsx`) — a UI history view, distinct from (and not a substitute for)
  the OS-level push notification actually appearing.

### Where the strategy is sound

- Centralizing all server sends through one function with per-priority retry behavior is the
  right shape — it's *why* the N3 bug class (wrong ID passed in) was fixable in three places at
  once back on 2026-08-11, and why this audit's re-check of every current call site found no new
  instance of it.
- The ride-offer path getting bespoke, heavily-tested treatment (custom channel, DND bypass,
  fallback-to-minimal-notification-on-render-failure, background handler wired at module scope
  before the app's React tree even mounts) is the correct prioritization — it is this app's
  single most safety/earnings-critical notification, and it shows in the code quality.
- Loud failure logging exists at the layer that matters most (`_deliver_push_now`,
  `features.py:1284-1406`, full traceback via `logger.opt(exception=True).error(...)`) — this
  repo's own "do not silently swallow errors" rule is actually followed *there*.

### Where the strategy has gaps

- **No delivery-outcome metric.** `spinr_dispatch_offer_sent_total` (the one push-adjacent
  metric that exists) is incremented when a ride offer is *claimed*, not when the push actually
  sends or is confirmed delivered. There is no metric anywhere in the pipeline that distinguishes
  "push attempted," "push sent successfully to FCM," "push failed and was queued for retry," or
  "push exhausted retries and was dropped." For a fleet-wide notification system, this is the
  single biggest structural gap: a systemic failure (like Firebase Admin SDK never
  initializing) produces *zero* dashboard signal — only individual log lines that require someone
  to be actively grepping logs to notice, which is a very plausible way for this to go unnoticed
  for a long time.
- **No startup health check for Firebase Admin SDK.** The app boots successfully whether or not
  push notifications will ever work. A `/health`-adjacent check (or at minimum a loud,
  impossible-to-miss startup log line) confirming the SDK actually initialized against a real
  credential would have caught this on day one of whichever deploy introduced the gap, instead of
  it surfacing only as "drivers say they don't get notifications" months later.
- **Two competing push-handling libraries in the same app**, with an undocumented reliance on
  Android manifest priority to decide which one wins. This isn't inherently wrong (RNFirebase
  needs to own delivery for background/headless correctness), but the `expo-notifications`
  `setNotificationHandler` code being live-looking but actually dead for FCM messages is exactly
  the kind of thing that misleads whoever next touches this file into thinking a fix belongs
  there when it doesn't.
- **Client-side message-type handling is an allowlist that silently drops everything else**,
  rather than a router with an explicit fallback. A new backend-side notification type added
  without a matching client-side branch fails closed (silently invisible) rather than failing
  open (a generic fallback notification at minimum) — the opposite of what you'd want for a
  system where "the driver didn't get told" has real consequences (missed payout-failure
  alerts, missed document-expiry warnings that lead to being blocked from going online).

## Part 2 — Delivery mechanism, finding by finding

### Backend (send side) — confirmed by direct code reading

| # | Finding | File:line | Severity |
|---|---|---|---|
| 1 | Firebase Admin SDK init failure on the ADC fallback path is completely silent (`except Exception: pass`) | `backend/core/security.py:12-29` | **HIGH** — most plausible single root cause |
| 2 | No delivery-outcome metric anywhere in the push pipeline; `spinr_dispatch_offer_sent_total` measures offer claims, not push success | `backend/routes/rides/matching.py:1270` vs. `:1462-1478` | **HIGH** — explains why a systemic failure goes unnoticed |
| 3 | `push_retry_loop` doesn't check whether a ride/offer is still live before resending — a failed dispatch push can arrive 60-240s late, well past the ~15s offer window | `backend/utils/push_retry.py:133-208` | **MEDIUM** — produces stale/useless notifications, not literal invisibility |
| 4 | `spawn()`'d dispatch-push task has no attached exception callback; an in-task exception is invisible to the caller's `try/except` | `backend/utils/background.py:57-81`, `routes/rides/matching.py:1426-1478` | **MEDIUM** — masks failures further, doesn't cause them alone |
| 5 | FCM token cleanup is purely reactive (only on a `NotFoundError` from an actual send); a token that silently goes bad without a send attempt (idle driver) stays on file indefinitely | `backend/features.py:1387-1403`, `routes/notifications.py:261-339` | LOW-MEDIUM |
| 6 | Re-audit of every driver-facing `send_push_notification` call site for the 2026-08-11 ID-mismatch bug class (N3) found no new instance — all resolve to `users.id`, correctly | multiple, see Part 3 | Confirms N3's fix held; not a new finding |

### Client (receive/display side) — confirmed by direct code reading

| # | Finding | File:line | Severity |
|---|---|---|---|
| 7 | Foreground FCM listener only handles `new_ride_assignment`; every other type is silently dropped in foreground | `driver-app/hooks/useDriverDashboard.ts:1812-1838` | **HIGH** |
| 8 | `@react-native-firebase/messaging`'s Android manifest service has default (higher) priority over `expo-notifications`' service for the same FCM intent-filter — RNFirebase exclusively receives all FCM messages; `expo-notifications`' handler is dead code for these | `node_modules/@react-native-firebase/messaging/android/.../AndroidManifest.xml` vs. `node_modules/expo-notifications/android/.../AndroidManifest.xml` (`priority="-1"`) | **MEDIUM-HIGH** — structural, widens #7 |
| 9 | Background/killed-state handler only branches on 3 types (`new_ride_assignment`, `ride_cancelled`, `location_health`); other data-only messages return with no display | `driver-app/services/backgroundMessaging.ts:159-259` | **HIGH** for those types |
| 10 | iOS `Info.plist`/`app.config.ts` has no `UIBackgroundModes: ['remote-notification']`; RNFirebase's Expo config plugin only wires an Android step | `driver-app/app.config.ts:66-89`; `node_modules/@react-native-firebase/messaging/plugin/build/index.js:8-14` | MEDIUM, **not fully traced** (no compiled `ios/` build present to confirm) — flagged SUSPECTED |
| 11 | Ride-offer path specifically (channel importance, DND bypass, foreground listener branch, background handler branch, error handling with fallback) is correctly built end-to-end | `driver-app/services/notifeeService.ts:120-132,328-380`; `useDriverDashboard.ts:1812-1838`; `backgroundMessaging.ts:189-259` | Confirms this is NOT where the complaint originates, if it's about ride offers |
| 12 | FCM token registration/refresh (permission request incl. Android 13+ `POST_NOTIFICATIONS`, re-registration on token rotation and re-login) is correctly implemented | `driver-app/app/_layout.tsx:508-569`; `shared/services/firebase.ts:293-301` | Confirms this is NOT the cause |

## Part 3 — What was NOT found (ruled out)

- **No recurrence of the N3 ID-mismatch bug** (`drivers.id` passed where `users.id` is required)
  at any currently-audited driver-facing call site.
- **No silent-catch swallowing the actual display call** — `displayRideOfferNotification` logs
  loudly and falls back to a minimal notification on failure; the few empty catches found
  (`dismissRideOfferNotification`, token-refresh calls) are justified/unrelated to whether a
  notification renders.
- **No recent regression** — `git log` on the relevant client files shows no change in the
  window that would explain a new onset; this reads as a long-standing structural gap, consistent
  with the user's own framing ("pending for a very long time"), not a recent break.
- **Android notification channel configuration is correct** for the one type that has a channel
  — importance, DND bypass, sound all set correctly for ride offers. (A past bug in this exact
  area — a channel pointing at a never-bundled sound resource — was already found and fixed
  before this audit, per the code's own comment trail; not a current issue.)

## Recommendations

Ordered by leverage (biggest signal for smallest change first), not by file count:

1. **(Ops, not code — needs you, do this first) Confirm `FIREBASE_SERVICE_ACCOUNT_JSON` is
   actually set and valid on both Fly (primary) and Railway (standby).** Given the two hosts can
   drift independently (per `CLAUDE.md`'s own documented Railway-drift status), this needs a
   direct check — neither research pass nor this session has deploy/secrets access to verify it.
   This is the fastest way to confirm or rule out Root Cause #1 as the actual live problem, and it
   costs nothing to check before any code changes.
2. **(Backend, cheap, highest-leverage code fix) Make Firebase Admin SDK init failure loud.**
   Replace the bare `except Exception: pass` fallback in `core/security.py` with a loud
   `logger.error(...)` (full exception) at minimum, and ideally a hard startup failure in
   production the same way this repo already fails fast on a weak `JWT_SECRET`
   (per `CLAUDE.md`'s documented pattern). This alone would have surfaced the problem the day it
   was introduced instead of leaving it to a user report.
3. **(Backend) Add a real delivery-outcome metric.**
   `spinr_push_send_total{outcome=sent|failed|retried|dropped,priority=...}` at the point
   `_deliver_push_now` actually resolves, not at offer-claim time. This is the change that turns
   "someone has to notice a log line" into "an on-call alert exists" — matches this repo's own
   stated Sentry/metrics conventions (`domain=dispatch`/`admin`, already-established pattern) and
   is the natural next step now that the observability gap is named.
4. **(Driver-app, real code fix, well-scoped) Add a router with an explicit fallback for
   unhandled FCM message types**, in both `useDriverDashboard.ts`'s foreground listener and
   `backgroundMessaging.ts`'s background handler — at minimum a generic Notifee notification
   (title/body from the payload, default channel) for any `type` not in the existing allowlist,
   instead of silent drop. This directly closes findings #7 and #9 and is the fix for "chat/
   reminder/alert notifications never show" as distinct from the ride-offer path (which stays
   untouched — it already works).
5. **(Driver-app, investigate then decide) Confirm the iOS `UIBackgroundModes` gap** (finding
   #10) against an actual EAS/native build — this session has no compiled iOS artifact to check,
   and the finding is flagged SUSPECTED, not confirmed, for a reason.
6. **(Driver-app, lower priority, cleanup) Either wire `expo-notifications`' handler into the
   real delivery path or remove it** — right now it's misleading dead code for anyone who reads
   `_layout.tsx:219-243` and assumes it's live.

## What was NOT verified

- **Live production state** — whether `FIREBASE_SERVICE_ACCOUNT_JSON` is actually set/valid on
  Fly and/or Railway today. Needs ops access this session doesn't have.
- **Whether Sentry is actually receiving the `_deliver_push_now` error-level logs** via the
  loguru→Sentry bridge, and whether anyone has an alert configured on them — the bridge exists
  per `CLAUDE.md`, but whether it's wired for this specific log site, and whether an alert rule
  exists, was not checked.
- **The iOS `UIBackgroundModes` finding** — flagged SUSPECTED, needs a real build to confirm.
- **Whether the user's specific complaint is about ride offers or other notification types** —
  this determines which of the two root causes (or both) is actually in play; see Executive
  Summary. Not resolvable from code alone.
- **No code was changed by this audit.** Recommendations are ready to implement but were not
  applied, pending the user's priority call given the fork above.
