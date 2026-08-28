# Change Impact & Risk Log — Car marker points north on east–west streets (reported heading 0)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | srikumarimuddana@gmail.com (Claude Code) |
| Surface(s) | driver-app, rider-app, shared |
| Domain (Sentry tag) | drivers / rides |
| PR / commit link | branch `claude/driver-app-car-orientation-srtdi0` |
| Related issue or gap ID | Live-testing report: "driver moving westbound, car icon pointed north". Same symptom class as `2026-08-21-android-auto-car-heading.md` and `2026-08-28-map-vehicle-tracking-smoothing.md` — this is the residual case both missed. |

## 1. Issue / gap identified

A driver travelling **westbound** saw the car marker translate west across the
map correctly while the icon stayed pointed **due north** — a constant 90°
error on east–west streets.

PR #4652 (commit `2b6e843`, currently `HEAD`) fixed this symptom for the
*cached* location fallback the day before. It still reproduces on the **live
GPS path**, which that fix did not touch.

## 2. Root cause

The bearing-source priority ranked the platform-reported heading **above** the
direction-of-travel fallback:

```
route segment → reported GPS heading → direction of travel
```

combined with a validity predicate of `heading >= 0`, which accepts `0`.

`0` is not a safe sentinel. iOS sets `CLLocation.course` to `-1` when the
course is invalid, and the code was written to that convention — the comment at
`carFixChannel.ts:204` states it outright ("Negative is expo's 'unknown'
sentinel"). Android does not follow it: `android.location.Location` carries a
separate `hasBearing()` flag, and `getBearing()` returns `0.0` when that flag
is false. expo-location reads the bearing without consulting `hasBearing()`, so
"this fix has no course" reaches JS as a literal `0` — indistinguishable from
genuinely driving due north.

Because that `0` is "valid", the middle branch won on every fix and the
travel-bearing fallback below it **never executed**. The car's position and its
rotation come from independent code paths (`animatedRegion.timing()` consumes
the coordinate directly), which is why the marker moved correctly while its
angle stayed pinned at 0.

Worst case is an online-but-idle driver: `app/driver/(tabs)/index.tsx:683`
passes `routeCoordinates` only when a ride is active, so with no ride the
route-snap branch — the one thing that outranked the bad heading — cannot run
at all.

The same defect existed independently in three files (12 predicate sites),
because the priority chain was copy-pasted rather than shared.

## 3. Fix / remediation

- **Reordered the priority** to `route segment → direction of travel →
  reported GPS heading`. Two fixes ≥ `MIN_BEARING_MOVE_M` apart yield a bearing
  that is correct on every platform regardless of what the provider claims.
- **Extracted the chain into one pure function**, `selectBearing()` in
  `shared/utils/vehicleTracking.ts`, and pointed both `CarMarker` copies at it.
  The duplication is what allowed the two copies to drift and what made the
  logic untestable.
- **Added `hasMovementBearingRef`**: once movement has established a bearing, a
  reported heading is ignored entirely. Without this, a driver stopped at a
  light on an east–west street (movement below the 3 m threshold) would fall
  back to the placeholder `0` and snap to north at every intersection.
- **Applied the same reorder to `carFixChannel.resolveHeading()`** (Android
  Auto), which had the identical flaw at line 206.

Deliberately **not** done: mapping `0 → unknown` at the source. That would
break a driver genuinely heading due north. Demoting the reported heading below
movement costs nothing in that case, because movement measures ~0 anyway — this
is pinned by a test.

## 4. Risk & impact on existing functionality

Blast radius — every consumer of the changed code, enumerated by grep:

- **`shared/components/CarMarker.tsx`** (5 rider-app consumers):
  `rider-app/app/(tabs)/index.tsx` (nearby drivers), `ride-options.tsx`,
  `driver-arriving.tsx`, `driver-arrived.tsx`, `ride-in-progress.tsx`. All five
  render the same rotation path and were affected by the same bug; all five get
  the fix. No prop signatures changed, so no call site needed editing.
- **`driver-app/components/CarMarker.tsx`**: one consumer,
  `driver-app/app/driver/(tabs)/index.tsx`.
- **`shared/utils/vehicleTracking.ts`**: `selectBearing` is a **new** export;
  the existing exports (`bearingDegrees`, `distanceMeters`, `snapToRoute`,
  `shortestArcRotationTarget`) are untouched, so existing importers are
  unaffected. `bearingDegrees` is no longer imported directly by either
  CarMarker (it is called inside `selectBearing`), but remains exported and is
  still used by `rider-app/__tests__/vehicleTracking.test.ts`.
- **`driver-app/lib/androidAuto/carFixChannel.ts`**: consumers grepped —
  `useCarLocation`, `carLocationTask`, `utils/backgroundLocation.ts`,
  `carSurface.tsx`, and `register.ts`. `register.ts` reads only latitude and
  longitude for the SOS payload and never the bearing, so the emergency path is
  unchanged (verified by reading the call site).
- **Untouched**: backend, ride state machine, dispatch, money paths,
  insurance-period logic. No database write, no migration, no API change. The
  `heading` value transmitted over WS and recorded by `tripLocationRecorder` is
  the raw platform value and is **not** modified by this change — only the
  marker's rendered rotation is.

Behavioural risk worth naming: a **parked** car now holds its last
movement-derived bearing instead of adopting a reported heading. That is
deliberate and matches the tradeoff already accepted in
`2026-08-21-android-auto-car-heading.md` ("a parked car's heading is not
meaningful either way"). Any real movement ≥ 3 m re-derives it immediately.

## 5. User-experience effect

Driver- and rider-facing, visible mid-session to anyone with the map open: the
car icon now points the way the vehicle is actually travelling instead of
sitting at due north on east–west streets. No copy, flow, pricing, or ride-state
change. A driver mid-ride when the new build installs sees only a
correctly-oriented icon.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/utils/vehicleTracking.ts` | New pure `selectBearing()` + `BearingSource`/`BearingSelection` types | One testable home for the priority chain that was duplicated in two components |
| `shared/components/CarMarker.tsx` | Calls `selectBearing`; adds `hasMovementBearingRef`; heading-only effect no longer overrides a movement bearing | Rider-app marker had the identical bug |
| `driver-app/components/CarMarker.tsx` | Same as above | The reported surface |
| `driver-app/lib/androidAuto/carFixChannel.ts` | `resolveHeading` priority reordered: derived-from-movement now above reported GPS course | Same defect, independently, on the head-unit path |
| `rider-app/__tests__/vehicleTracking.test.ts` | +7 tests covering every `selectBearing` branch, incl. 2 explicit REGRESSION cases | The chain had zero test coverage before |
| `driver-app/lib/androidAuto/__tests__/carFixChannel.test.ts` | +4 tests incl. westbound-with-heading-0 and genuine-northbound | Same, for the Android Auto path |

## 7. Before / after

```ts
// Before — shared + driver CarMarker: reported heading outranks movement,
// and `heading >= 0` accepts Android's placeholder 0.
const validHeading =
    heading != null && Number.isFinite(heading) && heading >= 0 ? heading : null;
let bearing: number | null = null;
if (snap && movedM >= MIN_BEARING_MOVE_M) {
    bearing = snap.bearing;
} else if (validHeading != null) {
    bearing = validHeading;              // ← junk 0 wins; car points north
} else if (movedM >= MIN_BEARING_MOVE_M) {
    bearing = bearingDegrees(...);       // ← never reached while driving
}
```

```ts
// After — movement outranks the reported heading; one shared implementation.
const { bearing, source } = selectBearing({
    snap, movedMeters: movedM, from: prevTargetRef.current, to: target,
    heading, hasMovementBearing: hasMovementBearingRef.current,
    minMoveMeters: MIN_BEARING_MOVE_M,
});
prevTargetRef.current = target;
if (bearing != null) {
    if (source === 'route' || source === 'travel') hasMovementBearingRef.current = true;
    animateRotationTo(bearing, duration);
}
```

```ts
// Before — carFixChannel.resolveHeading: GPS course checked first
if (typeof own === 'number' && Number.isFinite(own) && own >= 0) {
  return { fix: next, source: 'gps' };          // a 0 short-circuits the derive below
}
if (prev && ...moved >= MIN_COURSE_MOVE_M) { ...derived... }

// After — movement first, reported course only when there is nothing better
if (prev && ...moved >= MIN_COURSE_MOVE_M) { ...derived... }
if (typeof own === 'number' && Number.isFinite(own) && own >= 0) {
  return { fix: next, source: 'gps' };
}
```

## 8. Rollback plan

Pure client-side rendering change: no migration, no persisted data, no server
component, no change to any value written to the database or sent over the
wire. `git revert` **is** a valid rollback here — reverting the branch's commits
and shipping the previous mobile build restores the old behaviour exactly, and
no live data has been altered that would need repair. Installed builds keep
their current behaviour until they update, so there is no half-state between
clients.

No feature flag was added. The change replaces incorrect rendering wholesale
rather than adding new UX, and the `app_settings` flag mechanism has no existing
client-side switch for marker rendering (same reasoning recorded in the
2026-08-28 smoothing log). The worst failure mode of the new code is a
differently-rotated icon — it cannot produce a wrong position, price, or ride
state.

## 9. Verification performed

- `driver-app`: `npx tsc --noEmit` **clean**. Jest `lib/androidAuto` +
  `__tests__/app/driverDashboardScreen.test.tsx` + `hooks/__tests__` —
  **24 suites, 317 tests, all passing** (34 in `carFixChannel.test.ts`).
- `rider-app`: `npx tsc --noEmit` **clean**. Jest `vehicleTracking` +
  all four CarMarker-consumer screen suites + `homeScreen` + `rideOptionsScreen`
  — **6 suites, 310 tests, all passing** (19 in `vehicleTracking.test.ts`).
- `eslint` clean on every changed file. (`shared/` files report "outside of base
  path" under both apps' configs — pre-existing, not introduced here.)
- **The new tests were confirmed to FAIL against the pre-fix code.** The
  `carFixChannel.ts` fix was reverted to `HEAD` and the suite re-run: 2 failed /
  32 passed, then restored. These are genuine regression tests, not tests
  written to match whatever the code already did.
- `rider-app` production bundle: `npx expo export --platform web` — the closest
  available production-bundling check in this environment.

## What was NOT verified

- **No on-device or emulator run.** rider-app and driver-app have no automated
  visual/snapshot regression tooling (standing gap, CLAUDE.md gate #6), and this
  environment has no emulator and cannot produce an iOS/Android binary. No EAS
  production build was run.
- **The Android `getBearing()` → `heading: 0` mapping is reasoned from the
  Android/expo-location API contract, not read from source** — `node_modules`
  is not installed in this checkout, so expo-location's native code could not be
  inspected. The fix does not depend on that mapping being exactly right: it
  makes movement authoritative regardless of what the provider reports, so it
  holds whether the placeholder is `0`, `-1`, or `null`.
- **A second candidate cause is not eliminated.** `app.config.ts:19` sets
  `newArchEnabled: true`; if react-native-maps 1.27.2 does not propagate the
  animated non-style `rotation` prop under Fabric/Bridgeless, the marker would
  stay frozen at its initial angle (also 0 = north) *even with this fix*. The
  2026-08-28 log flagged the same risk as verified against library source but
  **not on device**. Discriminator for the next test drive: if the icon never
  changes angle in ANY state — including on-route mid-trip — the rotation prop
  is the problem and this fix will not resolve it.
- On-device confirmation is the outstanding gate. Suggested drive: one Android
  device, online-but-idle on an east–west street (the worst case, no route
  snapping), then the same street mid-trip.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed (all 5 rider-app consumers named)
- [x] UX field filled in for a behaviour change on an already-shipped screen
- [x] Regression tests proven to fail before the fix
