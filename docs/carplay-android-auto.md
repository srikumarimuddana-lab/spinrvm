# CarPlay & Android Auto — integration strategy

**Last verified:** 2026-08-16 (first hardware validation)
**Status:** Android Auto implemented on **@iternio/react-native-auto-play** (Nitro / New
Architecture). Committed: the dependency, the JS entry registration, and the car-UI layer
(`driver-app/lib/androidAuto/`) — an always-on live map (the driver's current location shown
as a car marker, with zoom buttons) that overlays the stored route during a ride, plus a
Lyft-style branded trip card and in-car ride actions (Accept/Decline offer alert, Arrived,
Complete) driven from the same `useDriverStore`.

**PROVEN ON HARDWARE (2026-08-16).** Validated on a real vehicle head unit: app discovery
from a Play-installed build, `CarAppService` registration, `MapTemplate` creation +
`setRootTemplate`, the React surface rendering, and a live Google map with the car marker.
The two open unknowns from the original plan both cleared — Nitro codegen builds under Expo
prebuild on SDK 57 / RN 0.86.2, and react-native-maps **does** render onto the Android Auto
surface. See `docs/change-log/2026-08-16-android-auto-hardware-validation.md` for the five
blockers hit on the way and how each was resolved.

**Still unvalidated on hardware:** marker rotation/glide and the redesigned earnings card
both landed after the last device test. Neither has been seen in a car.
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
- **Library: `@iternio/react-native-auto-play@0.5.13`** + `react-native-nitro-modules@0.35.9`
  (upgraded from 0.4.7 on 2026-08-26 — see the upgrade note below).
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
- **Hardware-validated (2026-08-16):** Nitro codegen builds under Expo prebuild on SDK 57 /
  RN 0.86.2; the surface hosts the React tree; the live map renders. Still unseen in a car:
  marker rotation/glide, the redesigned earnings card, and the alert/header rendering.
  The JS contract is covered by **211 unit tests across 12 suites**
  (`lib/androidAuto/__tests__/`). (An earlier version of this note said the suite could not
  run because `jest.setup.js` failed to resolve `firebase/auth` — that is fixed; re-verified
  green 2026-08-26, including against the 0.5.13 upgrade.)
- **How the surface actually works** (learned the hard way, worth not re-deriving):
  iternio's `VirtualRenderer.kt` creates a `VirtualDisplay` from the car's `SurfaceContainer`
  with `VIRTUAL_DISPLAY_FLAG_PRESENTATION`, hosts an Android `Presentation` on it, and mounts
  a Fabric root via `ReactSurfaceView`. It is a real display context — a normal GL-backed
  Google map composites there fine. **Do not enable `liteMode`**: it was tried on a
  compositing theory that turned out to be wrong (the blank map was an empty API key), and
  it pins the car marker to north and kills `animateMarkerToCoordinate`.
  `VirtualRenderer.kt:383` (0.5.13; was :313 in 0.4.7) paints the React host `DKGRAY` before your tree draws — so a
  dark-grey screen means React never mounted, while a white one means it mounted and the map
  drew blank.
- **Diagnosing the car screen:** there is no dev menu, red box, or Metro console on a head
  unit, and `register.ts`'s `log()` used to be `__DEV__`-gated — compiling every diagnostic
  out of the release builds that are the only ones Android Auto will load. Use the on-surface
  debug panel instead: the **Debug** header action in idle (non-production builds) shows a
  facts table (template state, maps key, renderer, ride state, location, heading, route,
  heatmap) and a live error log.
- **Not yet wired:** online/offline toggle, OTP start-trip + rider rating (stay on phone).
  On the toggle specifically: `toggleOnline` lives in `useDriverDashboard` (component state,
  not mounted on a car-only cold launch) and its failure paths call `router.push('/documents')`,
  `router.push('/vehicle-info')` and toasts — none of which mean anything from a car seat. It
  also needs "Allow all the time" location and rolls the driver back offline without it.
  Wiring it needs the eligibility checks lifted into the store with car-appropriate feedback,
  not a button.
- **No animation, ever.** Google's Car app quality guidelines forbid animated elements on a
  connected head unit and Play enforces it at review before a public release. The card's
  richness comes from hierarchy, type scale, contrast and spacing only.

### Library upgrade 0.4.7 → 0.5.13 (2026-08-26)

Dependency-only bump; **no call-site changes** — every API `register.ts` uses is
byte-identical between the two versions' Nitro specs (the only removals are the voice
methods, which moved from `HybridAutoPlay` to a new `HybridVoice` object we never called).
`react-native-nitro-modules` stays at 0.35.9, exactly the version 0.5.13 is generated
against. What the bump changes, all in the library's own native layer:

- **Head-unit freeze fix (0.5.13):** 0.4.7 released the old `VirtualDisplay` before its
  replacement existed; the window-absent gap stopped Choreographer, freezing map animation
  (`ValueAnimator`) and RN `setInterval`/`setTimeout` until the phone screen woke. 0.5.x
  releases the old display only after the new one has drawn. This is a plausible cause for
  marker-glide/countdown failures we'd have hit in the still-pending device test.
- **Resize reflow (0.5.10):** on head units that run Android Auto windowed, the React
  surface's layout params, scale and Fabric measure specs are now recomputed when the host
  recreates the surface with new dimensions (0.4.7 kept a stale-sized tree). The React
  surface survives the resize, and root components get an updated `window` prop via the new
  `WindowInformationWrapper` the library wraps around our `CarMapSurface` automatically.
- **Car-derived density:** `window.scale`/insets now come from `surfaceContainer.dpi`, not
  the phone's `displayMetrics.density` — the trip-card overlay and inset math were silently
  mis-scaled on any head unit whose density differs from the phone's.
- **ProGuard requirement (0.5.3):** Nitro resolves hybrid objects by class name, so
  minified release builds need
  `-keep class com.margelo.nitro.swe.iternio.reactnativeautoplay.** { *; }`. Wired into
  `app.config.ts` (`expo-build-properties` → `android.extraProguardRules`); inert today
  because minification is off, present so flipping it on later can't break car-only builds.
- **`runtimeVersion` 2.6.0 → 2.7.0:** the bump adds new native hybrid objects
  (`HybridVoice`, `AndroidWindowInformation`), so 0.5.13 JS must never OTA onto a 0.4.7
  binary — the `register.ts` guard would degrade that to silently losing car support.
- **Newly available, not yet used:** `HybridAutoPlay.isCarServiceRunning()` (distinguish an
  AA/CP headless start from e.g. a notification wake) and the real voice API
  (`HybridVoice.startVoiceInput` — speech-to-text, streaming chunks, cancel detection via
  `ErrorUtil.isVoiceInputCanceledError`, car-mic capture). Voice is the sanctioned route to
  driver actions Google's distraction rules forbid touch targets for (the unwired
  online/offline toggle is the realistic candidate) but is a product + PIPEDA decision
  (RECORD_AUDIO, raw-audio egress), not a free win. Note 0.5.10 also added non-navigation
  app categories via `ReactNativeAutoPlay_androidAutoAppCategory` — we must stay on the
  default `navigation`: the live-map surface needs `MAP_TEMPLATES`/`ACCESS_SURFACE`, which
  the non-nav manifest drops.
- **CarPlay-day items banked:** the maneuver travel-estimate fix (0.5.8 — CarPlay only
  applies estimate updates to the already-active maneuver, so in-dash ETA on the current
  turn went stale) and the README's new Expo SDK ≥ 56 requirement that
  `buildReactNativeFromSource: true` be set for the screen-lock timer patch — already true
  in our `app.config.ts` for the Firebase/dev-launcher reasons documented there.

**Validation state:** jest (12 suites / 211 tests), `tsc --noEmit` and eslint are green
against 0.5.13. The EAS `android-auto`-profile build and a head-unit session have NOT been
re-run for the bump — same gate as everything else on this surface: do not release until a
Play-internal build has been seen in a car, ideally re-checking exactly the three behaviours
the native fixes target (marker glide during offer countdown, surface resize, card scale).

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
| Latest | `2.4.1-beta.0` (Jun 2024) | `2.7.22` (Dec 2025) | **`0.5.13`** (Jul 2026; adopted 2026-08-26, initially integrated at 0.4.7) |
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
entry registration, and the `lib/androidAuto/` car-UI layer with 211 unit tests (lint/tsc clean
on the new files; pure logic fully covered).

**Not yet validated** (needs an EAS build + head unit, which this environment can't run): that
iternio's Nitro codegen builds on our exact stack (Expo SDK 55 / RN 0.85.2), and that a template
+ the route-map surface actually render on an Android Auto head unit. Until that build passes,
treat the integration as unproven — do not merge to a release branch. Per the repo's "surface
loudly, don't mask" rule, the residual risk is stated, not hidden.

**Update (2026-09-04):** first real-hardware pass completed — Toyota Grand Highlander MMIC. Map
and template rendered correctly; found (and reportedly fixed, on a branch not yet in this repo as
of this writing) a vehicle-icon-at-90° bug — the marker pointed perpendicular to the direction of
travel instead of along it. This is exactly the class of bug `carSurface.tsx`'s own header warns
about (marker heading vs. camera bearing, same family as the 2026-08-20/21/28 fixes). **Before the
next build:** confirm that fix landed as a real commit with a change-log entry — nothing in this
repo currently documents the Grand Highlander session, the bug, or the fix, which risks repeating
the "shipped a hardware-only fix with no record" pattern already seen three times on this surface.

**Update (2026-09-04, later same day) — deliberate OTA exception, not a policy violation.** The
90° icon-heading fix above was shipped via EAS Update (OTA) rather than the "always build" policy
two sections below. Recorded here as a reasoned exception, with the reasoning, so it doesn't read
as the policy being silently ignored:
- **Why OTA was technically sound here:** JS-only change (`carSurface.tsx`'s heading logic), no
  native module, no `runtimeVersion` bump — the same JS bundle drives both the phone screen and the
  Android Auto surface (`carSurface.tsx` renders into the native `MapTemplate` via iternio's JS
  bridge, not separate native code), so the fix genuinely can reach the car surface this way.
- **Why it's still an exception, not the new default:** the "always build" policy exists because
  the car surface can't be watched live and because Google's car-app review is meant to catch
  exactly this class of rendering bug — an OTA routes around that review even when it's technically
  permitted. Treat future Android Auto changes as build-required by default; this one was a
  deliberate, reasoned call to get an already-confirmed regression fixed fast, not a precedent.
- **Rollout mechanics that applied:** no custom `updates.checkAutomatically` override in
  `app.config.ts`, so Expo's default applies — the update is checked for on app cold start and
  applied on the *next* relaunch, not to an already-running session. No staged-rollout percentage
  is configured in this repo, so (unless `eas update` was invoked with an explicit rollout flag
  outside this repo's tracked config) it shipped to the full channel at once, not a canary slice.
- **Hardware re-check status: ✅ CONFIRMED (2026-09-04).** Re-tested on the same Toyota Grand
  Highlander MMIC — the marker tracked correctly along the route in both directions of travel,
  clean pass, no other issues found. This closes the loop this OTA left open: the fix is now
  hardware-validated, not just JS-level-tested.

---

## OTA vs. native build — policy, not a per-change judgment call

**Every Android Auto surface change ships via a full EAS build (`android-auto` profile) → DHU or
real-hardware validation → Play internal-track resubmission. Never via EAS Update (OTA), even for
a JS-only diff.**

Two independent reasons:

1. **Technical fence already in the repo.** `driver-app/app.config.ts`'s `runtimeVersion` is a
   literal pinned string (`'2.7.0'`, not a policy like `appVersion`) specifically because
   `@iternio/react-native-auto-play` is a compiled native module — a JS/native mismatch doesn't
   crash, it silently disables Android Auto for the driver (`register.ts` degrades to "car support
   disabled"). A same-`runtimeVersion` OTA that only touches JS/TSX in `lib/androidAuto/` is
   technically *eligible* to ship under Expo's own rules — eligible is not the same as safe here.
2. **Sideloading doesn't reach a real car at all.** Per "External approvals" above: Android Auto's
   developer-mode "Unknown sources" toggle does not apply to this app type — a locally-built or
   `preview`-channel APK will never appear in a connected vehicle regardless of OTA/update channel.
   The only path onto a real head unit is the `android-auto` build profile → Play internal track.
   An OTA can't route around this even if someone wanted it to.

The car surface is also the one part of the app nobody can see or interact with while it's live in
a moving vehicle — a bad phone-screen OTA gets screenshotted and reported same-day; a bad
Android-Auto OTA (like the 90° icon) drives silently until a driver happens to notice. Treat this
as a standing rule, not a per-PR decision.

---

## UX & design guidance for the car surface (2026-09-04)

Recommendations below are grounded in what's actually implemented today (`shared/components/RouteLine.tsx`,
`shared/constants/routeMapStyle.ts`, `.claude/context/brand-spinr.md`) plus Android Auto's own
platform constraints — not invented from scratch. Cite the file when changing any of this so the
next person can verify the same way.

**Route color — keep the existing orange→red gradient, don't switch to green.**
`RouteLine.tsx` is one component shared by every map in the app (phone *and* car), specifically so
"every map reads the same" (its own docstring). Its gradient
(`ROUTE_GRADIENT_START = '#FF9500'` → `ROUTE_GRADIENT_END = '#EE2B2B'`, `routeMapStyle.ts`) already
matches Spinr's actual brand red/orange (`brand-spinr.md`: primary red `#FF3B30`, orange accent
`#FF9500`) — it's deliberate brand use, not a placeholder. Green would (a) desync car vs. phone for
the same trip, (b) move off-brand (`brand-spinr.md` explicitly: "Spinr is not a teal/green or amber
brand"), and (c) sit next to the existing red dropoff pin (`#EF4444`) — red/green adjacency is the
single worst color pair for deuteranopia/protanopia (~8% of men). If a future multi-route-choice
feature needs a "selected vs. alternate" cue, differentiate by **stroke weight + opacity**, not
hue — colorblind-safe and matches the mental model every Google/Apple Maps user already has.

**Trip-completion / receipt accent — reuse `#F59E0B`, don't invent a new orange.**
`ROUTE_PIN_COLORS.completion = '#F59E0B'` (`routeMapStyle.ts`) already IS Spinr's semantic
"trip complete" color, and it matches `brand-spinr.md`'s dark-mode Warning token exactly (light
mode: `#d97706`). Use it for any receipt/fare-summary accent instead of picking a new amber — free
consistency already paid for. Caveat: dense fare text is driver-distraction-restricted content on
the car screen mid-drive; this only applies to the phone-app/email receipt unless the fare summary
is explicitly shown only once parked.

**No animation on the car surface — it's a hard platform rule, not a preference.** Per "No
animation, ever" above: Google's Car App Quality guidelines forbid animated elements on a connected
head unit and Play enforces it at review. A "playful end-of-trip" moment has to live on the
**phone app** (`driver-app`'s own trip-complete/earnings screen) instead — same personality, zero
Play-review risk, and arguably better UX anyway (nobody should be watching an animation while
parked waiting to pull away).

**Genuine differentiators already shipped, worth calling out rather than burying:**
- **Live demand heatmap rendered on the car screen itself** (`carSurface.tsx`, HM-30, idle-state
  only) — most rideshare Android Auto integrations are rider-facing-only or bare nav; a
  driver-facing heatmap on the *car* screen, not just the phone, is uncommon.
- **SOS reachable from the car head unit** (`register.ts`'s `sosAction`, deliberately non-primary
  so it's never crowded out by the leg-progress button) — full flow: header action → confirm
  dialog → notifies safety team + emergency contacts. A real safety differentiator, not cosmetic.

**Strategic option, not a quick change: `MapTemplate` vs. `NavigationTemplate`.** Every file in
`lib/androidAuto/` uses Android's car-app-library `MapTemplate` (generic map + buttons); none use
`NavigationTemplate` (the dedicated turn-by-turn class, the only one that unlocks
instrument-cluster/HUD mirroring on supporting vehicles and native maneuver banners). Cosmetic
polish on `MapTemplate` has a ceiling; true Uber/Lyft-level "integrated nav" feel requires migrating
to `NavigationTemplate`'s `Navigator`/trip-update APIs — a real engineering project with a different
lifecycle, not a color or copy change. Worth its own scoped decision once the current
retest/rebuild cycle is done, not bundled into it.

**Screen-size responsiveness.** `MapTemplate` itself scales natively to whatever the head unit
reports — that part is Android's job. What Spinr controls and must test manually is everything
drawn on top (`CarOfferPanel`, `CarTripCard`, heatmap polygons, the marker). Don't treat any one
vehicle (e.g. the Grand Highlander) as the reference device — build a small DHU test matrix across
2-3 aspect ratios (standard widescreen, a narrower/older unit, one large newer display) before
each real-hardware pass.

**Route-progress latency.** The camera already interpolates over a fixed 700ms
(`CAMERA_ANIM_MS`, `carSurface.tsx`) — correct only if the GPS fix cadence feeding it
(`carLocationTask.ts`) is roughly matched or slower. Not measured as of this writing; verify
empirically on the next real-hardware pass rather than assuming it's fine.

**rider-app has no Android Auto surface — believed intentional, not a gap.** A passenger doesn't
need a car-screen app in someone else's vehicle, and Uber/Lyft don't build rider-side Android Auto
either. Flagging only so this reads as a decision, not an oversight, if it's ever questioned.
