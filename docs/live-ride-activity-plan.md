# Live Ride Activity — Implementation Plan (rider app)

Status: **PLAN — not yet implemented.** Scoped for scheduling.

## 1. Goal
After a driver **accepts** a ride, the rider gets a live, glanceable ride‑status
surface **outside the app**, updating as the driver approaches:

- **iOS** — a Live Activity on the Lock Screen + Dynamic Island (status, ETA,
  driver/vehicle), via ActivityKit.
- **Android** — an ongoing / "Live Update" notification with the same status +
  ETA, updated in place (via notifee).

Lifecycle: **start at `driver_accepted`**, update on each state change + a
throttled ETA tick, **end at `completed` / `cancelled`**.

## 2. State → content mapping
Source of truth is the existing ride state machine (`routes/rides.py`).

| Ride state | Headline | Activity action |
|---|---|---|
| `driver_accepted` | "Driver on the way" + ETA to pickup | **START** |
| `driver_arrived` | "Your driver has arrived" | update |
| `in_progress` | "On your way to {dropoff_area}" + ETA to dropoff | update |
| `completed` | "Trip complete" | **END** (final) |
| `cancelled` | "Ride cancelled" | **END** |

**Shared content-state schema** (one struct both platforms render):
`{ status, headline, eta_minutes, driver_name, vehicle_label, pickup_area, dropoff_area, ride_code, updated_at }`
(area-only, no exact addresses/PII — matches the DriverPublicView/offer-card minimisation policy.)

## 3. Architecture / data flow
1. Rider app is foreground at `driver_accepted` → **starts the activity locally**
   with the initial content and obtains the **ActivityKit push token** (iOS) /
   device token (Android).
2. App **registers the token** with the backend for this ride.
3. Backend, on each ride-state transition (already an emit point for WS) + a
   throttled location/ETA tick, **pushes an update**:
   - iOS → APNs **`liveactivity`** push (`event: update`) to the stored token.
   - Android → FCM **data** message → notifee updates the ongoing notification.
4. On `completed`/`cancelled` → backend sends a final **end** push; both
   platforms end/cleanup the activity.

The **data already exists** (state machine + driver-location stream over the
`spinr:ws:dispatch` WS). New work = the **update-push path + token storage** and
the two OS surfaces.

## 4. Backend work
- **Migration** `NNN_ride_live_activities.sql`: table `ride_live_activities`
  `{ id, ride_id, rider_id, platform, push_token, started_at, ended_at }` + RLS
  (rider-owned), index on `ride_id`.
- **Endpoint** `POST /api/v1/rides/{ride_id}/live-activity/register` (rider-auth,
  ownership-checked) — upsert the push token + platform for the ride.
- **`utils/live_activity.py`** — `send_live_activity_update(ride, event)`:
  builds the content-state and dispatches per platform.
- **`utils/apns_client.py`** (new) — token-based (`.p8`) APNs HTTP/2 client.
  ActivityKit pushes need **direct APNs**, topic `<bundle>.push-type.liveactivity`,
  header `apns-push-type: liveactivity`, body `{aps:{timestamp, event,
  content-state, stale-date, dismissal-date?}}`. (FCM does not carry the
  liveactivity push type, so this can't ride the existing FCM path.)
- **Hooks**: at each transition in `routes/rides.py` (accept / arrived /
  start / complete / cancel) call `send_live_activity_update` — reuse the
  existing WS-emit sites.
- **ETA/location throttle**: piggyback on the driver-location update (every
  ~20–30 s while en route) to push an ETA refresh; **rate-limit** to respect the
  ActivityKit push budget (APNs throttles frequent Live Activity updates).

## 5. iOS work (rider app — native)

Use **`@bacons/apple-targets`** (`npm i @bacons/apple-targets`) for the Widget
Extension target via Continuous Native Generation. It scaffolds + links the
Apple target through prebuild; it does **NOT** provide an ActivityKit JS bridge —
that is a separate custom module (see below).

- **5a. Widget Extension target** via `@bacons/apple-targets`:
  - `targets/LiveRideActivity/expo-target.config.js` → `{ type: "widget" }`
    (this is the Widget Extension that hosts the Live Activity).
  - `targets/LiveRideActivity/*.swift` — SwiftUI: `ActivityAttributes`
    (static: ride_code, areas, driver_name, vehicle_label) + `ContentState`
    (dynamic: status, headline, eta_minutes), the Lock Screen view, and the
    Dynamic Island (compact / minimal / expanded) views.
  - `targets/LiveRideActivity/Info.plist` — `NSSupportsLiveActivities = true`.
  - App config: add the plugin to `app.config.ts` and set `ios.appleTeamId`.
- **5b. ActivityKit JS↔native bridge** (NOT covered by the package): a small
  **Expo native module** (Swift, Expo Modules API) exposing `start(attributes,
  content) → pushToken`, `update(content)`, `end()`. Lives in the main app
  target and shares the `ActivityAttributes`/`ContentState` types with the
  widget. (No maintained community lib is assumed compatible with Expo 55 / RN
  0.85 — treat as custom.)
- **5c. `services/liveActivity.ts`** (JS): on `driver_accepted` (ride-status
  screen / WS event) call `start(...)`, capture the push token, POST it to the
  backend; `end()` on terminal state.

## 6. Android work (rider app — native)
Android has no ActivityKit equivalent; there is **no `@bacons/apple-targets`
counterpart** — the "package" here is **notifee**, and the surface is a
notification (no widget/target to scaffold). Two tiers:

- **Baseline (all Android versions) — ongoing notification:** add
  **`@notifee/react-native`** (driver app already uses it — mirror its
  channel/config) and build an **ongoing**, high-importance notification
  (channel `ride-live`, not dismissible) showing status + ETA + driver/vehicle +
  a "View"/"Call" action; update **in place by id**; cancel on end. This is the
  practical, ships-everywhere equivalent.
- **Android 16+ (API 36) — Live Updates / Promoted Ongoing:** the OS now has a
  purpose-built "Live Updates" template (`Notification.ProgressStyle` + the
  promoted-ongoing flag) for ride/delivery tracking — a status-bar chip + a
  prominent lock-screen card, the closest analog to an iOS Live Activity. Use it
  **when available, with graceful fallback** to the ongoing notification below
  16. notifee may not yet expose `ProgressStyle`/promoted-ongoing, so this tier
  likely needs a **small custom native module / raw Notification builder**.
- **FCM data handler** (foreground + background + killed): on a `live_activity`
  data message, create/update the notification; on `end`, cancel. Drives both
  tiers from the same backend push.

## 7. Build / deploy
- **Both platforms need a fresh native build (EAS build), not OTA** — iOS adds a
  widget target + entitlements; Android adds notifee.
- Add the config plugins to `rider-app/app.config.ts`.
- Provision an **APNs `.p8` auth key** for ActivityKit pushes (separate concern
  from FCM); store as a backend secret; configure the liveactivity topic.
- **Device testing required** — Live Activity push doesn't work in the simulator;
  Dynamic Island needs iPhone 14 Pro+ (Lock Screen activity works on others).

## 8. Phasing
- **Phase 0 (shared):** content-state schema + `ride_live_activities` table +
  register endpoint + state-machine hooks (log-only, no push yet).
- **Phase 1 (Android):** notifee ongoing notification + FCM update path. Ships
  value first, lower lift.
- **Phase 2 (iOS):** Widget Extension + config plugin + JS bridge + APNs
  ActivityKit push (the heavy part).
- **Phase 3:** throttled ETA/location updates + polish (driver photo, map
  snapshot, actions), metrics.

## 9. Risks / unknowns
- Expo 55 / RN 0.85 compatibility of Live Activity libs → likely a **custom
  native module**.
- ActivityKit needs a **new direct-APNs path** (token `.p8`); existing pushes go
  via FCM.
- **APNs update budget** — must throttle ETA updates or pushes get dropped.
- iOS 16.1+ for Live Activities; Dynamic Island iPhone 14 Pro+.
- Token lifecycle: push-to-start vs per-activity update token; rotation +
  end-of-activity cleanup; killed-app iOS updates are **push-only** (no app code
  runs) so the backend must drive them.

## 10. Rough effort
- Backend ~3–5 d · iOS ~5–8 d · Android ~2–3 d · + device testing → **~2–3 weeks**
  focused, native builds on both platforms.

## 11. Concrete files / targets
**Backend**
- `backend/migrations/NNN_ride_live_activities.sql`
- `backend/routes/rides.py` (register endpoint + transition hooks)
- `backend/utils/live_activity.py` (content-state + per-platform dispatch)
- `backend/utils/apns_client.py` (token-based APNs HTTP/2 for `liveactivity`)

**Rider app**
- `rider-app/ios/LiveRideActivity/*` (SwiftUI widget)
- `rider-app/plugins/withLiveActivity.ts` (+ `app.config.ts` wiring, Info.plist)
- `rider-app/modules/live-activity/*` (JS↔native bridge) or community lib
- `rider-app/services/liveActivity.ts` (start/update/end + token register)
- `rider-app/services/rideLiveNotification.ts` (Android ongoing) + notifee dep
- rider-app FCM handler updates (background/killed)
