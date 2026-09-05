# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | vikas@ngitservices.com |
| Surface(s) | driver-app (new shared util, currently wired only into driver-app) |
| Domain (Sentry tag) | drivers |
| PR / commit link | (added at PR open) |
| Related issue or gap ID | User-requested research pass: "research and implement any new techniques available... which light weight yet stable and accurate" (2026-09-05 session), following live-testing bug reports of jumpy/laggy car-marker movement |

## 1. Issue / gap identified

The driver dashboard's car marker plays back GPS fixes through a spline-interpolated buffer (`markerPlayback.ts`) that smooths motion BETWEEN fixes, but each individual fix is used exactly as reported — a single noisy fix (multipath reflection, weak sky view) still bends the spline toward its own error rather than being damped.

## 2. Root cause

No stage in the pipeline treats a fix's own measurement noise as noise — `pushFix`/`playbackPosition` assume every buffered fix is the true position. This is a documented gap relative to how e.g. Uber's Beacon system pre-filters raw GPS with sensor fusion before it ever reaches the rendered marker.

## 3. Fix / remediation

Added `shared/utils/gpsSmoothing.ts` — a small (~150 line, comment-heavy), dependency-free 1-D Kalman/complementary filter (`smoothFix`), the same lightweight technique widely documented for smoothing consumer GPS traces: treats position as a random walk with a tuned process-noise constant and each fix as a noisy measurement, producing a damped running estimate. Wired into `driver-app/components/CarMarker.tsx`'s single `ingestFix` choke point (used by both the un-throttled `fixFeed` path and the coordinate-prop path) — every raw fix is smoothed before entering the existing playback buffer, rather than changing the buffer/spline logic itself. Re-seeds (drops the running estimate) whenever the existing `shouldResetBuffer` teleport-detection fires, so a real reset isn't dragged back toward the stale pre-teleport estimate.

(This file also contains `isImplausibleJump`, the physics-based jump-rejection function for the separate, following commit — see `2026-09-05-driver-gps-jump-rejection.md`. It is not yet wired into `CarMarker.tsx` in this commit.)

## 4. Risk & impact on existing functionality

- Blast radius: `gpsSmoothing.ts` is a brand-new file with no existing callers before this change. `smoothFix` is wired into exactly one call site: `CarMarker.tsx`'s `ingestFix`. Grepped `driver-app/` and `rider-app/` for any other importer of `@shared/utils/gpsSmoothing` — none; grepped for other `markerPlayback`/`fixFeed` consumers to confirm `CarMarker.tsx` is the only place `ingestFix`-shaped logic lives in driver-app (`shared/components/CarMarker.tsx`, used by rider-app's driver-tracking marker, is a **separate, unmodified component** — this change does not touch it or rider-app in any way).
- `markerPlayback.ts` (`pushFix`, `playbackPosition`, `shouldResetBuffer`, `splineSample`) is **completely unmodified** — the smoothing filter sits strictly upstream of it, so its own extensively-documented behavior (extrapolation caps, spline continuity, buffer pruning) is unaffected; it now simply receives already-slightly-damped coordinates instead of raw ones.
- The filter cannot itself introduce new lag beyond what its own tuning implies: the process-noise constant is chosen high enough (6 m/s, vs. the ~3 m/s pedestrian default this technique commonly uses) that under sustained real movement the estimate converges to the true position within a few fixes — verified by the "converges toward sustained real movement" unit test (10s of simulated 50 km/h driving lands within ~22 m of the true final position, well under one city block).
- No PIPEDA concern: no new logging, no new data leaves the device; this only reshapes an in-memory coordinate already used to render the marker.

## 5. User-experience effect

Driver-facing only, visible only via the smoothness of the driver's own car marker while online. No new UI, no copy, no user-facing toggle. A driver with a noisy GPS signal (weak sky view, urban canyon) should see less single-fix jitter in the marker's motion; a driver with a clean signal should see no perceptible difference (the filter converges to the true position quickly under real movement per §4).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/utils/gpsSmoothing.ts` (new) | `smoothFix` (1-D Kalman-style filter) + `isImplausibleJump` (unwired in this commit — see follow-up log) | Lightweight, dependency-free GPS jitter smoothing ahead of the existing playback buffer |
| `driver-app/components/CarMarker.tsx` | `ingestFix` now runs every raw fix through `smoothFix` before `pushFix`; added `smoothingStateRef`; re-seeds on buffer reset | Wire the new filter into the single existing ingest choke point |
| `driver-app/__tests__/gpsSmoothing.test.ts` (new) | Unit tests for `smoothFix` and `isImplausibleJump` | Both are pure functions — as testable in isolation as `locationDisplayGate.ts`'s existing pure helpers |

## 7. Before / after

```tsx
// Before
const ingestFix = useCallback((fix: MarkerFix) => {
    const now = Date.now();
    const coord = { latitude: fix.latitude, longitude: fix.longitude };
    if (shouldResetBuffer(bufferRef.current, coord, SNAP_DISTANCE_M)) {
        bufferRef.current.length = 0;
        hasMovementBearingRef.current = false;
        if (Platform.OS === 'android') {
            setAndroidCoord(coord);
            prevTargetRef.current = coord;
        }
    }
    const ts = /* ... */;
    pushFix(bufferRef.current, { ...coord, timestampMs: ts }, now);
}, []);
```

```tsx
// After
const ingestFix = useCallback((fix: MarkerFix) => {
    const now = Date.now();
    const rawCoord = { latitude: fix.latitude, longitude: fix.longitude };
    const ts = /* ... (unchanged) */;
    if (shouldResetBuffer(bufferRef.current, rawCoord, SNAP_DISTANCE_M)) {
        bufferRef.current.length = 0;
        smoothingStateRef.current = null; // re-seed, don't drag toward stale estimate
        hasMovementBearingRef.current = false;
        if (Platform.OS === 'android') {
            setAndroidCoord(rawCoord);
            prevTargetRef.current = rawCoord;
        }
    }
    smoothingStateRef.current = smoothFix(smoothingStateRef.current, { ...rawCoord, timestampMs: ts });
    const coord = { latitude: smoothingStateRef.current.latitude, longitude: smoothingStateRef.current.longitude };
    pushFix(bufferRef.current, { ...coord, timestampMs: ts }, now);
}, []);
```

## 8. Rollback plan

No feature flag — this is a pure, self-contained client-side math change with a bounded, well-tested effect (damping, not altering the destination). Rollback is a plain `git revert` of the `CarMarker.tsx` wiring (the new `gpsSmoothing.ts` file can stay unused harmlessly, or be reverted together); no live data, ride state, or money path touched.

## 9. Verification performed

- [x] `npx jest driver-app/__tests__/gpsSmoothing.test.ts` — 10/10 passed, covering: cold-start seeding, single-fix jitter damping, convergence under sustained real movement, and out-of-order-timestamp safety.
- [x] `npx jest __tests__/components/CarMarker.test.tsx` — 8/8 passed unchanged (confirms the new smoothing stage doesn't break existing marker-rendering behavior).
- [x] `npx tsc --noEmit -p tsconfig.json` for the full driver-app project — clean, 0 errors.
- [x] Blast-radius grep performed: confirmed `gpsSmoothing.ts` has exactly one wired call site (`CarMarker.tsx`), `markerPlayback.ts` is unmodified, and `shared/components/CarMarker.tsx` (rider-app's marker) is untouched.
- [x] Reviewed against relevant `CLAUDE.md` conventions: surgical (new file + one wiring site), no PIPEDA concern, no state-machine/money path.
- [ ] Feature-flagged: not flagged. Justification: bounded, well-tested pure-math change with no user-facing toggle or destination change — only motion smoothness. If this ever needs to be disabled quickly post-release, the rollback above (plain revert) is simpler and equally fast for mobile, since changes only ship on `[build]`-tagged EAS releases anyway.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert; new file is inert if unwired).
- [x] Blast radius is stated, not assumed (single new file, single wiring site, rider-app's separate marker component explicitly confirmed untouched).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 completed).

## What was NOT verified

- **Not tuned against real Spinr trip data or a live device.** The process-noise (6 m/s) and default-accuracy (8 m) constants are documented, reasoned defaults (see the code comments in `gpsSmoothing.ts` citing locationDisplayGate.ts's own accuracy range and adjusting the commonly-cited pedestrian default upward for vehicle speeds) — not empirically fit to Spinr's own GPS trace data, since no device/simulator with live movement was available in this session. The convergence unit test proves the filter doesn't introduce unacceptable lag under one synthetic straight-line scenario; it does not prove the tuning is optimal for real turns, stop-and-go traffic, or genuinely poor-signal conditions.
- **Accuracy-adaptive weighting is not actually active.** `smoothFix` accepts an optional `accuracyM` per fix, but neither of `CarMarker`'s two ingest paths (the `fixFeed` or the `coordinate` prop) currently plumbs expo-location's reported accuracy through — `MarkerFix` has no `accuracy` field. This commit deliberately did not widen `MarkerFix` and every fix producer to add it, to keep this change scoped to the two files above; the filter falls back to a fixed assumed accuracy (8 m) for every fix instead. Recommend a follow-up ACTION_ITEMS entry if accuracy-adaptive smoothing (trusting a precise fix more than a noisy one) is wanted — it would need `MarkerFix`/`fixFeed` producer changes across both apps, a larger blast radius than this commit.
- Not visually confirmed on a device — driver-app has no visual-regression tooling; smoothness is asserted via the unit tests' numeric convergence bound, not by eye.
