# 2026-08-29 — Playback-buffer car marker, course-up driver camera, OTA version label

Round 3 of the live-testing map/tracking work (rounds 1–2:
`2026-08-28-map-vehicle-tracking-smoothing.md`,
`2026-08-28-idle-drift-follow-zoom.md`). Branch
`claude/map-vehicle-tracking-animation-3e85y2`.

## 1. Issue/gap identified

Live testing (2026-08-29) reported three residual problems after rounds 1–2:
(a) the car marker "stays at one place and then jumps" — motion still syncs
in bursts instead of flowing; (b) the map never orients so the direction of
travel points up the screen ("bottom to upwards direction... as a user would
expect"); (c) it was impossible to tell on-device whether a JS fix had
actually arrived via OTA, so every field report risked testing a stale bundle.

## 2. Root cause

(a) The marker architecture animated **to each fix on arrival**: with a 2–4 s
GPS cadence plus render throttling, the animation finishes long before the
next fix arrives, so the car sits parked, then leaps. No amount of tuning the
animation duration fixes an architecture with no lookahead. (b) The idle
follow camera always framed north-up with the car centered — course-up was
never implemented. (c) expo-updates applies a downloaded OTA on the *second*
launch and nothing in the UI exposed the running bundle's identity.

## 3. Fix/remediation

- **Playback buffer + capped dead reckoning** (`shared/utils/markerPlayback.ts`,
  new; consumed by both `CarMarker` components): the marker renders the car
  `PLAYBACK_DELAY_MS = 5 s` in the past and animates continuously through a
  queue of timestamped fixes on a 500 ms ticker — the next point is already
  buffered, so motion never stalls. When the buffer runs dry it extrapolates
  along the last segment's velocity for at most 3 s (plausibility-guarded at
  60 m/s), then holds honestly. Buffer resets (one clean snap) on >500 m jumps.
- **Course-up driver camera** (driver dashboard + `MapControls`): while
  online-idle the map rotates so travel direction points up, car pinned in the
  lower third (center shifted ahead 18% of viewport height via WebMercator
  meters-per-pixel + great-circle `destinationPoint`). Bearing derives only
  from ≥8 m real movement between camera ticks (Android's placeholder
  heading 0 never rotates the map) and holds at stops. New compass button
  toggles course-up/north-up; default course-up. Rider app stays north-up.
- **OTA version label** (`shared/utils/otaVersion.ts`, new): Settings screens
  in both apps now show `OTA <8-char update id>` / "embedded build" so testers
  can verify which bundle they're running before reporting.
- **Test repair** (independent commit `b5cc679`): `loginScreen.test.tsx` was
  red on main pinning the removed 2026-08-20 consent checkbox; rewritten to
  pin the 2026-08-28 clickwrap contract. No product code changed.

## 4. Risk & impact on existing functionality

- `shared/components/CarMarker.tsx` consumers: rider `track-ride.tsx` +
  `driver-en-route` flows and driver dashboard — every live car marker on
  both apps changes movement behavior. The public props are unchanged; only
  internal animation architecture moved, and the round-1/2 behaviors it must
  preserve (selectBearing priority, route snapping at 35 m, shortest-arc
  rotation, accuracy display gate upstream) are all still applied per tick.
- The 5 s render delay is new and visible in principle: the marker shows
  where the car was 5 s ago (industry-standard technique; Uber/Lyft ship
  comparable delays). ETA text, dispatch, and trip recording are untouched —
  capture-before-filter still records raw fixes durably.
- Course-up effect only runs in `rideState === 'idle'` — active-ride camera
  framing, offer framing, and the ride state machine are untouched.
- `MapControls` is driver-app-only (grep: single importer, the dashboard);
  new props are optional so any other usage compiles unchanged.
- `otaVersionLabel()` lazy-requires expo-updates inside try/catch — cannot
  crash Settings even if the module is missing (dev client).
- Zero new API calls, network traffic, or backend changes anywhere in this
  round.

## 5. User experience effect

Driver + rider facing, visible mid-session after OTA: car markers glide
continuously instead of jumping (both apps); the driver idle map rotates
course-up with a new compass toggle (driver app only); Settings shows a
small bundle-identity line (both apps). No flow, copy, or money change.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `shared/utils/markerPlayback.ts` | new — playback buffer engine | continuous marker motion |
| `shared/utils/vehicleTracking.ts` | added `destinationPoint()` | forward-shifted course-up center |
| `shared/components/CarMarker.tsx` | animate-on-arrival → buffered 500 ms ticker | fix stall-then-jump |
| `driver-app/components/CarMarker.tsx` | mirrored identical conversion | same fix, driver copy |
| `driver-app/app/driver/(tabs)/index.tsx` | course-up follow camera + toggle wiring | bottom→up orientation |
| `driver-app/components/dashboard/MapControls.tsx` | compass toggle button (optional props) | driver control of rotation |
| `shared/utils/otaVersion.ts` | new — OTA bundle label helper | verifiable field testing |
| `driver-app/app/driver/settings.tsx`, `rider-app/app/settings.tsx` | show OTA label | same |
| `rider-app/__tests__/markerPlayback.test.ts` | new — 12 tests | pin engine behavior |
| `rider-app/__tests__/vehicleTracking.test.ts` | +3 `destinationPoint` tests | pin geometry |
| `rider-app/__tests__/settingsScreen.test.tsx` | assertion includes OTA label | keep suite green |
| `driver-app/__tests__/app/loginScreen.test.tsx` | checkbox pins → clickwrap pins | repair pre-existing red suite |

## 7. Before/after snippet

Before (CarMarker, per-fix animation — car waits between fixes):

```ts
// on each new coordinate prop:
animatedRegion.timing(nextFix, { duration: clamp(sinceLastFix) }).start();
// duration elapses before the next fix arrives → marker parks, then leaps
```

After (buffered playback — the target is always already known):

```ts
// fix ingest: pushFix(bufferRef.current, fix, Date.now())
// every TICK_MS=500ms:
const p = playbackPosition(bufferRef.current, Date.now() - PLAYBACK_DELAY_MS);
// p interpolates between buffered fixes (or dead-reckons ≤3s, then holds)
animatedRegion.timing(snapToRoute(p.coordinate), { duration: TICK_MS }).start();
```

## 8. Rollback plan

All JS-only — no native build, no migration, no backend change. Rollback is
`git revert` of the four commits + OTA republish (eas-build.yml publishes on
main push automatically); riders/drivers pick it up on second launch. The
course-up camera additionally has an in-app off switch (compass button) if a
driver dislikes it before any revert. No live data is touched, so revert is
a complete rollback.

## 9. Verification performed

- `rider-app`: full jest suite + `tsc --noEmit` (run at push time, results in
  PR); markerPlayback 12/12, vehicleTracking 22/22, settingsScreen 22/22.
- `driver-app`: `tsc --noEmit` clean; eslint clean on changed files;
  driverDashboardScreen 49/49; loginScreen 17/17 (was 8 red on main);
  full jest suite run at push time.
- Playback engine covered by direct unit tests: interpolation, extrapolation
  cap, stationary/implausible-segment guards, buffer pruning/reset.

## 10. What was NOT verified

- **No on-device testing** — no emulator/device in this environment. The 5 s
  delay feel, course-up rotation smoothness, and compass button placement
  are reasoned about, not observed; that is exactly what the new OTA label
  exists to make verifiable in the next field session.
- No automated visual/snapshot regression tooling exists for rider-app or
  driver-app (standing gap, `ACTION_ITEMS.md`), so marker/camera motion is
  asserted via unit tests on the math, not screenshots.
- `Dimensions.get('window')` course-up shift not tested across
  rotation/split-screen; worst case is a slightly off-center car.
- No real production build (`expo export`) was run for either app in this
  session — jest + tsc + eslint only.
