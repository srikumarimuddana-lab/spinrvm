# 2026-08-30 — Marker drift-through-a-stop + idle panel overlapping the marker

Round 5, filed within hours of round 4 (PR #4712) merging. Two field-verified
bugs from real driving with the round-4 build, both with screenshots.
Branch `claude/map-vehicle-tracking-animation-3e85y2`, restarted from main
after #4712.

## 1. Issue/gap identified

- **Marker drift-through-a-stop**: stopped at a red light, the car marker
  kept moving forward across the intersection, then snapped/reversed back
  to the true position once GPS caught up. Screenshots show the reversed
  marker position mid-correction.
- **UI overlap**: the "You're Online" pill and vehicle info card
  (`DriverIdlePanel`) sat directly on top of the car marker.

## 2. Root cause

- **Drift**: `useDriverDashboard`'s `watchPositionAsync` is configured with
  a `distanceInterval` (5–10 m) alongside `timeInterval`. On Android this is
  an AND, not an OR — `LocationManager` simply does not deliver a fix once
  the device has genuinely stopped, no matter how much time passes. The
  code already had a comment acknowledging this ("standstill under
  distanceInterval") with a 30 s watchdog — but that watchdog only runs
  during `TRACKED_TRIP_PHASES` (trip legs), not online-idle, which is what
  the report came from. With no fixes arriving, `markerPlayback`'s buffer
  ran dry and dead-reckoned forward along the last PRE-STOP segment's real
  velocity for up to 3 s (`MAX_EXTRAPOLATION_MS`), projecting the car
  through the intersection it was actually stopped at. The correction back
  to the true, stationary position read as a reverse-snap.
- **Overlap**: the course-up follow camera (round 3) intentionally pins the
  car in the lower third of the screen (nav-app framing) via a manual
  forward camera-center shift. Nothing accounted for `DriverIdlePanel` —
  vehicle pill, online pill, 100 dp GO/STOP button — occupying almost
  exactly that same lower third, bottom-anchored. The two were built
  independently and never reconciled.

## 3. Fix/remediation

- **Stationary heartbeat** (`useDriverDashboard.ts`): while online, the last
  known coordinate is re-emitted into `markerFixFeed` with a fresh
  timestamp every ~2.5 s whenever no real fix has arrived — a pure JS
  timer, zero GPS poll, zero network call. Covers every online state
  (idle, ride_offered, navigating_to_pickup, trip), unlike the trip-only
  30 s watchdog. This is the actual root-cause fix — it keeps the buffer's
  newest entry fresh so extrapolation is never needed at a genuine stop.
- **Speed-gated extrapolation** (`markerPlayback.ts`), defense in depth: dead-reckoning
  now requires the last real segment's speed to be ≥1.5 m/s
  (`MIN_EXTRAPOLATION_SPEED_MPS`) — a segment that was already
  slow/stopped when a gap began must hold, never coast forward.
  `MAX_EXTRAPOLATION_MS` cut 3000 → 1500 ms: with the heartbeat guaranteeing
  fresh data every ~2.5 s, a shorter cap bounds the worst case tightly.
- **Map padding** (`app/driver/(tabs)/index.tsx`): `MapView.mapPadding`
  reserves the idle panel's footprint (≈230 dp + `insets.bottom`) while
  `rideState === 'idle'` — the SDK-native mechanism for "a UI element
  obscures part of the map," honored by every camera/center calculation
  the map does, including the follow effect's own `animateCamera`. Zero
  padding in every other ride state, which don't render this panel.

## 4. Risk & impact on existing functionality

- `markerPlayback.playbackPosition` extrapolation behavior changes for
  every consumer of the shared engine (both apps, every car marker) — grep
  confirms the only consumers are `shared/components/CarMarker.tsx` and
  `driver-app/components/CarMarker.tsx`, both already covered by this PR's
  test updates. The new speed gate only SUPPRESSES extrapolation in more
  cases (never invents new motion); no behavior change for a genuinely
  moving car.
- The heartbeat only writes to the already-un-throttled `fixFeed` added in
  round 4; it does not touch `setLocation`, the durable trip recorder, or
  any backend call — zero interaction with dispatch, fare, or the ride
  state machine.
- `mapPadding` is scoped to `rideState === 'idle'` only, with explicit zero
  padding in every other state — the active-ride route-overview framing
  (`fitToCoordinates`-style calls elsewhere in the file, if any) is
  unaffected.
- `IDLE_PANEL_HEIGHT_DP` (230) is an estimate from `DriverIdlePanel.tsx`'s
  own styles, not measured on a device — see §10.

## 5. User experience effect

Driver-facing, visible mid-session after OTA: the marker should now hold
still at a genuine stop instead of drifting through it and correcting
backward, and should render clearly above the online/vehicle pills instead
of hidden behind them. No flow, copy, or money change.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `shared/utils/markerPlayback.ts` | min-speed extrapolation gate; cap 3000→1500ms | stop drift-through-a-stop |
| `rider-app/__tests__/markerPlayback.test.ts` | updated cap-dependent test; new speed-gate test | pin the new behavior |
| `driver-app/hooks/useDriverDashboard.ts` | stationary marker-feed heartbeat | close the actual GPS gap |
| `driver-app/app/driver/(tabs)/index.tsx` | `mapPadding` reserving the idle panel | stop the marker hiding under the panel |

## 7. Before/after snippet

Before (extrapolates any segment regardless of speed, 3 s cap):

```ts
if (segM >= MIN_SEGMENT_MOVE_M && speed <= MAX_PLAUSIBLE_SPEED_MPS) {
  // projects forward even from a segment where the car was already stopped
}
```

After (speed-gated, 1.5 s cap, backed by a heartbeat that keeps real data flowing):

```ts
if (segM >= MIN_SEGMENT_MOVE_M && speed <= MAX_PLAUSIBLE_SPEED_MPS && speed >= MIN_EXTRAPOLATION_SPEED_MPS) {
  // only continues a segment that was actually moving
}
// + useDriverDashboard: re-emits the last coordinate every ~2.5s when idle-stopped,
// so the buffer rarely needs to extrapolate at all
```

## 8. Rollback plan

All JS-only — `git revert` of these three commits + automatic OTA republish
on main push; applies on second app launch. No native build, no migration,
no live data touched. The three fixes are independently revertable if one
proves wrong in the field without needing to revert the others.

## 9. Verification performed

- `markerPlayback` unit tests: 15/15, including a new test that pins the
  min-speed gate directly (a ~0.5 m/s segment must hold, not extrapolate)
  and an updated cap test.
- `tsc --noEmit` clean on driver-app; eslint clean on every changed file
  (the pre-existing 6-error baseline in `useDriverDashboard.ts`, unchanged
  since round 4, verified again by exact line-count diff).
- `driverDashboardScreen.test.tsx`: 49/49 (this suite mocks
  `useDriverDashboard` entirely, so it does not exercise the heartbeat
  logic directly — see §10).
- Full jest suites both apps run at push time (results in PR).

## 10. What was NOT verified

- **No on-device testing** — this round exists precisely because round 4's
  reasoning-only verification missed these two bugs; the same limitation
  applies here. The heartbeat and mapPadding fixes are the two highest-
  confidence, most mechanically-verifiable changes available without a
  device (heartbeat: a JS timer with no external dependency; mapPadding:
  documented, cross-platform react-native-maps prop for exactly this
  problem), but neither is confirmed against a real GPS/map render.
- No hook-level unit test exists for `useDriverDashboard` (it's fully
  mocked in the screen test, matching the file's existing pattern) — the
  heartbeat logic is verified by type-checking, lint, and manual code
  review only, not a dedicated test.
- `IDLE_PANEL_HEIGHT_DP = 230` is a hand-estimate from `DriverIdlePanel`'s
  StyleSheet values, not a device measurement — if it's too small the
  overlap could partially recur; too large just wastes a bit of usable map
  area. Either is a one-line tune, not a redesign.
- No automated visual/snapshot regression tooling exists for either app
  (standing gap, `ACTION_ITEMS.md`).
