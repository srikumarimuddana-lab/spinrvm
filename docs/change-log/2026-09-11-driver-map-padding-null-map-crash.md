# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (session `01Ro32rhyxmV2ws4MaPhPUSX`) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | _pending_ |
| Related issue or gap ID | Sentry `CRIMSON-SMOKE-7445-SF`. Live-testing report: "when the ride-offer push arrives, I expand it to see the card, then open the minimised driver app and it's a frozen white screen." |

## 1. Issue / gap identified

Opening the driver app while a ride offer is pending (the offer arrived while the app was minimised) sometimes leaves the app on a blank white screen that does not respond. Confirmed as an **unhandled native crash**, not a JS render error: Sentry `CRIMSON-SMOKE-7445-SF`, driver `2.0.0+25`, `handled: no`, `mechanism: expoReactHost`, 3 events between 02:38 and 03:01 on 2026-09-11, the last one 64 s after an app start.

```
NullPointerException: Attempt to invoke virtual method
  'void com.google.android.gms.maps.GoogleMap.setPadding(int, int, int, int)' on a null object reference
  at com.rnmaps.maps.MapView.applyBaseMapPadding(MapView.java:1506)
  at com.rnmaps.fabric.MapViewManager.setMapPadding
  at com.facebook.react.fabric.mounting.SurfaceMountingManager.updateProps
```

## 2. Root cause

Two facts combine:

1. **`react-native-maps@1.27.2` has no null guard on this setter.** `MapView.applyBaseMapPadding` (`MapView.java:1494-1510`) guards only against a zero layout size (`getHeight() <= 0 || getWidth() <= 0`) and defers in that case. It does **not** check `map == null`. Eight other setters in the same file do. So a `mapPadding` **update** that lands after the view is laid out but before `onMapReady` — the ~100-500 ms window on every mount and every `mapKey` remount, during which `GoogleMap` is still `null` — calls `setPadding` on null. The initial-creation path is safe only by accident: at creation the view is not yet laid out, so the size guard defers it.
2. **The driver dashboard changes `mapPadding` inside exactly that window.** `app/driver/(tabs)/index.tsx` derives `mapPadding` from `rideState` (idle reserves the bottom strip for the HUD; other states differ). Opening the app on a pending offer runs `consumePendingOffer` on mount/resume, which hydrates the offer and flips `rideState` to `ride_offered` — a `mapPadding` prop update — while the `MapView` is initialising (cold start), has just been remounted (`mapKey` bumps on ride end and offline→online), or is re-attaching after `router.replace('/driver/')` brought the Drive tab back.

An unhandled exception on the main thread inside a Fabric mount item blanks the React surface without killing the process: the "frozen white screen".

Expanding the notification is not causal — it adds the seconds needed to come back through a path that recreates the map. The issue is `substatus: new` today because live testing involved repeated cold starts (picking up OTA updates) with offers in flight, which is the race exactly. One honest note: the ride-offer handover change in PR #5219 added an `await` between `setIncomingRide` and `router.replace` in `consumePendingOffer`, which can widen this window in some orderings; it did not create the race, which is native and pre-existing.

## 3. Fix / remediation

**Never send a `mapPadding` update before the map exists.** `index.tsx` now tracks which `MapView` instance has fired `onMapReady`, keyed on `mapKey`, and passes `mapPadding={undefined}` until then. Once ready, the existing `rideState`-derived value is supplied as before.

Keyed on `mapKey` rather than a boolean reset in an effect, deliberately: an effect-based reset would let a remounted `MapView` mount with the stale `true`, then flip the prop to `undefined` one frame later — and that flip is itself a padding update landing inside the window this guards. Deriving `mapReady = mapReadyKey === mapKey` makes a remount read as not-ready in the same render the new key appears in.

The upstream fix is a null guard in `applyBaseMapPadding` (native, needs an EAS build and a `patch-package` entry or an upstream PR). This JS gate is the OTA-shippable equivalent and should stay even once the native guard lands, since it also avoids a wasted native round-trip.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** One prop on one `MapView` in one screen. No other consumer of `mapPadding` in driver-app. `rider-app` has its own map screen, untouched.
- **What could regress:** for the ~100-500 ms between layout and `onMapReady`, the map now has no padding instead of the idle-HUD padding. The map has not drawn a tile at that point, so this is not visible. If `onMapReady` never fired (broken Google Maps init), the map would stay unpadded — but in that state there is no map to pad either, and the follow camera's own guards already handle a not-ready map.
- **`onMapReady` was not previously wired** on this `MapView`; adding it introduces no other behaviour.
- **Pre-existing, unrelated, noted not fixed:** `mapPadding.bottom` is computed with `+ insets.bottom`; if safe-area insets were ever undefined, that yields `NaN` (observable in Jest, where the safe-area mock returns none). Out of scope; does not cause this crash.

## 5. User-experience effect

Driver-facing, Android. Opening the app on a pending offer no longer blanks the screen. No visible change otherwise. Visible mid-session only in the sense that a driver currently hitting the white screen stops hitting it.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/index.tsx` | `mapReadyKey` state keyed on `mapKey`; `onMapReady` wired; `mapPadding` withheld until ready | Never update padding while `GoogleMap` is null |
| `driver-app/__tests__/app/driverDashboardScreen.test.tsx` | 1 test: `mapPadding` is `undefined` before `onMapReady` and the `rideState`-derived object after | Regression guard; fails against the previous code |
| `docs/change-log/2026-09-11-driver-map-padding-null-map-crash.md` | This log | Live-tested surface |

## 7. Before / after

```tsx
// BEFORE
<MapView key={mapKey} ... mapPadding={ rideState === 'idle' ? {...} : ... } />

// AFTER
const [mapReadyKey, setMapReadyKey] = useState(-1);
const mapReady = mapReadyKey === mapKey;
<MapView key={mapKey} ...
  onMapReady={() => setMapReadyKey(mapKey)}
  mapPadding={ !mapReady ? undefined : rideState === 'idle' ? {...} : ... } />
```

## 8. Rollback plan

`git-revert-safe` — no data, no persistence. In practice `eas update:republish` of the prior update group (OTA, JS-only; no native change). Reverting restores the crash.

## 9. Verification performed

- `npx jest __tests__/app/driverDashboardScreen.test.tsx --no-coverage` — **55 passed** (1 new).
- `npx tsc --noEmit` — **0 errors**.
- `npx eslint app/driver/(tabs)/index.tsx` — **0 errors**.
- Root cause verified against: the Sentry event (native stack, `handled: no`, timing), `react-native-maps` source (`MapView.java:1494-1510`, the missing null guard, the 8 guarded setters elsewhere), and `index.tsx`'s `rideState`-derived `mapPadding`.
- No production build run; JS-only, ships OTA.

## 10. What was NOT verified

- **Not reproduced on a device.** The mechanism is read from the native stack trace and source, which is far stronger than the inference the earlier car-marker fixes rested on, but the fix itself has not been observed to prevent the crash on hardware. Reproduce: minimise the app, send an offer, reopen the app; confirm no white screen and no new `CRIMSON-SMOKE-7445-SF` events.
- **Whether the same null-map window is reachable via other prop updates.** Only `mapPadding` appears in the crash data and only it is gated here. Any other `MapView` prop derived from state that can change during map init would be a separate instance of the same native bug.
- The `NaN` bottom-padding note in §4 was not investigated.
