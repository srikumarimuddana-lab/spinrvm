# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | Live-testing report: "whenever user goes offline zoom out and icon stay still facing towards the top side of the screen. when online zoom in and still face the icon towards the upper side of the screen." Fix #5 of the driver-app map/camera overhaul sequence (fix #1 heatmap #5272, fix #2 speed staleness #5273, fix #3 marker accuracy #5275, fix #4 zoom-tier noise floor #5278, all merged). |

## 1. Issue / gap identified

Going online or offline on the driver dashboard never explicitly re-frames the follow camera. The map is left showing whatever zoom/heading the follow-camera effect last applied — possibly a driving-speed zoom tier or a mid-turn heading — instead of a deliberate "zoomed out while parked, zoomed back in when active" framing.

## 2. Root cause

`rideState` stays `'idle'` across the online↔offline toggle — only the `isOnline` flag changes. Two existing effects in `index.tsx` could in principle re-frame the camera, but neither is wired to this specific transition:

- The rideState-keyed recenter (`useEffect(..., [rideState, _hasRidePolyline])`) only fires on a `rideState` change, which this toggle never causes.
- The follow-camera effect (`useEffect(..., [location, rideState, mapRef, courseUp])`) only re-runs when a fresh `location` fix arrives. Going offline halts `watchPositionAsync` entirely (`useDriverDashboard.ts`'s location-subscription effect: `if (!isOnline) return`), so no new fix ever arrives to trigger it — the camera simply freezes at its last computed framing.

Net effect: nothing has ever explicitly zoomed out on going offline or zoomed back in on returning online; the "framing" a driver sees is just an accident of whatever the follow camera happened to be doing the moment before the toggle.

## 3. Fix / remediation

Added a new effect in `index.tsx`, keyed specifically on `isOnline`, that fires exactly once per online↔offline transition (using a dedicated `prevIsOnlineForCameraRef`, the same previous-value-tracking pattern already used by the existing `mapKey` remount effect):

```tsx
const OFFLINE_IDLE_ZOOM = 14; // zoomed out — neighbourhood context while parked
const prevIsOnlineForCameraRef = useRef(isOnline);
useEffect(() => {
  if (isOnline === prevIsOnlineForCameraRef.current) return;
  const c = location?.coords;
  if (!c || !mapRef.current) return; // retried on the next location tick
  prevIsOnlineForCameraRef.current = isOnline;
  followZoomTierRef.current = null; // re-derive fresh once fixes resume
  const zoom = isOnline ? FOLLOW_ZOOM_TIERS[0].zoom : OFFLINE_IDLE_ZOOM;
  const heading = courseUp && camBearingRef.current != null ? camBearingRef.current : 0;
  const center = markerPosRef.current ?? { latitude: c.latitude, longitude: c.longitude };
  mapRef.current.animateCamera({ center, zoom, heading }, { duration: 600 });
}, [isOnline, location, courseUp, mapRef]);
```

- **Offline → zoom out** to a fixed, more zoomed-out level (`OFFLINE_IDLE_ZOOM = 14`, below even `FOLLOW_ZOOM_TIERS`' most-zoomed-out "highway" tier of 16).
- **Online → zoom in** to `FOLLOW_ZOOM_TIERS[0].zoom` (17.5, the same "stopped/idle" tier the follow camera already uses at rest), so there's no visible re-zoom jump the instant the first post-online GPS fix arrives and the follow-camera effect takes back over.
- **Heading** uses the exact same rule the follow camera and the compass button already use (`courseUp && camBearingRef.current != null ? camBearingRef.current : 0`) rather than hard-coding north. When course-up is on (the default), this keeps the car icon pointing toward the top of the screen — matching the ask literally — because the map rotates to the marker's own last bearing rather than to true north, which would only coincidentally point "up." If the driver has toggled to north-up, this respects that choice instead of overriding it.
- **Race guard**: if no location fix is available yet at the moment of the flip (e.g. a fresh app launch before the first fix), the effect does nothing and leaves `prevIsOnlineForCameraRef` unset for this transition, so it retries automatically the next time `location` changes (a dependency of this same effect) rather than silently missing the transition.
- Resets `followZoomTierRef.current` to `null` (mirroring `onRecenter`'s existing convention) so the follow-camera effect re-derives its zoom tier fresh from the next real fix instead of comparing against a stale pre-transition tier.

The compass button's own toggle behavior (`onToggleCourseUp`) was reviewed and left unchanged — it already does exactly what was asked ("press for default, press again to revert to previous direction"): it flips `courseUp` between north-up (`heading: 0`) and course-up (`heading: camBearingRef.current`), always using the marker's live bearing so a second press restores the actual current direction of travel, not a stale cached one.

## 4. Risk & impact on existing functionality

- **Blast radius**: this is a new, self-contained effect in `index.tsx` — the driver dashboard's own screen file. It reads existing refs/state (`mapRef`, `location`, `courseUp`, `camBearingRef`, `markerPosRef`, `followZoomTierRef`) but writes only to `mapRef.current.animateCamera` (a side effect, not shared state) and to `followZoomTierRef.current` (already written by the follow-camera effect and `onRecenter`; resetting it to `null` is the same reset those call sites already perform). No shared hook, component, or other screen reads or writes anything this effect touches beyond what already existed.
- **Does not touch the follow-camera effect, the compass toggle, or `onRecenter`** — those are unchanged; this only adds a new, independent trigger for the same `mapRef.current.animateCamera` call surface they already use.
- **Idempotent per transition**: the `prevIsOnlineForCameraRef` guard ensures this fires at most once per actual `isOnline` flip, not on every unrelated re-render — verified by a dedicated test (see §9).
- **No interaction with ride state, dispatch, or payments** — `isOnline` here is the existing driver-toggled online/offline flag; this fix only changes camera framing in response to it, not any online/offline business logic.

## 5. User-experience effect

**Driver-facing.** Going offline should now visibly zoom the map out and settle it; going back online should zoom back in to the same framing the follow camera uses at rest. The car icon's on-screen orientation (pointing toward the top when course-up is on) is preserved through both transitions, matching the live-testing ask.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/index.tsx` | Added a new `useEffect` keyed on `isOnline` that explicitly zooms the follow camera out on going offline and back in on returning online, using the same heading rule as the existing follow camera/compass toggle | Neither existing camera effect was ever wired to the online↔offline transition itself — see §2 |
| `driver-app/__tests__/app/driverDashboardScreen.test.tsx` | Added a new `describe('offline<->online camera framing')` block: 3 tests — zooms out on going offline, zooms back in on returning online, does not fire on an unrelated re-render | Proves the new effect's exact trigger condition and camera parameters against the existing real-component test harness (mocked `react-native-maps`, real `index.tsx`) |

## 7. Before / after

```tsx
// Before — no effect reacted to isOnline at all; the camera stayed wherever
// the follow-camera effect (location-fix-triggered) last left it, and going
// offline stops new fixes from arriving, so it never moved again.
```

```tsx
// After — an explicit, one-shot transition on isOnline itself
const prevIsOnlineForCameraRef = useRef(isOnline);
useEffect(() => {
  if (isOnline === prevIsOnlineForCameraRef.current) return;
  const c = location?.coords;
  if (!c || !mapRef.current) return;
  prevIsOnlineForCameraRef.current = isOnline;
  followZoomTierRef.current = null;
  const zoom = isOnline ? FOLLOW_ZOOM_TIERS[0].zoom : OFFLINE_IDLE_ZOOM;
  const heading = courseUp && camBearingRef.current != null ? camBearingRef.current : 0;
  const center = markerPosRef.current ?? { latitude: c.latitude, longitude: c.longitude };
  mapRef.current.animateCamera({ center, zoom, heading }, { duration: 600 });
}, [isOnline, location, courseUp, mapRef]);
```

## 8. Rollback plan

`git-revert-safe` — pure client-side camera-framing addition. No data written anywhere, no schema/API change, no shared state mutated beyond a ref (`followZoomTierRef`) that other effects already reset in the same way.

## 9. Verification performed

- [x] New unit/integration tests: `driver-app/__tests__/app/driverDashboardScreen.test.tsx` — added 3 tests in a new `describe('offline<->online camera framing')` block, all passing (61/61 total in the file, up from 58).
- [x] Full driver-app suite: `npx jest` — 145/145 suites, 1644/1644 tests passing. One suite (`backgroundMessaging.android.test.ts`) failed on the first full-suite run after this change; confirmed via an isolated re-run (20/20 passing) and a second full-suite run (145/145 suites, 1644/1644 tests passing) that this was an unrelated flake, not a regression — that file is untouched by this change.
- [x] `npx tsc --noEmit` clean on driver-app. A real production build (`expo export` / EAS build) was **not** run — this is a JS/TS logic-only change with no native dependency, Expo SDK, or native module involved.
- [x] Blast-radius grep performed: confirmed no other file reads or writes `prevIsOnlineForCameraRef` (new, local to this component) or calls `mapRef.current.animateCamera` outside `index.tsx`'s own effects (the pre-existing follow-camera effect, `onRecenter`, and the compass toggle).
- [x] Reviewed the compass button's existing `onToggleCourseUp` handler against the user's stated requirement ("press for default, press again to revert to previous direction") — confirmed it already does this correctly via `camBearingRef`; left unchanged.

**What was NOT verified:** the actual on-device visual result — this environment cannot drive a real device through an online/offline toggle to confirm the zoom-out/zoom-in animation reads as intended, or that 600ms/zoom-14 are the "right" feel (chosen to be clearly more zoomed-out than any driving tier, and to land exactly on the existing stopped-tier zoom on return, but the specific numbers are a judgment call, not a measured requirement). No visual-regression tooling exists for driver-app (per CLAUDE.md), so this was reasoned about and tested at the effect/parameter level, not screenshotted. The "icon travels/drifts while stopped" and "vehicle facing east" symptoms from the original report are separate, already-tracked items (partially addressed by fix #3's accuracy damping; the heading-reset mechanism remains open) — this fix only addresses the explicit zoom-in/zoom-out camera framing on the online/offline transition itself.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-layer cleanup needed)
- [x] Blast radius is stated, not assumed (grepped, listed in §4 — one new effect, no shared consumers beyond refs other same-file effects already write)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 states the only visible effect: explicit zoom out/in on the online/offline toggle, heading behavior preserved)
