# CarPlay & Android Auto — integration strategy

**Last verified:** 2026-05-30
**Status:** Design + spike-first. No native code committed yet — the integration must be
proven in an EAS-build-capable environment before any plugin/dep lands on a release branch.
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
- Two hard external gates with weeks of lead time: **Apple CarPlay "Driving Task" entitlement**
  and **Google Play car-app review**. Start both early.

---

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

Gate the project on this before writing the car-UI layer:

1. `npx expo install @g4rb4g3/react-native-carplay` (+ `expo-speech`); keep New Arch disabled.
2. Write a minimal `withCarIntegration` plugin (iOS scene manifest + entitlement; Android
   `CarAppService`). `npx expo prebuild` cleanly.
3. **Android build passes** with our Option-C chain (Kotlin 2.2.21 / compileSdk 36 / ksp
   2.2.21-2.0.5) — confirm the fork's `kotlin-stdlib 1.9.25` / `react-android 0.76.9 compileOnly`
   don't break the link. EAS `development` profile.
4. **iOS build passes** with the dual-scene manifest; confirm the **phone app still cold-starts,
   backgrounds/foregrounds, and routes a push-notification tap** correctly (the scene migration
   regression).
5. Connect **CarPlay Simulator** (Xcode → I/O → External Displays) and **Android Auto DHU**;
   render one hardcoded `ListTemplate`/`InformationTemplate` from a dev build.
6. Verify `CarPlay.registerOnConnect/Disconnect` fire and a button callback reaches JS.

**Pass = green light** to build the car-UI layer + Jest tests and request the entitlements.
**Fail at step 3/4** = native incompatibility on our stack → evaluate forking the gradle/podspec
or pinning, and re-scope. This is exactly why the spike precedes the feature code.

---

## Why nothing native is committed yet

This environment cannot run an EAS build, a CarPlay Simulator, an Android Auto DHU, or obtain
the Apple entitlement, so a native config plugin / scene migration written here would be
**unverifiable** and could destabilize the carefully-tuned Android build
(`docs/android-build-strategy.md`). Per the repo's "surface loudly, don't mask" rule, the
integration is staged as a spike-gated plan rather than speculative native code.
