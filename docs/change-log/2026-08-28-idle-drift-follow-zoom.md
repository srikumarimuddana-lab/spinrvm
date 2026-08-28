# Change Impact & Risk Log — idle-online marker drift fix + speed-adaptive follow camera

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | vikas@ngitservices.com (via Claude Code session) |
| Surface(s) | driver-app (rider map benefits indirectly via the gated WS feed) |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/map-vehicle-tracking-animation-3e85y2` (round 2, post-#4652) |
| Related issue or gap ID | Live-testing screenshots 2026-08-28 08:24–08:25 (Albert St): car a block off-road, sliding right while online-idle |

## 1. Issue / gap identified

While a driver cruised **online without a ride**, the car marker rendered off the road and slid sideways. There is also no way to see street detail (turn restrictions, one-ways) when slowing/stopping without manual zooming.

## 2. Root cause

Idle-online tracking used `Location.Accuracy.Balanced` (cell/Wi-Fi positioning, ±30–100 m) at 10 s/30 m, and the driver UI re-rendered the marker at most every 10 s. No gate rejected poor-accuracy fixes, and the driver camera never followed the car. Ride phases were unaffected (High accuracy + route snapping), which is why the same drive looked correct once framed as a trip.

## 3. Fix / remediation

- Idle/offer phases: `High` accuracy at 4 s/10 m; idle render throttle 10 s → 3.5 s.
- New display accuracy gate (`driver-app/utils/locationDisplayGate.ts`): fixes with reported accuracy > 50 m don't move the marker or feed the live WS position (the same send feeds the rider map — one client gate fixes both ends, no backend change). A 30 s stale override guarantees the marker never freezes; durable trip capture stays unconditional (capture-before-filter preserved).
- Idle follow-car camera with speed-adaptive zoom (17.5 stopped / 16.75 city / 16 arterial, hysteresis at boundaries). Pan or hotspot tap hands control to the driver; recenter resumes follow. Zero API cost — vector-map detail only.

## 4. Risk & impact on existing functionality

- **Blast radius**: driver-app only (`useDriverDashboard.ts`, dashboard screen, new pure util). Backend, rider-app, state machine, money paths untouched. WS message *rate* while idle rises with the 4 s cadence (was 10 s) — still ephemeral `durable:false` markers; the ~60 s durable idle breadcrumb cadence is unchanged, so `driver_location_history` volume is unchanged.
- **Battery**: GNSS active while online-idle (was Balanced). This is the industry-standard trade (Uber/Lyft drivers run full GPS while online); cost only accrues while the driver chooses to be online.
- **Dispatch visibility**: the gate could hide a driver only if *every* fix is >50 m for >30 s — then the override shows the rough fix anyway; dispatch reads DB-side positions written by the ungated durable paths, so matching is unaffected.
- Follow camera is idle-only; ride-phase framing, heatmap interactions, and Android Auto surface untouched.

## 5. User-experience effect

Driver: online-idle map now matches trip quality — car stays on the road, camera follows with context-appropriate zoom, panning is respected. Rider (pre-booking nearby-driver markers and arriving-car view): no more block-off positions relayed from idle drivers. Visible immediately via OTA on next app launch after merge.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `driver-app/utils/locationDisplayGate.ts` | new — pure gate + zoom-tier logic | testable decisions |
| `driver-app/utils/__tests__/locationDisplayGate.test.ts` | new — 8 tests | gate/hysteresis behavior locked |
| `driver-app/hooks/useDriverDashboard.ts` | High-accuracy idle config, accuracy gate, 3.5 s render throttle | drift root causes |
| `driver-app/app/driver/(tabs)/index.tsx` | follow camera + speed zoom, pan/hotspot pause, recenter resume | street detail on demand |

## 7. Before/after snippet

```ts
// Before — idle tracking (cell/Wi-Fi grade) and no display gate:
idle: { timeInterval: 10_000, distanceInterval: 30, accuracy: Location.Accuracy.Balanced },
...
if (!integrity.trusted) { ... return; }
locationRef.current = loc;             // any fix, however inaccurate, moved the marker

// After — GNSS while online + accuracy gate with freeze-proof override:
idle: { timeInterval: 4_000, distanceInterval: 10, accuracy: Location.Accuracy.High },
...
if (!integrity.trusted) { ... return; }
if (!shouldDisplayFix(loc.coords.accuracy, Date.now() - lastDisplayedFixMsRef.current)) return;
lastDisplayedFixMsRef.current = Date.now();
```

## 8. Rollback plan

`git revert` of the two commits — client-only, no data mutated, no schema/flag involved. Battery-only concern can be rolled back independently by reverting the `LOCATION_CONFIGS` lines.

## 9. Verification performed

- New unit tests: 8 for gate + zoom hysteresis, green.
- driver-app: full affected suites (`driverDashboardScreen`, `hooks/__tests__`, new util) — 110 tests green; `tsc --noEmit` clean; eslint — my files clean, `useDriverDashboard.ts` error/warning counts byte-identical before vs after (6/4, all pre-existing).

## 10. What was NOT verified

- No on-device run (no emulator here; no visual-regression tooling for driver-app — standing gap). The follow-camera feel, zoom tiers, and battery impact need the next real drive; tier speeds (2 / 9 m/s) and zooms (17.5/16.75/16) are tunable constants in `locationDisplayGate.ts` if the road feel says otherwise.
- `animateCamera` zoom on iOS Apple Maps reasoned from react-native-maps support, not device-verified.
