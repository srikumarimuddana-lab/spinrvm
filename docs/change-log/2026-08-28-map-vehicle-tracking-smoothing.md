# Change Impact & Risk Log — Smooth map vehicle tracking (rider + driver)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | vikas@ngitservices.com (via Claude Code session) |
| Surface(s) | rider-app, driver-app, shared |
| Domain (Sentry tag) | rides / drivers |
| PR / commit link | branch `claude/map-vehicle-tracking-animation-3e85y2` |
| Related issue or gap ID | Live-testing feedback 2026-08-28 (Regina Ave): car icon tilted ~90°, teleporting instead of gliding |

## 1. Issue / gap identified

During live app testing the map car marker (a) rendered tilted ~90° to the road, (b) jumped abruptly between positions instead of moving smoothly, and (c) the rider's map lurched continuously during pickup/trip — on both rider and driver apps.

## 2. Root cause

Three independent causes, confirmed by code audit (the car PNGs face north; the asset was not the problem):

1. **Wrong default bearing** — with no valid GPS heading (expo-location reports `-1` when slow/stationary; one driver-app fallback hardcoded `0`), the marker's fallback `travelBearing` initialized to 0 (due north) and only corrected after ≥3 m of movement. On an east–west street (Regina Ave) that is exactly 90° across the road. Rotation also snapped instantly with no smoothing.
2. **Android stale-AnimatedRegion snap-back** — `CarMarker` only advanced its `AnimatedRegion` on iOS; Android used `animateMarkerToCoordinate`. The region therefore stayed frozen at the mount coordinate on Android, and every re-render (each heading change) re-applied that stale coordinate to the native marker — the car snapped back to its first position before the next animation pulled it forward.
3. **Camera thrash** — `ride-in-progress.tsx` and `driver-arriving.tsx` re-ran `fitToCoordinates` on every driver GPS update (~3–4 s), restarting a camera fly-to each time.

Secondary: raw fixes were linearly interpolated with no route awareness (corner cutting, off-road drift), and the 3–4 s driver GPS cadence left few real positions to animate between.

## 3. Fix / remediation

- New pure-math util `shared/utils/vehicleTracking.ts`: snap a GPS fix onto the route polyline (≤35 m) with the segment's bearing; shortest-arc rotation targeting.
- `CarMarker` (shared + driver-app copy): `AnimatedRegion.timing` on both platforms; rotation driven by an `Animated.Value` along the shortest arc (≤600 ms tween); bearing priority route-segment → GPS heading → direction-of-travel; new optional `routeCoordinates` prop.
- Rider screens: one initial camera framing per ride/driver, then `animateCamera` smoothly follows the car at the rider's zoom; panning pauses following for 10 s. Route coords passed to `CarMarker`.
- Driver app: dashboard passes its active nav polyline to the marker; cached-location fallback now reports heading `-1` (unknown) instead of `0`; pickup/trip GPS cadence tightened to 2 s / 5 m.

## 4. Risk & impact on existing functionality

Blast radius — every `CarMarker` consumer was enumerated:

- **shared/components/CarMarker.tsx** consumers: `rider-app/app/(tabs)/index.tsx` (nearby drivers), `ride-options.tsx`, `driver-arriving.tsx`, `driver-arrived.tsx`, `ride-in-progress.tsx`. All keep working with unchanged props (`routeCoordinates` is optional); nearby-driver markers get the new glide/rotation but no route snapping.
- **driver-app/components/CarMarker.tsx** consumers: driver dashboard only (`app/driver/(tabs)/index.tsx`). `lib/androidAuto/carSurface.tsx` renders its own marker, untouched.
- **GPS cadence** (`LOCATION_CONFIGS`): every trip-phase fix is durably recorded by `tripLocationRecorder` and uploaded (REST batch, WS fallback) — 2 s / 5 m roughly **1.5–2× trip-location point volume** (storage, batch uploads, WS messages ~0.5/s per active driver, well under the 30 msg/s WS limit). Backend DB marker writes stay throttled by `_write_marker_if_due`. Battery draw on driver phones increases modestly during trips only.
- Backend, ride state machine, money paths, insurance-period logic: untouched.
- Possible regressions to watch: marker rotation now flows through `Marker.Animated`'s animated-props path (`setNativeProps`) — verified supported in react-native-maps 1.27.2 source; if a platform ignored it the car would keep its last angle (cosmetic, not a crash). Off-route detours render the raw GPS fix (deliberate, never lies about position).

## 5. User-experience effect

Rider and driver both see a visibly different (smoother) live map mid-session: car glides and turns smoothly, stays on the road, camera follows steadily instead of lurching, and panning is no longer fought by the camera. No copy, flow, or money-visible changes.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `shared/utils/vehicleTracking.ts` | new — route snap + rotation math | shared by both apps' markers |
| `rider-app/__tests__/vehicleTracking.test.ts` | new — 12 unit tests | shared/__tests__ isn't collected by any CI jest run |
| `shared/components/CarMarker.tsx` | cross-platform glide, animated rotation, route snapping | fixes tilt + Android snap-back |
| `driver-app/components/CarMarker.tsx` | mirror of the above | driver dashboard marker |
| `rider-app/app/ride-in-progress.tsx` | follow-car camera, route→marker | fixes camera thrash |
| `rider-app/app/driver-arriving.tsx` | follow-car camera, route→marker | fixes camera thrash |
| `driver-app/hooks/useDriverDashboard.ts` | heading `-1` fallback; 2 s/5 m cadence | wrong-north fix; smoother rider tracking |
| `driver-app/app/driver/(tabs)/index.tsx` | pass nav route to marker | route snapping for own car |
| `driver-app/jest.config.js` | map `@shared/utils/vehicleTracking` to the real module | marker tests exercise real math |
| `rider-app/__tests__/rideInProgressScreen.test.tsx`, `driverArrivingScreen.test.tsx` | add `animateCamera` to map-ref mocks | new camera call |

## 7. Before/after snippet

Camera (both rider screens), before — every GPS update:

```tsx
map.fitToCoordinates([driver, dropoff], { edgePadding, animated: true }); // restarts fly-to every ~3 s
```

After — once per ride, then follow:

```tsx
if (!didInitialFitRef.current) { didInitialFitRef.current = true; map.fitToCoordinates([...]); }
else if (Date.now() >= followPausedUntilRef.current) {
  map.animateCamera({ center: { latitude: dLat, longitude: dLng } }, { duration: 800 });
}
```

Marker movement (Android), before — region never advanced, so re-renders snapped back:

```tsx
if (Platform.OS === 'android') node?.animateMarkerToCoordinate?.(coordinate, duration);
else animatedRegion.timing({...}).start();
```

After — one truthful path, snapped to the route:

```tsx
const snap = snapToRoute(coordinate, routeRef.current, MAX_ROUTE_SNAP_M);
const target = snap?.coordinate ?? coordinate;
if (bearing != null) animateRotationTo(bearing, duration);
animatedRegion.timing({ latitude: target.latitude, longitude: target.longitude, duration, useNativeDriver: false }).start();
```

## 8. Rollback plan

Pure client-side change, no migration, no data mutation: revert the branch's commits and ship the previous mobile build (`git revert` is a valid rollback here — no live data is altered; trip-location volume simply returns to the old cadence). No feature flag was added: the change replaces the broken behavior wholesale, and the app_settings flag mechanism has no existing client switch for marker rendering. If only the cadence needs rolling back, `LOCATION_CONFIGS` is a two-line revert.

## 9. Verification performed

- `rider-app`: `npx tsc --noEmit` clean (pre-existing TS5101 deprecation only); jest suites `vehicleTracking` (12 new), `rideInProgressScreen`, `driverArrivingScreen`, `homeScreen`, `rideOptionsScreen` — all pass (161 + 146 tests in the two runs).
- `driver-app`: `npx tsc --noEmit` clean; `driverDashboardScreen` + `hooks/__tests__` + `tripLocationRecorder` suites — 116 tests pass; eslint clean on changed files.
- `rider-app` bundle smoke: `npx expo export --platform web` completed successfully (exit 0) — the closest available production-bundling check in this environment.
- **No real device/EAS production build was run** — this environment cannot produce or run an iOS/Android binary. `tsc`, jest, eslint, and the Expo export bundle are what was actually executed.

## 10. What was NOT verified

- **No on-device visual verification**: rider-app and driver-app have no automated visual/snapshot regression tooling (standing gap, see CLAUDE.md gate #6), and no emulator exists in this environment. The animation behavior (glide, rotation tween, camera follow) is reasoned from react-native-maps 1.27.2 source, not screen-recorded. A manual test drive (or mock-location run) on one Android and one iOS device before release is strongly recommended — specifically: marker stays on-road during a trip, turns smoothly at corners, camera follows without lurching, panning pauses following.
- Animated `rotation` prop through `Marker.Animated` verified against library source, not on-device.
- Battery impact of the 2 s cadence measured nowhere — accepted qualitatively.
- Backend load from ~1.5–2× trip-location points inferred from code (`_write_marker_if_due` throttling confirmed by reading, not load-tested).
