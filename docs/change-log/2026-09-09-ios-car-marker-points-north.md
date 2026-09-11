# Change Impact & Risk Log — iOS car marker stays north / looks like reversing southbound

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-09 |
| Author | Cursor Grok (agent session) |
| Surface(s) | driver-app, shared (pure helpers + rider-app unit tests) |
| Domain (Sentry tag) | drivers |
| PR / commit link | uncommitted local work |
| Related issue or gap ID | Live iOS report: car icon always points north; driving south looks like reverse. Related: `2026-08-28-car-marker-heading-zero.md`, `2026-08-31-driver-map-shared-bearing-fix.md`, `2026-09-09-android-rotation-interpolation.md` |

## 1. Issue / gap identified

On the driver-app map (iOS), the car icon’s hood stays due north while the marker’s position still updates. Driving south looks like the car is reversing. Android is a different stack (Google Maps honors `Marker.rotation`) and was not the reported surface.

## 2. Root cause

Two independent defects, only the first of which explains iOS:

1. **Apple Maps ignores `Marker.rotation`.** Driver-app iOS uses Apple Maps (`provider` unset; no iOS Google Maps key — `app.config.ts`). `react-native-maps` 1.27.2 documents `rotation` as **iOS: Google Maps only**. `AIRMapMarker` has no `rotation` property. CarMarker still wrote an `Animated.Value` into `Marker.rotation`, which is a silent no-op. The PNG is nose-up = geographic north at transform 0. Position animation still runs, so the icon slides south while facing north.

2. **Per-tick movement often stays under 3 m** even while driving (500 ms ticks vs 5–10 m GPS cadence). `selectBearing` then never takes route/travel and can fall through to heading `0` (Android placeholder) or `none` (iOS `heading === -1`). That does not paint iOS rotation by itself, but it also starves `onBearingChange` / course-up camera.

`newArchEnabled: true` is required (Reanimated 4). A known `react-native-maps` Fabric issue (#5918) says inner `transform: rotate` can also be ignored on Apple Maps; if a real iPhone still points north after this change, the next step is a native `CGAffineTransform` patch, not more JS heading math.

## 3. Fix / remediation

- **iOS:** rotate the car PNG with a view `transform` driven by the existing `rotationAnim` (separate wrapper from the native-driver mount spring). Keep `tracksViewChanges` true on iOS so a frozen north-facing snapshot cannot replace live transforms. Android still uses `Marker.rotation` only (no inner rotate — would double).
- **Both:** `coalescePlaybackBearing()` uses the playback spline tangent while interpolating/extrapolating even when this tick’s chord is &lt; 3 m, so heading `0` cannot pin the icon north.
- Optional `mapHeadingRef` on CarMarker subtracts camera heading when a parent passes it (`visualRotationDegrees`). The dashboard does **not** pass it in this change: course-up on iOS was observed as a north-up map (camera heading not applied). Subtracting a *target* camera heading would zero the icon and leave the reported bug in place. Helper is tested for a follow-up if live camera heading is confirmed.

Not done: rider-app `shared/components/CarMarker.tsx` copy, Android Auto, Google Maps on iOS, turning New Architecture off.

## 4. Risk & impact on existing functionality

Blast radius:

- `driver-app/components/CarMarker.tsx` — only in-app callers: `app/driver/(tabs)/index.tsx` (wired) and `lib/androidAuto/carSurface.tsx` (Android Auto; optional `mapHeadingRef` unused, Android path unchanged).
- `shared/utils/vehicleTracking.ts` — new exports only; existing `selectBearing` / `snapToRoute` behavior unchanged. Grepped importers: driver CarMarker, shared CarMarker, RouteLine, heatmap hooks, dashboard `destinationPoint` / `snapToRoute`. None of those call the new functions except driver CarMarker.
- rider-app tests for the new pure helpers (CI actually runs these; `shared/` has no jest project).

Could regress:

- **iOS course-up if the map camera heading actually rotates:** a world-space PNG transform on a screen-upright annotation plus a rotating map can look double-rotated (hood not “up”). Accepted for this fix of the north-stuck / reverse-south bug; follow-up is to pass *applied* camera heading into `mapHeadingRef` once that is verified on device.
- **iOS marker snapshot perf:** `tracksViewChanges` stays true for the single driver marker (same cost as the pulsing-ring path). Not used on multi-marker screens.
- Android visual rotation path unchanged except bearing selection can now pick the spline when ticks are short (correctness, not a new animation).

No ride state machine, money, auth, or backend.

## 5. User-experience effect

- **Driver, iOS, mid-session:** the car hood should follow travel direction (southbound should look like driving forward, not reverse). Visible as soon as the new JS bundle is on the device.
- Android: slow/idle driving should pick a real travel bearing more often (icon not stuck north from heading `0`).
- No copy, pricing, or ride-state change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/utils/vehicleTracking.ts` | `coalescePlaybackBearing`, `visualRotationDegrees` | Testable bearing/visual math |
| `rider-app/__tests__/vehicleTracking.test.ts` | Tests for both helpers | CI collects shared math here |
| `driver-app/components/CarMarker.tsx` | iOS view-transform rotation; no snapshot freeze; coalesce in ticker | Apple Maps actually turns the PNG |
| `driver-app/__tests__/components/CarMarker.test.tsx` | Spline-under-3m, world-space onBearingChange, iOS wrapper / tracksViewChanges, Android no wrapper | Regression coverage |
| `docs/change-log/2026-09-09-ios-car-marker-points-north.md` | This log | Live-tested surface |

## 7. Before / after

```tsx
// Before — iOS writes rotation only to Marker.rotation (Apple Maps no-op)
rotation={isAndroid ? androidRotation : (rotationAnim as any)}
// ticker: selectBearing only; per-tick chord often < 3 m → heading 0 / none
```

```tsx
// After — iOS also rotates the PNG; ticks use spline tangent while interpolating
<Animated.View style={iosRotateStyle}><!-- Image --></Animated.View>
const selected = coalescePlaybackBearing(selectBearing(...), { bearing: p.bearing, mode: p.mode });
```

## 8. Rollback plan

No migration, no live data, no `app_settings` flag. This is a client-only rendering fix; a flag-off state would leave iOS pointing north (the bug). Rollback is a JS/OTA revert or a store build that restores the previous `CarMarker.tsx`. No Stripe/wallet/ride-state remediation.

## 9. Verification performed

- [x] `node node_modules/jest/bin/jest.js __tests__/components/CarMarker.test.tsx --no-coverage --runInBand` (driver-app) — **25/25 passed**
- [x] `node node_modules/jest/bin/jest.js __tests__/vehicleTracking.test.ts --no-coverage --runInBand` (rider-app) — **33/33 passed**
- [x] `npx tsc --noEmit` (driver-app) — clean
- [x] Blast-radius grep: `CarMarker`, `selectBearing`, `Marker.rotation`, `mapHeadingRef`
- [x] Reviewed against CLAUDE.md driver-app / Apple Maps conventions (no Google iOS pod)
- [ ] Not feature-flagged — bugfix restoring expected icon orientation; flag-off would keep the live bug
- [ ] **No production `eas build` / device drive in this environment**
- [ ] driver-app has **no** visual-regression tooling (standing gap) — reasoned from MapKit/`react-native-maps` docs and JS tests, **not screenshotted**

## 10. What was NOT verified

- Real iPhone: southbound drive, north-up vs course-up, idle vs on-trip. If the hood still faces north, treat as `react-native-maps` #5918 (Fabric ignores view transforms) and patch MapKit, do not retune `selectBearing`.
- Whether `animateCamera({ heading })` actually rotates Apple Maps on this New-Arch build. `mapHeadingRef` is intentionally not fed from the dashboard yet.
- rider-app’s copy of CarMarker (separate file).
- Android Auto head unit (C70 still open).
- Android physical device for the spline-under-3m path (unit-tested only).
