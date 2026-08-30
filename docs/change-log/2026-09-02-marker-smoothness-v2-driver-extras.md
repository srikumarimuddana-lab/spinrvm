# 2026-09-02 — Marker smoothness v2 (research-grounded) + four driver map extras

Round 4 of the live-testing map/tracking work (rounds 1–3:
`2026-08-28-map-vehicle-tracking-smoothing.md`,
`2026-08-28-idle-drift-follow-zoom.md`,
`2026-08-29-marker-playback-course-up-ota-label.md`). Branch
`claude/map-vehicle-tracking-animation-3e85y2`, restarted from main after
PR #4694 merged.

## 1. Issue/gap identified

Live testing after round 3 (2026-09-02): the car marker is **still** not
smooth — motion improved but remains visibly steppy/laggy. Separately, four
driver-app map features standard at ride-share incumbents were approved as
follow-ups: night map, speed display, arrival auto-detect, route-deviation
heads-up.

## 2. Root cause

Web research into what Uber/Lyft/Gojek/Grab actually ship (Uber patent
US11796328, Gojek's live-tracking engineering post, react-native-maps
issues #1765/#2382/#3913) plus code profiling identified four residual
causes, all real:

1. **Buffer starvation by our own render throttle** (primary). GPS fixes
   arrive every 2–4 s, but `useDriverDashboard` only pushed a location into
   React state every 3–3.5 s (`renderThrottleMs`), and CarMarker only saw
   the throttled stream — inter-fix spacing could beat up to ~7.5 s, past
   the 5 s playback delay + 3 s extrapolation cap. The buffer ran dry mid-
   glide: freeze, then jump-catch-up.
2. **Arrival-time stamping.** Fixes were stamped `Date.now()` at ingest, so
   throttle-bursty delivery distorted playback velocity.
3. **Default easing + fixed-duration segments.** Each 500 ms
   `AnimatedRegion.timing` used `Easing.inOut` — the car accelerated and
   braked twice per second; fixed durations over variable distances made
   velocity discontinuous at every joint (Gojek: per-segment duration is
   "the difference between a smooth and a choppy animation").
4. **JS-bridge animation on Android.** `AnimatedRegion` marches every frame
   over the bridge and is documented to degrade to 5–10 fps under load
   (react-native-maps#1765); the industry path is the native UI-thread
   animator (`animateMarkerToCoordinate`).

## 3. Fix/remediation

- **Un-throttled fix feed** (`shared/utils/fixFeed.ts`, new): a 20-line
  pub/sub channel; `useDriverDashboard` emits every display-gated fix (with
  real `loc.timestamp` + heading) and CarMarker ingests via subscription —
  zero re-renders either side. The dashboard render throttle stays but no
  longer gates the marker. Rider app unchanged (its WS path was never
  throttled).
- **Real measurement timestamps**: new `fixTimestampMs` prop / feed field;
  60 s sanity window falls back to arrival time on bad device clocks.
- **Lookahead linear segments**: each 500 ms tick now animates toward the
  playback position one tick in the FUTURE with `Easing.linear`, so
  animation velocity equals played-back ground speed by construction and
  segments join continuously.
- **Catmull-Rom spline sampling** (`markerPlayback.ts`): position AND
  bearing sampled from a cubic Hermite through the buffered fixes — corners
  rounded, heading rotates through turns instead of kinking; implausible
  (>60 m/s) neighbor segments disable the spline for that sample.
  `playbackPosition` also returns `speedMps` now.
- **Native Android animator**: Android renders a plain `Marker` driven by
  `animateMarkerToCoordinate` (UI-thread ValueAnimator), with a teleport
  guard — the coordinate prop re-syncs to each tick's target only after the
  animation window, so re-render prop-diffs are visual no-ops. iOS keeps
  `Marker.Animated` + `AnimatedRegion` (now linear). Fallback to direct
  prop sets if the native method is absent (new-arch interop builds).
- **Driver extras** (one commit each): Android night-map style
  (`customMapStyle` when dark — `userInterfaceStyle` is iOS-only); GPS
  speed chip while online and moving; arrival geofence (2 fixes within
  60 m while navigating to pickup auto-invoke the existing
  `arriveAtPickup` action — same client radius guard + backend state-machine
  validation as the manual tap, one attempt per ride); route-deviation
  heads-up (3 consecutive fixes >60 m off the polyline → rate-limited
  toast, detection only).

## 4. Risk & impact on existing functionality

- `CarMarker` consumers: driver dashboard + rider `ride-in-progress`,
  `driver-arriving`, `driver-arrived`, `ride-options`, `(tabs)/index`
  (nearby drivers). Public props are additive (`fixTimestampMs`,
  `fixFeed`); all rider call sites work unchanged via the prop path. The
  Android render path change (plain Marker + native animator) affects every
  marker on Android — the highest-risk piece; guarded by the post-window
  coordinate re-sync and by the fallback when the native method is missing.
- Arrival geofence touches ride-state **timing**, not the machine itself:
  it calls the same guarded store action as the button; the backend still
  enforces `_require_ride_in_state` and the pickup radius. Failure mode is
  a too-early "arrived" inside 60 m — bounded by the dwell requirement and
  the server-side radius check.
- `useDriverDashboard` gains a stable feed object; the throttle behavior
  for dashboard re-renders is unchanged.
- Zero new API calls, network traffic, or backend changes in this round.
- This file's pre-existing lint errors (6, e.g. `useRef(Date.now())`
  initializers) are untouched; the round adds none.

## 5. User experience effect

Driver + rider facing, visible mid-session after OTA: markers move at true
ground speed with rounded corners and no twice-a-second pulse (both apps;
biggest gain on Android). Driver-only: dark map at night, speed chip while
moving, automatic "arrived" near pickup (toast confirms), quiet off-route
toast. No copy, flow, or money change beyond wait-time now starting at
actual arrival when the geofence fires.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `shared/utils/markerPlayback.ts` | Catmull-Rom sampling + `speedMps` | smooth corners/heading |
| `rider-app/__tests__/markerPlayback.test.ts` | +3 tests (speed, spline) | pin engine v2 |
| `shared/components/CarMarker.tsx` | lookahead linear ticks, native Android path, `fixTimestampMs`/`fixFeed` | the 4 root causes |
| `driver-app/components/CarMarker.tsx` | mirrored (keeps `isOnline` compat prop, RN Image) | same |
| `shared/utils/fixFeed.ts` | new pub/sub fix channel | throttle bypass |
| `driver-app/hooks/useDriverDashboard.ts` | emit every displayed fix into `markerFixFeed` | starvation fix |
| `driver-app/app/driver/(tabs)/index.tsx` | feed wiring; speed chip; arrival geofence; off-route toast; dark style | extras |
| `driver-app/utils/mapStyles.ts` | new — Google night style JSON | Android night map |

## 7. Before/after snippet

Before (fixed-duration segment toward the PAST playback position, default
inOut easing, JS bridge on Android):

```ts
const p = playbackPosition(buffer, Date.now() - PLAYBACK_DELAY_MS);
animatedRegion.timing({ ...p.coordinate, duration: TICK_MS }).start();
// accelerates from 0 and brakes to 0 twice a second; Android: per-frame bridge
```

After (lookahead target, linear easing, native animator on Android):

```ts
const p = playbackPosition(buffer, Date.now() - PLAYBACK_DELAY_MS + TICK_MS);
// Android: UI-thread ValueAnimator
markerRef.current.animateMarkerToCoordinate(target, TICK_MS);
// iOS:
animatedRegion.timing({ ...target, duration: TICK_MS, easing: Easing.linear }).start();
// velocity == played-back ground speed; segments join continuously
```

## 8. Rollback plan

All JS-only — `git revert` of the round's commits + automatic OTA republish
on main push; applies on second app launch (verifiable via the round-3
Settings OTA label). The arrival geofence and off-route toast are isolated
effects revertable independently. No live data is touched; the geofence
writes only through the existing arrive endpoint.

## 9. Verification performed

- Engine: 17/17 `markerPlayback` + 22/22 `vehicleTracking` unit tests
  (interpolation, spline corner behavior, extrapolation caps, guards).
- `tsc --noEmit` clean on both apps; eslint clean on every changed file
  (pre-existing `useDriverDashboard` errors unchanged, verified by
  baseline diff).
- Full jest suites both apps run at push time (results in PR).
- Research findings verified against primary sources (Uber patent, Gojek
  engineering post, react-native-maps issue tracker) — cited in the PR.

## 10. What was NOT verified

- **No on-device testing** — the native `animateMarkerToCoordinate` path,
  the Android rotation stepping, and the overall motion feel are reasoned
  from documented behavior, not observed. This is the top thing to check in
  the next field session (Settings OTA label first). If the native method
  crashes or is missing on our Expo/new-arch build, the code falls back to
  stepped prop updates — degraded but never frozen.
- Arrival geofence not exercised against a real ride (no device); unit
  coverage is indirect via the store action's existing tests.
- No automated visual/snapshot tooling exists for either app (standing
  gap, `ACTION_ITEMS.md`).
