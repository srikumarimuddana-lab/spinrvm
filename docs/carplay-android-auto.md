# CarPlay & Android Auto — integration strategy

**Last verified:** 2026-06-02
**Status:** Android Auto implemented on **@iternio/react-native-auto-play** (Nitro / New
Architecture). Committed: the dependency, the JS entry registration, and the car-UI layer
(`driver-app/lib/androidAuto/`) — a route map for navigation states + a status screen
otherwise. **Still unproven on hardware:** it must be confirmed by an **EAS dev build** on an
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

## Implemented: Android Auto route map ("Path B look, Path A cost")

**Decision:** render the route the driver expects (the Uber-style in-dash map), but do NOT pay
for live navigation — draw the polyline we *already* store and hand turn-by-turn to the
driver's own Google Maps / Waze.

- **No new maps spend.** It reuses `rides.planned_route_polyline` (migration 100, a decoded
  `[[lat,lng], …]` line captured once at ride creation). No Routes/Directions/Navigation-SDK
  calls — per-ride cost stays $0, same as the existing `openNavigation()` deep link.
- **Files (`driver-app/lib/androidAuto/`):**
  - `carScreen.ts` — pure model for the **status** states (idle / ride_offered /
    trip_completed): title + 1–4 rows. Fully unit-tested.
  - `carRoute.ts` — pure logic for the **navigation** states: destination by ride state
    (pickup pre-trip, dropoff in-trip), polyline parse, and the Google/Waze hand-off URLs.
    Package-agnostic and fully unit-tested.
  - `carSurface.tsx` — `CarMapSurface`: react-native-maps `MapView`+`Polyline`+`Marker` drawn
    on the head-unit map surface, reading the same `useDriverStore`. Lazy-requires maps so it
    degrades to `null` off-device.
  - `register.ts` — `registerAutoPlay()`: on `HybridAutoPlay` `didConnect`, selects the surface
    by ride state — an iternio `MapTemplate` (surface `component` + Google/Waze hand-off
    buttons) during `navigating_to_pickup` / `arrived_at_pickup` / `trip_in_progress`, and an
    `InformationTemplate` status screen otherwise. Leg+ride-id-keyed root swaps; `isConnected()`
    guard. Registered from `driver-app/index.js` at bundle load (car-only cold launch never
    mounts the phone route layout).
- **Interaction model:** Android Auto routes taps through template **map buttons**, not
  in-surface touchables (driver-distraction rules). iternio's per-button `onPress` callbacks
  (`nav-google` / `nav-waze` → `Linking.openURL`, web-Maps fallback) replace the old fork's
  cross-platform emitter juggling.
- **Still unproven on hardware:** Nitro codegen building under Expo prebuild, the on-surface
  map render, and the map-button icons need the EAS dev build + DHU. The JS contract is covered
  by **29 unit tests** (`lib/androidAuto/__tests__/`).
- **Not yet wired:** accept/decline from the car, online toggle, OTP/rating (stay on phone).

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
   phone cold-start is unaffected.
2. **New native deps** (`@iternio/...` + `react-native-nitro-modules`) ⇒ new EAS build; car
   testing needs a dev build (not Expo Go). Nitro pulls in codegen — first build is the gate.
3. **iOS:** the iternio pod autolinks and compiles, but with CarPlay dormant (no entitlement,
   no scene manifest) the phone app's launch path is untouched.

---

## External approvals (start early — weeks of lead time)

- **Google Play Android Auto** — declare the car app and pass Car App Quality review
  (NAVIGATION category explicitly allows ride/delivery driver apps). Required before Android
  Auto reaches users; for development/DHU testing, enable Android Auto "Unknown sources".
- **Apple CarPlay entitlement** (only when iOS CarPlay is taken up) — request at
  developer.apple.com/carplay. A rideshare *driver* app is typically pushed to the
  template-only **driving-task** category; the in-dash **map** needs the harder
  `carplay-maps` navigation entitlement. Add the granted entitlement to the EAS iOS profile.

---

## What's committed vs what the build must validate

Committed on this branch (verifiable without a device): the dependency swap to iternio, the
entry registration, and the `lib/androidAuto/` car-UI layer with 29 unit tests (lint/tsc clean
on the new files; pure logic fully covered).

**Not yet validated** (needs an EAS build + head unit, which this environment can't run): that
iternio's Nitro codegen builds on our exact stack (Expo SDK 55 / RN 0.85.2), and that a template
+ the route-map surface actually render on an Android Auto head unit. Until that build passes,
treat the integration as unproven — do not merge to a release branch. Per the repo's "surface
loudly, don't mask" rule, the residual risk is stated, not hidden.
