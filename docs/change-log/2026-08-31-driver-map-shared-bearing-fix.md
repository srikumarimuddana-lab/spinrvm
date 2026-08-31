# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude (session on behalf of vikas@ngitservices.com) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/map-vehicle-tracking-animation-3e85y2`) |
| Related issue or gap ID | Live-testing report, 2026-08-31: "the map orientation on the phone does not set to North and icon transition is still an issue" |

## 1. Issue / gap identified

On the driver dashboard map, in course-up mode: (a) the map's rotation didn't reliably reflect true travel direction, and (b) the car icon's own rotation visibly lagged/snapped relative to the map — reported together as one live-testing note.

## 2. Root cause

Two **independently-computed bearings** drove the map camera and the car icon:

- **Camera heading** (`index.tsx`'s follow-camera effect): a straight chord between the last two raw GPS fixes, only once ≥8m apart, with zero delay.
- **Car icon rotation** (`CarMarker.tsx`'s playback ticker): a Catmull-Rom spline tangent through a buffer that deliberately renders `PLAYBACK_DELAY_MS` = 5 seconds in the past (for glide smoothness), route-snap-aware, with a 3m movement threshold.

At normal driving speed these two numbers disagree by construction — different lag (0s vs 5s), different thresholds (8m vs 3m), different smoothing (raw chord vs spline). Through any turn the camera has already rotated to the new heading while the icon is still finishing the old one 5 seconds behind, so the icon doesn't point "up" on the course-up map even while the camera is nominally tracking the same vehicle. That single mismatch explains both halves of the report: the map "not orienting correctly" and the icon "transitioning" oddly.

A secondary bug in the same area: `onToggleCourseUp`'s handler only acted immediately in the course-up→north-up direction, and neither direction re-armed `followRef.current` (set `false` by any touch/drag on the map, per `onPanDrag`). Once a driver had panned the map even once, the compass toggle could silently do nothing in either direction until the driver also tapped the separate "recenter" button, since the follow-camera effect exits early on that guard.

## 3. Fix / remediation

- Added an `onBearingChange?: (bearing: number) => void` prop to `CarMarker`, fired every playback tick alongside the bearing it applies to the icon's own rotation (same value, same tick — not a second computation).
- `index.tsx`'s follow-camera effect no longer computes its own bearing from raw GPS deltas (removed the `camPrevRef`/`bearingDegrees` chord calc and the now-unused `bearingDegrees` import). It now reads `camBearingRef`, populated only by `CarMarker`'s `onBearingChange` callback (via a stable ref-only `useCallback`, so it can't cause re-renders or go stale under `React.memo`).
- `onToggleCourseUp` now always sets `followRef.current = true` on tap (re-arming follow after any prior pan), and applies the toggle immediately in both directions using the same shared bearing — not just the north-up direction, and not waiting for the next GPS tick.

## 4. Risk & impact on existing functionality

- **Blast radius:** `driver-app/components/CarMarker.tsx` and `driver-app/app/driver/(tabs)/index.tsx` only. Grepped for other consumers of `CarMarker`: `driver-app`'s own dashboard is the only call site that now passes `onBearingChange`; the prop is optional and additive, so every other caller of `CarMarker` (rider-app's own copy is a separate file, untouched) is unaffected by its addition. `_propsAreEqual` (the `React.memo` comparator) deliberately does not compare `onBearingChange`, matching how it already doesn't compare other callback-shaped props — no risk of stale-callback bugs since the parent's callback is a referentially-stable ref-only closure.
- No change to ride state, dispatch, fares, or any backend-reachable path — this is a client-side map-rendering fix only.
- The camera's underlying rotation/zoom/recenter mechanics (speed-adaptive zoom tiers, "pin the car low" center offset, the `COURSE_UP_RIDE_STATES` gating) are all unchanged — only the *source* of the bearing number feeding them changed.

## 5. User-experience effect

- **Driver-facing.** The course-up map camera and the car icon now always agree on "forward" — the icon should visibly point toward the top of the screen while course-up is active, instead of periodically lagging/pointing the wrong way through turns. The compass toggle button now reliably straightens/rotates the map immediately on tap, in both directions, even after the driver has panned the map.
- Visible continuously while online (course-up is the default), most noticeably during turns and immediately after tapping the compass toggle.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/CarMarker.tsx` | Added `onBearingChange` prop, fired from the existing ticker at the point a bearing is selected | Expose the marker's own bearing as the single source of truth |
| `driver-app/app/driver/(tabs)/index.tsx` | Removed the independent camera bearing calc; consumes `CarMarker`'s bearing instead; `onToggleCourseUp` re-arms `followRef` and acts immediately in both directions; removed now-unused `bearingDegrees` import | Eliminate the two-bearing mismatch; fix the toggle's dead-after-pan bug |
| `driver-app/__tests__/components/CarMarker.test.tsx` | Added a test asserting `onBearingChange` fires with the exact bearing the ticker applies; made the `markerPlayback.playbackPosition` mock a `jest.fn()` so tests can drive the ticker's bearing-selection path | Regression coverage for the new callback |

## 7. Before / after

```tsx
// Before (index.tsx) — camera computes its own bearing
const prev = camPrevRef.current;
if (prev && haversineMeters(prev.latitude, prev.longitude, c.latitude, c.longitude) >= 8) {
  camBearingRef.current = bearingDegrees(prev.latitude, prev.longitude, c.latitude, c.longitude);
}
camPrevRef.current = { latitude: c.latitude, longitude: c.longitude };
```
```tsx
// After — camera reads the same bearing CarMarker already applied to the icon
const handleMarkerBearingChange = useCallback((bearing: number) => {
  camBearingRef.current = bearing;
}, []);
// ...
<CarMarker ... onBearingChange={handleMarkerBearingChange} />
```

## 8. Rollback plan

`git revert` — client-side only, no data, no migration, no feature flag. Reverting restores the two-independent-bearing behavior (the pre-existing, if imperfect, state), not a crash.

## 9. Verification performed

- [x] `npx tsc --noEmit` (driver-app) — clean.
- [x] `npx eslint` on both changed source files — clean (0 errors, 0 warnings). The 2 warnings in the changed test file are pre-existing (`no-require-imports` inside the untouched `react-native-maps` mock factory), confirmed via `git diff` showing those lines unmodified.
- [x] Full driver-app suite: **127/127 suites, 1434/1434 tests passing** (1432 baseline + 2 new).
- [x] New regression test added and passing: `onBearingChange` fires with the exact bearing value the ticker selects and applies to the icon that same tick.
- [x] Blast-radius grep: confirmed no other call site passes or depends on the new prop; confirmed `bearingDegrees` has no other remaining usage in `index.tsx` before removing its import.
- [ ] Manual/on-device verification while actually driving — not performed; no device/emulator in this environment. Reasoned about via the exact tick-by-tick code path (playback buffer → `selectBearing` → `onBearingChange` → `camBearingRef` → `animateCamera`), not screenshotted; driver-app has no visual-regression tooling (standing gap, `ACTION_ITEMS.md`).

## 10. What was NOT verified

- No live-device confirmation that the icon now visibly stays "up" on a real course-up drive — this fix is verified at the unit/logic level (same value now feeds both consumers, by construction) but not observed live.
- The unrelated top-left icon visible in the reported screenshots (outside this app's own rendered UI, likely a screen-recording/compass overlay tool used for reference) was not investigated further — it isn't part of Spinr's codebase.
- rider-app's own `CarMarker.tsx` (a separate file) and any analogous follow-camera logic there were not touched — this fix is scoped to the driver-app map, which is what was reported.
