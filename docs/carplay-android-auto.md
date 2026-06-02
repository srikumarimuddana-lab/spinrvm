# CarPlay & Android Auto — integration strategy

**Last verified:** 2026-06-02
**Status:** Spike scaffold + first real surface. Committed: the dependency, the
`withCarIntegration` config plugin, the JS smoke test (`car/carSpike.ts`), and the
**Android Auto route map** (`car/carRoute.ts` + `car/carNav.tsx`) — see "Implemented" below.
It must still be proven by an **EAS dev build** on a CarPlay Simulator / Android Auto DHU
before anything relies on it; that build is the gate. No release branch should merge this
until the spike passes.
**Decision inputs:** Scope = *driving-task v1, architected for an in-dash-nav phase later*;
platforms = *CarPlay + Android Auto in parallel*; approach = *`react-native-carplay` fork +
a custom Expo config plugin* (keep all ride logic in shared TS/Zustand).

---

## TL;DR

- The in-car experience is **integrated into the driver app**, not a separate app — same JS
  bundle, same `useDriverStore`/`useAuthStore`, same WebSocket + auth. Only the **UI layer**
  is separate: head units render Apple/Google *templates*, not React Native views, so we add
  a parallel "car UI" that observes the same stores and calls the same actions
  (`acceptRide`, `declineRide`, `arriveAtPickup`, …). Natively this adds a second entry point
  — an iOS `CPTemplateApplicationScene` and an Android `CarAppService` — in the *same* binary.
- **Library: `@g4rb4g3/react-native-carplay@2.7.22`** (a maintained fork of `birkir/react-native-carplay`).
  It supports React 19, is old-architecture-compatible, inherits our Kotlin/SDK versions from
  `rootProject.ext`, and ships Android Auto templates. The upstream `birkir` package (`2.4.1-beta.0`)
  peers `react@^17||^18` only — **rejected** (we run React 19.2.3).
- **No published variant officially lists our exact stack** (Expo SDK 55 / RN 0.85.2). The fork
  is the closest fit, but the final 10% is a **native build spike** (see checklist) — not a
  clean `yarn add`. Do the spike first.
- The single biggest blast-radius item is the **iOS `UIScene` lifecycle migration** CarPlay
  forces on the *existing phone app*. Android Auto is purely additive.
- **iOS CarPlay caveat (discovered in review):** this fork's iOS bridge exposes only the
  *windowed* connect (`connectWithInterfaceController:window:`) — the **navigation/maps**
  entitlement path. CarPlay's **driving-task** category is *windowless*, and the fork has no
  windowless connect, so **driving-task on iOS is not supported by the fork as-is**. iOS CarPlay
  therefore needs either the hard-to-get **navigation entitlement** (windowed, fork-supported) or
  **custom native** work for driving-task. **Android Auto** (navigation category, no entitlement)
  is the tractable, fork-supported path → lead with Android Auto. Because of this (and the
  phone-scene risk), the **entire iOS CarPlay wiring is gated OFF by default** in
  `withCarIntegration.js` (opt in with `SPINR_CARPLAY_IOS=1`).
- Two hard external gates with weeks of lead time: **Apple CarPlay entitlement** (driving-task
  *or* navigation — see caveat) and **Google Play car-app review**. Start both early.

---

## Implemented: Android Auto route map ("Path B look, Path A cost")

The first real car surface, replacing the smoke-test list on the Android Auto root.
**Decision:** render the route the driver expects to see (the Uber-style in-dash map),
but do NOT pay for live navigation — draw the polyline we *already* store and hand
turn-by-turn to the driver's own Google Maps / Waze.

- **No new maps spend.** It reuses `rides.planned_route_polyline` (migration 100, a decoded
  `[[lat,lng], …]` line captured once at ride creation). No Routes/Directions/Navigation-SDK
  calls at all — so the per-ride cost stays $0, same as the existing `openNavigation()` deep link.
- **Files:**
  - `car/carRoute.ts` — pure logic: destination by ride state (pickup pre-trip, dropoff
    in-trip), polyline parse, and the Google/Waze hand-off URLs. Fully unit-tested.
  - `car/carNav.tsx` — `CarMapSurface` (react-native-maps `MapView`+`Polyline`+`Marker`),
    `buildNavMapTemplate` (fork `MapTemplate`, surface `component`, two nav buttons), and
    `initCarNav()` (subscribes `useDriverStore`, leg-debounced root swap, idle `ListTemplate`).
  - `car/androidAutoEntry.tsx` — the Android Auto root now drives `initCarNav()`.
- **Interaction model:** Android Auto routes taps through template **map buttons**, not
  in-surface touchables (driver-distraction rules) — `nav-google` / `nav-waze` →
  `Linking.openURL` brings the nav app up on the head unit.
- **Still unproven on hardware:** map-button icons and the on-surface render need the EAS
  dev build + DHU (checklist steps 3/5/6). The JS contract is covered by 20 unit tests.
- **Not yet wired:** accept/decline from the car, online toggle, OTP/rating (stay on phone),
  and the **CarPlay (iOS)** reuse of this same layer (see "CarPlay reuse" below).

### CarPlay (iOS) reuse

`carRoute.ts` is platform-agnostic and the fork's `MapTemplate` is cross-platform, so ~90% of
this carries to CarPlay. What iOS still needs before it can ship:
1. The Apple CarPlay **navigation entitlement** (the fork only does the *windowed* connect —
   see the TL;DR caveat). Hard external gate; request early.
2. iOS hand-off URL schemes in `buildHandoffUrl` (`maps:` / `comgooglemaps://` / `waze://`)
   instead of the Android `google.navigation:` intent.
3. Drive `initCarNav()` from `app/_layout.tsx` (CarPlay shares the phone JS context) rather
   than a separate AppRegistry root, and drop the Android-only gate.
All of this stays behind `SPINR_CARPLAY_IOS=1`, OFF by default.

## Integration model (separate vs integrated)

```
            ┌──────────────── one running app / one JS bridge ────────────────┐
   Phone scene (expo-router screens)            Car scene (templates)          │
            └──────────────┬───────────────────────────────┬──────────────────┘
                           ▼                                ▼
            useDriverStore / useAuthStore  (single source of truth)
            acceptRide / declineRide / arriveAtPickup / completeRide / toggle
                           │
            useDriverDashboard: WebSocket + location pipeline (UNTOUCHED)
```

**Core rule:** the car layer is *view + controller only*. It never calls the backend
directly — it calls existing store actions, which hit the same endpoints that drive insurance
periods, the atomic dispatch claim, surge-before-booking, and WS fan-out. Every Spinr
invariant is preserved for free. No new dispatch/ride/period logic is written.

Distraction-sensitive steps stay **phone-only in v1**: OTP start-trip (`verifyOTP`), rider
rating (`rateRider`), fare/cancel confirmations. The car shows status + a "finish on your
phone" prompt for those.

---

## Library decision — evidence

| | `birkir/react-native-carplay` | **`@g4rb4g3/react-native-carplay`** |
|---|---|---|
| Latest | `2.4.1-beta.0` | **`2.7.22`** (active) |
| React peer | `^17 \|\| ^18` ❌ (we run 19.2.3) | `^18 \|\| ^19` ✅ |
| RN peer | `^0.60` | `^0.74 \|\| ^0.76 \|\| ^0.79` (we run **0.85.2** — gap) |
| New-arch | old-arch | **conditional** — applies `com.facebook.react` only if `newArchEnabled==true` (build.gradle L23-34) ✅ works with our New-Arch-**disabled** |
| Kotlin/SDK | fixed | **reads `rootProject.ext.kotlinVersion` / compileSdk / etc.** (L7-9, L36-42) → inherits our Option-C 2.2.21 / compileSdk 36 ✅ |
| Android Auto | partial | full template set (`NavigationTemplate`, `PaneTemplate`, `MessageTemplate`, `PlaceListNavigationTemplate`, `MapWith*`) ✅ |
| Expo config plugin | none | **none** — we write our own |

**Residual risks to clear in the spike** (from the fork's `android/build.gradle`):
- `implementation "org.jetbrains.kotlin:kotlin-stdlib:1.9.25"` (L98) — hardcoded; Gradle should
  resolve up to our 2.2.21, but verify no downgrade.
- `compileOnly "com.facebook.react:react-android:0.76.9"` (L94) — compiled against 0.76.9
  headers, runs against 0.85.2; check for ABI/symbol gaps.
- `androidx.car.app:app:1.7.0` against our `compileSdk 36`.
- README claims "Expo SDK 53"; its basic-usage snippet is out of date — the real API is
  **class-based templates** (see below), not plain objects.

---

## Blast radius on the existing app

**Unchanged:** phone business logic/runtime (car layer is additive; no car connected ⇒
the connect listener is a no-op), backend/dispatch/payments/DB/loops, and the **Android**
launch path (`CarAppService` is a new `<service>`; `MainActivity` is untouched).

**Genuinely touched — test carefully:**
1. **iOS `UIScene` migration (main risk).** CarPlay needs the scene lifecycle, so the phone
   app gains a `UIApplicationSceneManifest` with *two* scenes (phone window + CarPlay). This
   changes how the *existing* app cold-starts, backgrounds/foregrounds, and routes
   push-notification taps. Requires full cold-start + push-routing regression.
2. **Small edits to existing files:** `package.json` (deps), `app.config.ts` (plugin +
   entitlement), `app/_layout.tsx` (one hook call), and a behavior-preserving extraction of
   `openNavigation` from `hooks/useDriverDashboard.ts`. Everything else is new files.
3. **Build/signing:** new CarPlay entitlement + native dep ⇒ new EAS build, updated iOS
   provisioning profile; car testing needs a dev build (not Expo Go).

---

## Native config plugin to write (`plugins/withCarIntegration.js`)

Follows the existing local-plugin pattern (`plugins/withNotifeePermissions.js` et al.).

- **iOS:** inject `UIApplicationSceneManifest` (phone `UIWindowSceneSessionRoleApplication`
  + CarPlay `CPTemplateApplicationSceneSessionRoleApplication` → a `CarSceneDelegate`),
  add the **CarPlay entitlement** (`com.apple.developer.carplay-driving-task`), and ensure the
  AppDelegate routes `configurationForConnectingSceneSession`. (react-native-carplay's iOS
  side, `RNCarPlayApp.swift`, expects to be connected from the CarPlay scene delegate.)
- **Android:** add the `CarAppService` `<service>` with
  `androidx.car.app.category.NAVIGATION` intent filter, the `automotive_app_desc` XML + meta,
  the car-app min-API meta, and the `androidx.car.app` permissions
  (`NAVIGATION_TEMPLATES`, `MAP_TEMPLATES`, `ACCESS_SURFACE`). Keep `newArchEnabled: false`.

---

## Car-UI layer (`driver-app/car/`, all TS, behind a thin adapter)

State machine mirrors `useDriverStore.rideState`; each state maps to a template builder that
reads the store and wires buttons to existing actions:

| rideState | Template | Primary actions → existing store action |
|---|---|---|
| `idle` (offline) | `InformationTemplate` "Go online" (gated by `driver.is_verified` + docs) | toggle → `updateDriverStatus(true)` |
| `idle` (online) | `InformationTemplate` "You're online" | toggle → `updateDriverStatus(false)` |
| `ride_offered` | `AlertTemplate` + countdown + TTS announce | Accept → `acceptRide()`, Decline → `declineRide()` |
| `navigating_to_pickup` | `InformationTemplate` / `PaneTemplate` (rider, pickup, ETA) | Navigate → `openNavigation()`, Arrived → `arriveAtPickup()` |
| `arrived_at_pickup` | `InformationTemplate` "Verify on phone" | OTP start stays on phone |
| `trip_in_progress` | `InformationTemplate` / `PaneTemplate` (dropoff, ETA) | Navigate → `openNavigation()` (Complete → phone) |
| `trip_completed` | brief summary → idle | rating → phone |

- Mounted once via `useCarInterface()` in `app/_layout.tsx` (after the auth gate, beside the
  existing Firebase/notification setup). Subscribes to `CarPlay.emitter` connect/disconnect.
- **Adapter isolation:** the builders return Spinr-internal template descriptors; a thin
  `carAdapter` maps them to the lib's classes. This keeps the lib (old-arch-locked, swappable)
  at the edge and makes the builders unit-testable without the native module.
- **TTS:** add `expo-speech` (install via `npx expo install` so it resolves to the SDK-55
  line, *not* the SDK-56 `56.x` on `latest`) for spoken offer announcements; the existing
  `useRideOfferSound` already uses `mixWithOthers`, so audio coexists with nav voice.
- Reuse: `store/driverStore.ts` actions, `shared/store/authStore.ts` (`updateDriverStatus`,
  `is_verified`), `openNavigation` (extract from `hooks/useDriverDashboard.ts`),
  `useRideOfferSound`. The WebSocket + location pipeline is untouched and must keep running.

---

## JS API reference (corrected from source, not the README)

```ts
import { CarPlay, ListTemplate, AlertTemplate, InformationTemplate /* … */ } from '@g4rb4g3/react-native-carplay';

CarPlay.registerOnConnect(() => { /* build + set root template */ });
CarPlay.registerOnDisconnect(() => { /* tear down */ });
// Templates are CLASS instances (not plain objects):
const t = new InformationTemplate({ items: [...], actions: [...] });
CarPlay.setRootTemplate(t);          // root
CarPlay.pushTemplate(next);          // PushableTemplates
CarPlay.presentTemplate(alert);      // PresentableTemplates (Alert/ActionSheet/VoiceControl)
```
Cross-platform: `List/Grid/Information/Map/PointOfInterest/Alert/ActionSheet/NowPlaying`.
Android-only: `Message/Navigation/Pane/PlaceListNavigation/RoutePreviewNavigation/MapWith*/SignIn`.

---

## External approvals (start early — weeks of lead time)

- **Apple CarPlay "Driving Task" entitlement** — request at developer.apple.com/carplay;
  driving-task = template-only, **no custom in-dash map** (that's the later Android-led nav
  phase, which on iOS would need the much harder `carplay-maps` navigation entitlement). Add
  the granted entitlement to the EAS iOS credentials/profile.
- **Google Play Android Auto** — declare the car app and pass Car App Quality review
  (NAVIGATION category explicitly allows ride/delivery driver apps).

---

## Spike checklist (do this FIRST, in an EAS-build-capable env)

Gate the project on this before writing the car-UI layer. **Steps 1–2 + the JS smoke test are
DONE on this branch** (verified at config level: plugin registers all mods, `expo config`
resolves, jest 4/4, tsc/eslint clean on the new files). Steps 3–6 require an EAS dev build:

1. ✅ **DONE** — `@g4rb4g3/react-native-carplay` added (New Arch left disabled). `expo-speech`
   deferred to the feature build — not needed to prove the native link.
2. ✅ **DONE** — `plugins/withCarIntegration.js` (Android car-app + FGS-location perms always-on;
   **all iOS CarPlay wiring gated behind `SPINR_CARPLAY_IOS=1`, OFF by default** — the fork ships
   its own `CarAppService`, which AGP merges in). Plus `car/carSpike.ts` smoke test and
   `car/androidAutoEntry.tsx` registering the **`AndroidAuto` AppRegistry root** the fork's
   `CarPlaySession.runApplication("AndroidAuto")` requires (+ the fork's headless task). Run
   `npx expo prebuild --clean` and confirm it generates without error.
3. ⏳ **Android build passes** with our Option-C chain (Kotlin 2.2.21 / compileSdk 36 / ksp
   2.2.21-2.0.5) — confirm the fork's `kotlin-stdlib 1.9.25` / `react-android 0.76.9 compileOnly`
   don't break the link, and that the fork's library manifest (`package="org.birkir.carplay"`)
   merges cleanly under AGP 8 (the `package` attr is deprecated in favour of `namespace`). EAS
   `development` profile. **Android Auto needs no entitlement — this is the primary path.**
4. ⏳ **iOS build (default) passes with NO CarPlay wiring** — gated off, so the phone app is
   untouched; this just proves the dependency links on iOS. To exercise CarPlay itself you must
   build with `SPINR_CARPLAY_IOS=1`, **and first resolve the two open iOS questions on a real
   build**: (a) the phone app still cold-starts once the scene manifest is added (UIScene
   migration / phone-scene), and (b) the driving-task category — the fork only does the *windowed*
   (navigation-entitlement) connect, so iOS CarPlay needs the **navigation entitlement** or custom
   native for driving-task (see TL;DR caveat). Granting + adding the entitlement to the profile is
   a prerequisite for `SPINR_CARPLAY_IOS=1` to sign.
5. ⏳ Connect **CarPlay Simulator** (Xcode → I/O → External Displays) and **Android Auto DHU**;
   `car/carSpike.ts` should render the "Spinr (spike)" list on connect.
6. ⏳ Tap the list item → `[car-spike] item selected …` appears in Metro logs (JS round-trip OK).

**Pass = green light** to build the real car-UI layer (`car/useCarInterface.ts` wired to
`useDriverStore`, replacing `carSpike.ts`) + Jest tests, and request the entitlements.
**Fail at step 3/4** = native incompatibility on our stack → evaluate forking the gradle/podspec
or pinning, and re-scope. This is exactly why the spike precedes the feature code.

---

## What's committed vs what the build must validate

Committed on this branch (verifiable without a device): the dependency, the `withCarIntegration`
config plugin, and the `car/carSpike.ts` smoke test + unit test. These were validated at the
config/JS level (mods register, `expo config` resolves, jest/tsc/eslint clean on the new files).

**Not yet validated** (needs an EAS build + head unit, which this environment can't run): that
the fork compiles/links on our exact stack, that the iOS scene manifest + `CarSceneDelegate`
don't disturb the phone app's cold start, and that a template actually renders on a head unit.
Until the spike passes, treat the scaffold as unproven — do not merge to a release branch. Per
the repo's "surface loudly, don't mask" rule, the residual risk is stated, not hidden.
