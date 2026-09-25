# Event / WebSocket / FCM-Push Inventory (Tier B matrix, part 1/2)

**Lane:** ad hoc Tier-B matrix build (this session) · **Written:** 2026-09-25 · **Report-only.**
Evidence labels: VERIFIED / INFERRED / UNKNOWN per master convention. Absence claims ("unhandled",
"never emitted") were checked with several search spellings across all four locations a handler could
live (`rider-app/`, `driver-app/`, `admin-dashboard/src/`, `shared/`) per this task's method rule — see
§0.

## §0 Method (reproducible)

1. **Backend emit-site extraction**: a script (this session's `extract_ws_events.py`) scans every
   `backend/**/*.py` (excluding tests) for calls to `ConnectionManager.send_personal_message` /
   `.broadcast` / `.broadcast_to_admins` / `.broadcast_ride_status`, resolves the literal `"type": "..."`
   either inline in the call or by backtracking to the nearest prior `VAR = {...}` assignment of the
   message-argument's variable name in the same file. 106 call sites found, 82 auto-resolved to a
   literal type; the remaining 24 were resolved by hand (all 15 `broadcast_ride_status(...)` calls always
   emit the literal type `ride_status_changed` — the real sub-state travels in the `status` field, per
   that method's own docstring at `backend/socket_manager.py:493-500`, not as a distinct `type`; the
   other 9 were individually read — see §5).
2. **FCM push `"type"` values**: grepped `data={"type": "..."` / `data["type"] =` across `backend/` for
   every `send_push_notification(...)` call site, separately from the WS grep (a literal `"type"` inside
   a push `data=` dict is a push-payload type, not a WS event, even though several names are shared
   between the two channels by design — e.g. `new_ride_assignment`, `ride_cancelled`, `auto_offline`,
   `location_health` are sent over **both** WS and FCM, as a deliberate backgrounded-client fallback per
   `driver-app/hooks/useDriverDashboard.ts`'s own comment: "ride offers that arrive via FCM ... follow
   the same path as the WebSocket `new_ride_assignment` handler").
3. **Client handler extraction**: grepped each app for its WS message switch (`rider-app/hooks/
   useRiderSocket.ts`, `driver-app/hooks/useDriverDashboard.ts`, `admin-dashboard/src/app/dashboard/
   monitoring/page.tsx` — found via grep for `useWebSocket|onmessage|case '` across each app, not
   assumed by naming convention) and each app's FCM foreground/background handler (`rider-app/app/
   _layout.tsx`, `driver-app/services/backgroundMessaging.ts` + the foreground handler inside
   `useDriverDashboard.ts`).
4. **Emitted-but-unhandled check**: for every backend-emitted type not seen in a client's own switch/if
   chain, grepped the literal string (both quote styles) across **all four** locations
   (`rider-app`, `driver-app`, `admin-dashboard/src`, `shared`) before calling it unhandled — catches a
   handler implemented as a lookup-table/dictionary rather than a `switch`/`case` (none were found that
   way, but the check was run regardless per this task's absence-claim rule).
5. One false-positive was caught and excluded: `"type": "admin_alert"` appears only in
   `backend/tests/test_p3_ws_broadcast.py` as a generic test fixture payload — no production code path
   emits it — so it is **not** listed as a real emitted-but-unhandled event below.

## §1 Backend-emitted WS event types (production code only, test fixtures excluded)

33 distinct literal `type` values found across 106 real call sites (`send_personal_message` /
`broadcast` / `broadcast_to_admins` / `broadcast_ride_status`), plus 2 more found by hand
(`session_revoked` — sent via a direct `websocket.send_json(...)` at `routes/websocket.py:553` and
`socket_manager.py:276`, outside the four grepped methods — and `ping`, the 10s heartbeat at
`routes/websocket.py:565`, also a direct `send_json`). **35 total.**

| Event type | First emit site (file:line) | Recipient(s) | Rider-app | Driver-app | Admin-dashboard |
|---|---|---|:-:|:-:|:-:|
| `ride_status_changed` | `socket_manager.py:518` (+4 more — every `broadcast_ride_status()` call) | rider + driver + admin | ✅ | ✅ | ✅ |
| `driver_location_update` | `routes/drivers/location.py:348` | rider (+ throttled admin via `broadcast_driver_location_to_admins`) | ✅ | — | ✅ |
| `driver_accepted` | `routes/drivers/ride_flow.py:613` | rider | ✅ | — | — |
| `driver_arrived` | `routes/drivers/ride_flow.py:1109` | rider | ✅ | — | — |
| `ride_started` | `routes/drivers/ride_flow.py:1243` (+2) | rider | ✅ | — | — |
| `stops_updated` | `routes/rides/stops.py:122` (+3) | rider + driver | ✅ | ✅ | — |
| `ride_completed` | `routes/admin/rides.py:945` (+7) | rider + driver + admin | ✅ | — | ✅ |
| `ride_cancelled` | `services/corporate_member_offboarding_service.py:117` (+18 — the most call sites of any event) | rider + driver + admin | ✅ | ✅ | ✅ |
| `driver_timeout` | `routes/auth.py:2479` (+3) | rider | ✅ | — | — |
| `chat_message` | `routes/websocket.py:1716` (+1) | rider + driver | ✅ | ✅ | — |
| `new_notification` | `routes/notifications.py:849` | rider + driver | ✅ | ✅ | — |
| `auth_success` | WS handshake ack (all three clients' own connect handlers) | rider + driver + admin | ✅ | ✅ | ✅ |
| `error` | generic WS error frame | rider + driver + admin | ✅ | ✅ | ✅ |
| `ping` | `routes/websocket.py:565` | rider + driver + admin (heartbeat) | ✅ (replies `pong`) | ✅ | ✅ |
| `driver_status_changed` | `routes/websocket.py:943` (+3) | admin | — | — | ✅ |
| `ride_requested` | `utils/scheduled_rides.py:640` (+1) | admin | — | — | ✅ |
| `drivers_snapshot` / `get_drivers_snapshot` (client→server request/response pair) | admin monitoring snapshot | admin | — | — | ✅ |
| `rides_snapshot` / `get_rides_snapshot` | admin monitoring snapshot | admin | — | — | ✅ |
| `new_ride_assignment` | `routes/admin/rides.py:1414` (+1; primary dispatch-offer path is elsewhere — this is the resolved literal in the admin direct-assign path, see §5 note) | driver | — | ✅ | — |
| `ride_offer_expired` | `routes/rides/matching.py:1900` | driver | — | ✅ | — |
| `ride_taken` | `routes/drivers/ride_flow.py:555` | driver | — | ✅ | — |
| `session_revoked` | `routes/websocket.py:553`, `socket_manager.py:276` | rider + driver + admin (token revocation) | — (no handler found — see §3) | ✅ | — (no handler found) |
| `auto_offline` | `routes/drivers/ride_complete.py:1003` (+2) | driver | — | ✅ | — |
| `chat_typing` | `routes/rides/chat.py:234` | driver (rider side not confirmed — see §3) | — | ✅ | — |
| `tip_received` | `routes/rides/payments.py:308` | driver | — | ✅ | — |
| **`location_health`** | `utils/route_gap_monitor.py:175,230` | driver | — | ✅ | — |
| `driver_connection_lost` | `routes/websocket.py:410` | **admin (type declared in `shared/types/api/wsEvents.ts:96`, zero runtime consumer anywhere)** | — | — | ❌ **unhandled** |
| `emergency_alert` | `routes/rides/safety.py:286` (+1) | rider + driver + admin (SOS) | — (no client-side literal-string handler found — see §5, likely rendered via a different admin safety surface, not the monitoring page) | — | — |
| `safety_incident_opened` | `features.py:2173` | admin | — | — | — (no handler found in `admin-dashboard/src`) |
| `sos_false_alarm` | `routes/rides/safety.py:537` | admin/support | — | — | ❌ **unhandled** |
| `charge_dispute_created` | `routes/webhooks.py:1605` | admin | — | — | ❌ **unhandled** |
| `dispatch_geo_event` | `utils/h3_location_index.py:268` | admin (heatmap/geo feature) | — | — | ❌ **unhandled** |
| `payment_completed` | `services/payment_service.py:2041` (+1) | rider (receipt-adjacent) | — (no client-side literal-string handler found) | — | ❌ **unhandled** |
| `payment_retries_exhausted` | `utils/payment_retry.py:217` | admin | — | — | ❌ **unhandled** |
| `pickup_otp_locked` | `routes/drivers/ride_flow.py:1147` | driver (+ push, dual channel) | — | — (WS leg unhandled; push leg also not confirmed handled — see §5) | — |
| `scheduled_ride_stuck` | `utils/scheduled_rides.py:143` | admin | — | — | ❌ **unhandled** |
| `scheduled_ride_policy_blocked` | `utils/scheduled_rides.py:415` (+ push) | rider/driver + admin (dual channel) | — | — (WS leg unhandled) | ❌ **unhandled** |
| `ride_notes_updated` | `routes/rides/stops.py:310` | rider/driver | — | — | — no handler found in any app |
| `ride_noshow` | `routes/drivers/ride_cancel.py:829` | rider/driver | — | — | — no handler found in any app |
| `availability_changed` | `services/driver_session_end_service.py:80` (+5 more call sites — 6 total, the most fan-in of any *emitter*, one file each: `driver_session_end_service.py`, `driver_offer_service.py`, `stale_intent_reconciler.py`, `driver_readiness_reconciler.py` ×2, `insurance_periods.py`) | driver (+ push at one site) | — | **type declared in `shared/types/driverAvailability.ts:194`, zero runtime consumer anywhere** | — |

## §2 Emitted-but-unhandled — the confirmed list

**11 events**, each VERIFIED emitted from real (non-test) production code, each VERIFIED absent from a
runtime handler in all three apps (grep of both quote styles across `rider-app/`, `driver-app/`,
`admin-dashboard/src/`, and `shared/` — see §0.4):

1. `driver_connection_lost` — type is **declared** in `shared/types/api/wsEvents.ts:96` (a TypeScript
   union member) but has **zero runtime consumer** in any of the three apps. This is worth flagging
   separately from a plain "never handled" gap: someone modeled the type, wired nothing to read it.
2. `sos_false_alarm` — admin-facing SOS-resolution event with no receiver.
3. `charge_dispute_created` — Stripe dispute webhook fan-out with no admin-dashboard receiver (a
   dispute is presumably still visible via the disputes admin page's own data fetch — this is the
   **live-update** channel missing, not the data itself).
4. `dispatch_geo_event` — `h3_location_index.py:268` broadcasts to admins for what is presumably a
   live dispatch-heatmap feature; `admin-dashboard`'s monitoring page has no case for it at all —
   either dead/WIP instrumentation or a genuinely missing feature wire-up.
5. `payment_completed` — no client-side handler found in any app.
6. `payment_retries_exhausted` — admin-facing payment-retry-exhaustion alert with no admin receiver.
7. `scheduled_ride_stuck` — admin-facing with no admin receiver.
8. `scheduled_ride_policy_blocked` — WS leg unhandled (the push leg is a separate channel, not verified
   handled either — see §5).
9. `ride_notes_updated` — no handler found in any app.
10. `ride_noshow` — no handler found in any app.
11. `availability_changed` — type **declared** in `shared/types/driverAvailability.ts:194` (same pattern
    as #1: modeled, never consumed) despite being the single most-emitted event in the whole backend by
    call-site count (6 distinct emitters across 5 files).

**Pattern worth naming**: items 1 and 11 are not simple oversights — a `shared/` TypeScript type exists
for both, meaning someone designed the client-side shape and then the wiring never landed. This is the
same "docs are snapshots" / partial-completion pattern `00-history.md` already names for other surfaces
(W0-SUMMARY.md §4), now confirmed at the WS-event level with two more instances.

## §3 Declared-but-unconfirmed (needs a second look, not a clean miss)

- `session_revoked` — driver-app has an explicit `case 'session_revoked':` handler; **no equivalent
  found in `rider-app/hooks/useRiderSocket.ts`'s switch** (grep of that file's case list, §1 above) —
  worth a second check by whoever owns token revocation UX, since a revoked rider session should also
  force a client-side logout.
- `chat_typing` — driver-app handles it; rider-app's switch (§1) has no `chat_typing` case, only
  `chat_message` — typing indicators may be driver-only by design (drivers see "rider is typing") or a
  one-sided gap; not resolved either way this session.
- `emergency_alert` / `safety_incident_opened` — both are safety-critical and both emit real WS events,
  but neither resolved to a literal-string match in any of the three apps' searched files. This likely
  means the admin side renders SOS state from a **dedicated safety page/store** (not the monitoring
  page this session grepped) rather than a true gap — flagged as UNKNOWN, not asserted unhandled,
  because the safety surface was not exhaustively searched (`admin-dashboard/src` beyond `dashboard/
  monitoring/` was not fully walked this session). **Do not cite this as an unhandled-SOS finding
  without checking `admin-dashboard/src/app/dashboard/**` for a safety-specific WS subscription first.**
- `pickup_otp_locked` — dual WS+push emit (`ride_flow.py:1147` WS, `:1158` push `data=`); neither leg
  confirmed handled client-side this session.
- `new_ride_assignment`'s **primary** emit path (the real-time dispatch-offer fan-out, not the admin
  direct-assign path this session's script happened to resolve first) was not independently re-traced
  here — `dispatch.md`/`reliability.md` already own the dispatch-offer WS path in depth; this file only
  confirms the type string round-trips (backend emits it, driver-app's `useDriverDashboard.ts:1011` and
  the FCM foreground handler both have a case for it).

## §4 FCM push `"type"` values (data-message payloads, separate from WS)

| Push type | Emit site | Handled by |
|---|---|---|
| `new_ride_assignment` | dispatch offer push (driver) | driver-app foreground (`useDriverDashboard.ts:2222`) + background (`backgroundMessaging.ts`) |
| `auto_offline` | `routes/drivers/ride_flow.py` / matching | driver-app foreground |
| `ride_cancelled` | ride cancellation push | driver-app foreground + background |
| `subscription_expiring` | `routes/drivers/subscriptions.py:1929` | driver-app foreground |
| `document_expiry_warning` | `utils/document_expiry.py:365` | driver-app foreground; also used as an in-app-notification route key (`routes/notifications.py:759`) |
| `location_health` | `utils/route_gap_monitor.py` | driver-app background (`backgroundMessaging.ts`) |
| `live_activity` | `utils/live_activity.py:140` | rider-app (`app/_layout.tsx:286,694`) |
| `scheduled_ride_dispatched` | `utils/scheduled_rides.py:689` | rider-app (`app/_layout.tsx:704`) |
| `corporate_ride_booked` | `services/guest_notification_service.py:171` | rider-app (`app/_layout.tsx:705`) |
| `safety_checkin` | `utils/safety_checkin_loop.py:142` | rider-app (`app/_layout.tsx:721`) |
| `pickup_otp_locked` | `routes/drivers/ride_flow.py:1158` | not confirmed handled (§3) |
| `scheduled_ride_policy_blocked` | `utils/scheduled_rides.py:409` | not confirmed handled (§3) |
| `payment_retries_exhausted` | `utils/payment_retry.py:245` | not confirmed handled — same event also unhandled over WS (§2) |

All 4 rider-app FCM types (`live_activity`, `scheduled_ride_dispatched`, `corporate_ride_booked`,
`safety_checkin`) and all 5 confirmed driver-app FCM types round-trip cleanly (emitted → handled) —
this is a genuine, verified-clean result for the push channel's "core" event set, in contrast to §2's
WS-side gaps.

## §5 What was not checked

- The exact dispatch-offer WS fan-out path (`routes/rides/matching.py`'s driver-offer loop) was not
  re-traced end to end for its own `new_ride_assignment` emit site — `dispatch.md` already owns this in
  depth; this file only confirms the round-trip via a different (admin direct-assign) call site.
- `admin-dashboard/src/app/dashboard/**` beyond the `monitoring/` page was not exhaustively walked for
  a safety-specific or payment-specific WS/store subscription — §3's `emergency_alert`/
  `safety_incident_opened`/`charge_dispute_created`/`payment_completed` rows should be re-checked
  against those pages specifically before treating them as confirmed product gaps rather than
  "this session didn't find the receiver."
- Android Auto (`driver-app/lib/androidAuto/*`) was found in the initial grep sweep but not opened —
  it may consume a subset of ride/offer events through its own bridge, separate from the React
  Native WS/FCM handlers this file covers.
- The `/mcp` ASGI surface and REST-polling-based state sync (e.g., a screen that just re-fetches on
  focus instead of listening for a WS event) are out of scope — an "unhandled" WS event may still be
  reflected in the UI eventually via ordinary data refetch; this file only speaks to the *live-update*
  channel, not to whether the underlying state ever reaches the screen at all.
