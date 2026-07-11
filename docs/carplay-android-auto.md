# CarPlay & Android Auto — integration strategy

**Last verified:** 2026-07-10
**Status:** Android Auto implemented on **@iternio/react-native-auto-play** (Nitro / New
Architecture). Committed: the dependency, the JS entry registration, and the car-UI layer
(`driver-app/lib/androidAuto/`) — an always-on live map (the driver's current location shown
as a car marker, with zoom buttons) that overlays the stored route during a ride, plus a
Lyft-style branded trip card and in-car ride actions (Accept/Decline offer alert, Arrived,
Complete) driven from the same `useDriverStore`.
**Still unproven on hardware:** it must be confirmed by an **EAS dev build** on an
Android Auto DHU (Nitro codegen under Expo prebuild + the on-surface map render are the two
open unknowns). No release branch should merge until that build passes.
**Decision inputs:** Scope = *driving-task v1, architected for an in-dash-nav phase later*;
platforms = *Android Auto now, iOS CarPlay dormant*; approach = *a maintained, New-Architecture
native library + keep all ride logic in shared TS/Zustand*.

---

## TL;DR

- The in-car experience is **integrated into the driver app**, not a separate app — same JS
  bundle, same `useDriverStore`/`useAuthStore`, same WebSocket + auth. Only the **UI layer**
  is separate: head units render Apple/Google *templates*, not React Native views, so we add
  a parallel "car UI" that observes the same stores. Natively this adds a second entry point
  (an Android `CarAppService`, and on iOS a `CPTemplateApplicationScene`) in the *same* binary.
- **Library: `@iternio/react-native-auto-play@0.4.7`** + `react-native-nitro-modules@0.35.9`.
  Built on **Nitro Modules** (New-Architecture native — which we're on, via Reanimated 4),
  peers `react-native: *` (no version ceiling — important, we run **0.85.2**), supports both
  CarPlay and Android Auto, and ships its own merged `AndroidManifest` (CarAppService +
  permissions) so **no app-side config plugin is needed**. Pre-1.0, hence pinned exact.
- **Why not the alternatives:** `birkir/react-native-carplay` is a stale 2024 beta
  (`2.4.1-beta.0`, React ≤18) — rejected (we run React 19.2.3). `@g4rb4g3/react-native-carplay`
  (a maintained legacy-bridge fork) caps at RN 0.79 and needed a hand patch that didn't compile;
  it was trialled and **removed** in favour of iternio. See the evidence table below.
- **Android Auto is the live path** (navigation category, no entitlement). **iOS CarPlay is
  dormant:** it needs an Apple-granted CarPlay entitlement plus scene-delegate / Info.plist
  wiring not present here, and adding the entitlement speculatively breaks iOS signing.
- One hard external gate with weeks of lead time when iOS is taken up: the **Apple CarPlay
  entitlement**. **Google Play car-app review** applies before Android Auto ships to users.

---

## Implemented: Android Auto live map ("Path B look, Path A cost")

**Decision:** always show the driver an in-dash map (the Uber-style experience): a car marker
that follows the phone's current location, with zoom-in/zoom-out buttons, in every state — so
the car screen is useful even when idle, not a blank status pane. During a ride, overlay the
route the driver expects without paying for live navigation — draw the polyline we *already*
store and hand turn-by-turn to the driver's own Google Maps / Waze.

- **No new maps spend.** It reuses `rides.planned_route_polyline` (migration 100, a decoded
  `[[lat,lng], …]` line captured once at ride creation). No Routes/Directions/Navigation-SDK
  calls — per-ride cost stays $0, same as the existing `openNavigation()` deep link.
- **Files (`driver-app/lib/androidAuto/`):**
  - `carRoute.ts` — pure logic for the **navigation** states: destination by ride state
    (pickup pre-trip, dropoff in-trip), polyline parse, and the Google/Waze hand-off URLs.
    Package-agnostic and fully unit-tested.
  - `carMapCamera.ts` — pure zoom math + a tiny `useCarMapCamera` zustand store. The projected
    surface is non-interactive, so the template's zoom buttons (register.ts) and the rendered
    surface (carSurface.tsx) can't share React state directly — this store is the channel.
    Clamped to a street↔city span. Unit-tested.
  - `useCarLocation.ts` — a self-contained foreground location watcher for the surface,
    independent of the phone UI (which isn't mounted on a car-only cold launch). Seeds from the
    dashboard's last-known fix in AsyncStorage; only watches when permission is already granted.
  - `carSurface.tsx` — `CarMapSurface`: react-native-maps `MapView` drawn on the head-unit
    surface, reading the same `useDriverStore`. **Always** renders a live map with a `CarMarker`
    at the driver's current location and a controlled, zoom-driven camera; overlays the stored
    `Polyline` + destination `Marker` during a ride. Lazy-requires maps so it degrades to `null`
    off-device.
  - `carScreen.ts` — pure status-row model (title + 1–4 rows). No longer on the live Android
    path (the card + map now cover idle too); retained for the dormant iOS CarPlay path + tests.
  - `carCard.ts` — pure presentation model mapping the ride state to the glanceable trip card
    (status label + accent, rider + rating, destination + ETA/distance, fare + surge/WAV,
    phone-only hints). Shared by the surface card and the ride-offer alert. Fully unit-tested.
  - `CarTripCard.tsx` — the branded, display-only card drawn over the map surface from that model.
  - `register.ts` — `registerAutoPlay()`: on `HybridAutoPlay` `didConnect`, builds **one**
    persistent iternio `MapTemplate` (surface `component`) and then drives it per state via
    `setMapButtons` / `setHeaderActions` / `showAlert` — never rebuilding it (no flicker). Map
    buttons: zoom always, plus a single Navigate hand-off while navigating; header carries the
    leg's progress action; offers raise an Accept/Decline alert. State+leg+ride-id-keyed chrome
    de-duping; `isConnected()` guard; idempotent connect; camera reset on connect. Registered
    from `driver-app/index.js` at bundle load (car-only cold launch never mounts the phone route
    layout).
- **Interaction model:** Android Auto routes taps through template **map buttons** + the
  **header action** + **navigation alerts**, never in-surface touchables (driver-distraction
  rules). iternio's per-button `onPress` callbacks drive zoom (`→ useCarMapCamera`) and the
  Navigate hand-off (Google `nav` intent → `Linking.openURL`, web-Maps fallback). 3 map buttons
  during a ride (1 Navigate + 2 zoom) — well under Android Auto's action-strip ceiling.
- **Lyft-style UX (`carCard.ts` + `CarTripCard.tsx`):** the surface draws a branded **trip
  card** over the map — status pill; rider **avatar** (photo, with a coloured-initial fallback)
  + rating; the current destination with ETA/distance; fare with surge/WAV chips; an **earnings
  bonus** chip + **incentive/quest perk**; and phone-only hints. One persistent `MapTemplate` is
  updated via `setHeaderActions` / `setMapButtons` / `showAlert` (never rebuilt — no flicker):
  - **Ride offer** → a high-priority `showAlert` with **Accept / Decline** (the iconic request
    card; subtitle carries fare + bonus + ETA + surge), auto-dismissed when the offer state is
    left; the store keeps its own countdown, so a visual timeout never double-declines.
  - **Heading to pickup** → header **Arrived** (`arriveAtPickup`) + a single **Navigate**
    hand-off button (Google, the AA default) + zoom.
  - **At pickup** → header **Start on phone** (OTP start-trip is distraction-sensitive and stays
    phone-only per CLAUDE.md).
  - **In trip** → header **Complete trip** (`completeRide`) + Navigate + zoom.
  All car actions call the SAME `useDriverStore` actions the phone calls, so every dispatch /
  insurance-period / settlement invariant is preserved for free.
- **Icons:** purpose-built monochrome glyphs — `nav_arrow.png` (3 densities) for the Navigate
  hand-off, `zoom_in/zoom_out.png` for the camera. (Header actions are text, so need no icon.)
- **Still unproven on hardware:** Nitro codegen building under Expo prebuild, the on-surface
  map + card render, and the alert/header rendering need the EAS dev build + DHU. The JS
  contract is covered by **56 unit tests** (`lib/androidAuto/__tests__/`).
- **Not yet wired:** online/offline toggle, OTP start-trip + rider rating (stay on phone).

### iOS CarPlay — dormant

The pure layers (`carScreen.ts`, `carRoute.ts`) are platform-agnostic and ready, but the iOS
CarPlay surface is intentionally not wired: it requires an Apple-granted CarPlay entitlement
(`com.apple.developer.carplay-*`) plus the scene-delegate + `UIApplicationSceneManifest`
wiring iternio documents, none of which is present. `registerAutoPlay()` is guarded to
`Platform.OS === 'android'`, so the iternio native module is never loaded on iOS.

---

## Integration model (separate vs integrated)

```
            ┌──────────────── one running app / one JS bundle ────────────────┐
   Phone scene (expo-router screens)            Car scene (templates)          │
            └──────────────┬───────────────────────────────┬──────────────────┘
                           ▼                                ▼
            useDriverStore / useAuthStore  (single source of truth)
            acceptRide / declineRide / arriveAtPickup / completeRide / toggle
                           │
            useDriverDashboard: WebSocket + location pipeline (UNTOUCHED)
```

**Core rule:** the car layer is *view + controller only*. It never calls the backend directly
— it reads the same stores and (when car actions are added) calls existing store actions, which
hit the same endpoints that drive insurance periods, the atomic dispatch claim,
surge-before-booking, and WS fan-out. Every Spinr invariant is preserved for free.

Distraction-sensitive steps stay **phone-only in v1**: OTP start-trip (`verifyOTP`), rider
rating (`rateRider`), fare/cancel confirmations. The car shows status + a "finish on your
phone" prompt for those.

---

## Library decision — evidence

| | `birkir/react-native-carplay` | `@g4rb4g3/react-native-carplay` | **`@iternio/react-native-auto-play`** |
|---|---|---|---|
| Latest | `2.4.1-beta.0` (Jun 2024) | `2.7.22` (Dec 2025) | **`0.4.7`** (May 2026) |
| React peer | `^17 \|\| ^18` ❌ | `^18 \|\| ^19` ✅ | `*` ✅ |
| RN peer | `^0.60` | `^0.74 \|\| ^0.76 \|\| ^0.79` (we run **0.85.2** — gap) | **`*`** ✅ (no ceiling) |
| Architecture | old-arch | legacy bridge + new-arch compat | **Nitro (New-Architecture native)** ✅ |
| CarPlay + Android Auto | both (older) | both | **both** ✅ |
| App-side config plugin | none | none (hand-wired) | **none needed** — ships merged manifest |
| Verdict | rejected (React 18) | trialled, **removed** | **chosen** |

**Why g4rb4g3 was dropped:** it caps officially at RN 0.79 (we're on 0.85), and the hand patch
added to fix its RN-0.76 Kotlin breakage introduced a **duplicate `companion object`** in
`CarPlaySession.kt` — the exact "Only one companion object is allowed" / "Unresolved reference
'TAG'" error that started this work. iternio sidesteps the whole class of problem.

**Residual risk to clear in the EAS build (iternio):** Nitro requires the New Architecture (we
have it) and generates C++/codegen at build time — confirm it builds cleanly under Expo prebuild
on RN 0.85.2, and that react-native-maps renders onto the Android Auto surface.

---

## Blast radius on the existing app

**Unchanged:** phone business logic/runtime (the car layer is additive; no car connected ⇒ the
`didConnect` listener never fires), backend/dispatch/payments/DB/loops, and the **Android**
launch path (iternio's `CarAppService` is a new `<service>` merged from its library manifest;
`MainActivity` is untouched).

**Genuinely touched — test carefully:**
1. **Custom JS entry** (`driver-app/index.js`, `package.json` `main`): imports
   `expo-router/entry` (phone app unchanged) then calls `registerAutoPlay()`. Verify normal
   phone cold-start is unaffected. The iternio require is guarded: importing the package
   instantiates its Nitro HybridObject, which throws on a binary without the native module
   (Expo Go, or a stale dev client). The guard degrades that to "no car support" +
   `console.error` + a Crashlytics non-fatal instead of crashing the phone app at startup.
   (OTA safety itself is already fenced: the `runtimeVersion` 2.3.0 bump landed in the same
   merge as the iternio/nitro deps — the app.config comment names only webview, but the
   fence covers all three. The guard is Expo Go coverage + belt-and-suspenders.)
2. **New native deps** (`@iternio/...` + `react-native-nitro-modules`) ⇒ new EAS build; car
   testing needs a dev build (not Expo Go). Nitro pulls in codegen — first build is the gate.
3. **iOS:** the iternio pod autolinks and compiles, but with CarPlay dormant (no entitlement,
   no scene manifest) the phone app's launch path is untouched.

---

## External approvals (start early — weeks of lead time)

- **Google Play Android Auto** — real vehicles only list templated Car App Library apps installed
  from a trusted source. Android Auto's developer-mode **Unknown sources does not apply** to this
  app type, so the sideloaded APKs produced by the `test` and `preview` EAS profiles will not
  appear in a car even when that switch is enabled. Build and publish the dedicated Play internal
  test profile instead:

  ```bash
  cd driver-app
  eas build --platform android --profile android-auto --auto-submit-with-profile android-auto
  ```

  Add the driver's Google account to Play Console's internal testers, accept the opt-in link, then
  install **Spinr Driver from Google Play** on the phone connected to the car. The `android-auto`
  profile creates a store AAB and completes the internal-track release; it is not a public
  production rollout. A local APK remains suitable for emulator/DHU debugging where supported,
  but it is not a valid real-car discovery test.
- **Google Play car-app review** — declare the car app and pass the applicable Car App Quality
  review before a public release.
- **Apple CarPlay entitlement** (only when iOS CarPlay is taken up) — request at
  developer.apple.com/carplay. A rideshare *driver* app is typically pushed to the
  template-only **driving-task** category; the in-dash **map** needs the harder
  `carplay-maps` navigation entitlement. Add the granted entitlement to the EAS iOS profile.

---

## What's committed vs what the build must validate

Committed on this branch (verifiable without a device): the dependency swap to iternio, the
entry registration, and the `lib/androidAuto/` car-UI layer with 56 unit tests (lint/tsc clean
on the new files; pure logic fully covered).

**Not yet validated** (needs an EAS build + head unit, which this environment can't run): that
iternio's Nitro codegen builds on our exact stack (Expo SDK 55 / RN 0.85.2), and that a template
+ the route-map surface actually render on an Android Auto head unit. Until that build passes,
treat the integration as unproven — do not merge to a release branch. Per the repo's "surface
loudly, don't mask" rule, the residual risk is stated, not hidden.
