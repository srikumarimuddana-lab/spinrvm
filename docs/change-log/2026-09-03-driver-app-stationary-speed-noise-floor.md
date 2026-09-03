# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude, at user request — "there is a speed meter when the driver is online but stationary it displays 8km/h" |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/admin-portal-heatmaps-audit-gm8fbn` |
| Related issue or gap ID | User-reported live behavior; no issue filed |

## 1. Issue / gap identified

The driver-app's on-map speed chip (bottom of the map, shown while online) displayed `8 km/h` while the driver's vehicle was genuinely stationary.

## 2. Root cause

The speed chip's visibility rule (`app/driver/(tabs)/index.tsx`) only hid itself below `1.5 m/s` (~5.4 km/h): `isOnline && (location.coords.speed ?? 0) >= 1.5`. `coords.speed` is a GPS/Doppler-derived reading, not a wheel-speed sensor, and is well known to be noisy near zero — degraded sky view (dashboard mount, urban canyon, parked under a structure) can produce a non-zero apparent speed on a genuinely motionless vehicle. The reported 8 km/h (≈2.22 m/s) is above the old 1.5 m/s floor, so it passed the filter and displayed as if the vehicle were moving.

## 3. Fix / remediation

Raised the floor to a new named constant, `MIN_DISPLAYED_SPEED_MPS = 3` (≈10.8 km/h), added to `utils/locationDisplayGate.ts` (the existing shared file for this app's GPS-display-quality decisions — `MAX_DISPLAY_ACCURACY_M`, `shouldDisplayFix`, the follow-camera zoom tiers all already live there) rather than as a second magic number in the screen file. 3 m/s clears the reported 8 km/h false reading with real margin (a further ~35%), while staying well below any real driving speed (a driver actually pulling away is shown the chip almost immediately — even slow residential driving is ~15-20 km/h / 4.2-5.6 m/s).

## 4. Risk & impact on existing functionality

- **Blast radius**: one named constant, one call site (`index.tsx`'s speed-chip visibility condition). Grepped for other consumers of the old inline `1.5` literal — none; it was local to this one condition. `MIN_DISPLAYED_SPEED_MPS` does not affect `FOLLOW_ZOOM_TIERS`/`zoomTierForSpeed` (the follow-camera zoom-tier logic in the same file) — that has its own, separate 2 m/s "stopped" boundary with its own hysteresis, untouched here.
- No change to the underlying location stack, GPS sampling rate, or what's captured/stored for trip records — this only changes when the driver-facing *readout* is shown, a presentation-only decision.
- Purely a threshold increase in the "hide" direction — cannot cause the chip to show when it previously wouldn't have; only makes it hide in cases (any reading between old 1.5 m/s and new 3 m/s) that used to display.

## 5. User-experience effect

Driver-facing. The speed chip no longer shows a phantom reading while genuinely parked/stationary. A driver actually moving still sees their speed appear promptly (the new floor is far below normal driving speed).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/utils/locationDisplayGate.ts` | Added `MIN_DISPLAYED_SPEED_MPS = 3` constant with reasoning comment | Shared, documented, testable location for this app's GPS-display-quality thresholds |
| `driver-app/app/driver/(tabs)/index.tsx` | Speed-chip visibility condition: `>= 1.5` → `>= MIN_DISPLAYED_SPEED_MPS` | Fix the reported stationary-8km/h false reading |
| `driver-app/utils/__tests__/locationDisplayGate.test.ts` | Added 2 tests asserting the constant clears the reported false-positive with margin and stays well below real driving speed | Regression coverage for the specific reported value |

## 7. Before / after

```tsx
// Before — 1.5 m/s (~5.4 km/h) floor, below the reported 2.22 m/s (8 km/h) false reading
{isOnline && (location.coords.speed ?? 0) >= 1.5 && (
  <View style={[styles.speedChip, ...]}>...</View>
)}

// After — named, documented constant above the reported false reading
{isOnline && (location.coords.speed ?? 0) >= MIN_DISPLAYED_SPEED_MPS && (
  <View style={[styles.speedChip, ...]}>...</View>
)}
```

## 8. Rollback plan

Plain `git revert` — no data, no migration, no API change. A pure presentation-threshold constant with no other consumers.

## 9. Verification performed

- [x] Traced the exact reported symptom (8 km/h ≈ 2.22 m/s) against the old threshold (1.5 m/s) to confirm the reading would in fact have passed the old filter, before concluding this was the actual bug rather than something else.
- [x] Grepped this codebase for existing GPS-noise-magnitude precedent (`utils/sensorIntegrity.ts`'s `SPEED_REQUIRING_MOVEMENT = 5` m/s for a related-but-different anti-spoofing check, `lib/androidAuto/carFixChannel.ts`'s `MIN_COURSE_MOVE_M = 10` for bearing-noise) to ground the new constant's magnitude in this app's own established sense of GPS noise, rather than picking an arbitrary number.
- [x] Grepped for other consumers of the old inline `1.5` literal and of `locationDisplayGate.ts`'s other exports — confirmed isolated, no collision with the separate follow-zoom-tier speed boundaries in the same file.
- [x] `tsc --noEmit` — clean.
- [x] Added regression tests to the existing `locationDisplayGate.test.ts` suite (2 new tests) and manually verified their arithmetic by hand (3 > 8/3.6=2.22 ✓; 3 < 20/3.6=5.56 ✓) since they could not be executed in this sandbox — see below.

## What was NOT verified

- **Could not run the driver-app Jest suite in this sandbox** — `npx jest` fails on every test file (not just the ones touched here) with a pre-existing, unrelated `TypeError: _lruCache is not a constructor` from `babel-preset-expo`/`@react-native/jest-preset` — a sandbox tooling issue, not something introduced by this change. The new tests' correctness was confirmed by hand-checking the arithmetic instead of by running them; they should be re-run in a working CI/local environment before merge to confirm they actually pass under Jest.
- **No live GPS/device reproduction** — the fix is grounded in the exact reported value (8 km/h) and this app's own established GPS-noise-magnitude precedent, not a live test against real hardware in a genuinely degraded-signal parking spot.
- **A single higher threshold does not eliminate GPS speed noise, only raises the false-positive boundary** — a sufficiently bad fix (heavy multipath, stacked structure) could in principle still produce an apparent speed above the new 3 m/s floor. A more robust fix (e.g., requiring several consecutive readings above the floor, or folding in `speedAccuracy` where the platform reports it) was considered but not implemented — this app's location objects don't currently surface `speedAccuracy` anywhere, and a debounce/hysteresis scheme was judged more complexity than this specific reported bug (a single reproducible 8 km/h reading) warranted. Worth revisiting if phantom readings recur above the new floor.
