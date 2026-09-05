# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | vikas@ngitservices.com |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (added at PR open) |
| Related issue or gap ID | Live-testing bug reports of the car marker "disappearing"/teleporting; follow-up to `2026-09-05-driver-gps-smoothing-filter.md` |

## 1. Issue / gap identified

`CarMarker.tsx`'s only defense against a bad GPS fix is `shouldResetBuffer`: a pure **distance** threshold (`SNAP_DISTANCE_M = 500`). Any fix more than 500m from the last one is treated identically — as a legitimate teleport (stale fix after backgrounding, ride handoff) — and the marker hard-resets to it, whether the fix is real or a multipath/receiver glitch. A one-off glitch fix 600m away arriving 1 second after a normal one gets the exact same "snap here" treatment as a driver who was genuinely 600m away 30 minutes ago.

## 2. Root cause

Distance alone cannot distinguish a real position change from a physically impossible one — the missing ingredient is **elapsed time**. 500m in 30 minutes (parking lot → highway) and 500m in 1 second (GPS multipath bounce) are both ">500m," but only one is possible for a road vehicle. This is conceptually the same gap Uber's Beacon system closes with accelerometer/gyroscope fusion (rejecting a GPS jump the vehicle's own sensed acceleration couldn't have produced) — this fix reaches the same outcome with only the data already available (fix coordinates + timestamps), no new sensors.

## 3. Fix / remediation

Wired the already-added `isImplausibleJump` (from `shared/utils/gpsSmoothing.ts`, added but unwired in the prior smoothing-filter commit) into `CarMarker.tsx`'s `ingestFix`, as the very first check: compute the implied speed between the new fix and the last **accepted** raw fix; if it exceeds a generous ceiling (60 m/s / 216 km/h — matching `markerPlayback.ts`'s own existing `MAX_PLAUSIBLE_SPEED_MPS` constant, so both stages of the pipeline agree on what "impossible" means), drop the fix outright — no buffer touch, no reset, no smoothing update — rather than letting `shouldResetBuffer` treat it as a legitimate teleport. A genuine gap (backgrounding, tunnel) is unaffected: the same 500m difference computed over a real multi-minute gap implies an ordinary driving speed and passes through unchanged.

## 4. Risk & impact on existing functionality

- Blast radius: same single call site as the smoothing-filter commit — `CarMarker.tsx`'s `ingestFix`. `isImplausibleJump` was already added (and unit-tested) in the prior commit; this commit only wires it in and adds integration-level tests confirming the wiring itself behaves correctly.
- `shouldResetBuffer`/`markerPlayback.ts` are unmodified — this check runs strictly before them, so their own logic and tests are unaffected; they now simply never see an outright-implausible fix.
- **Failure mode if this check is ever wrong**: dropping a fix that was actually real (a false positive) means one GPS update is silently skipped — the marker holds its prior position for one tick instead of jumping to a bad one, which is the same graceful degradation the existing extrapolation-cap/holding mode in `markerPlayback.ts` already provides for a data gap. This is a strictly safer failure mode than the current behavior (silently accepting and rendering an impossible jump).
- No PIPEDA concern (in-memory coordinate comparison only, no new logging).

## 5. User-experience effect

Driver-facing only, visible only as the absence of the previously-possible "marker suddenly jumps/snaps to a nonsensical location for one fix" glitch. No new UI, no copy, no toggle.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/CarMarker.tsx` | `ingestFix` now calls `isImplausibleJump` first and returns early (drops the fix) when it's true; added `lastAcceptedRawFixRef` | Reject physically-impossible fixes before they reach the reset/buffer/smoothing logic |
| `driver-app/__tests__/components/CarMarker.test.tsx` | Added a new describe block: 3 integration tests asserting `pushFix` is/isn't called for an implausible-vs-plausible-vs-real-gap fix sequence | Cover the actual wiring, not just the pure function (already unit-tested in `gpsSmoothing.test.ts`) |

## 7. Before / after

```tsx
// Before
const ingestFix = useCallback((fix: MarkerFix) => {
    const now = Date.now();
    const rawCoord = { latitude: fix.latitude, longitude: fix.longitude };
    const ts = /* ... */;
    if (shouldResetBuffer(bufferRef.current, rawCoord, SNAP_DISTANCE_M)) {
        /* ... reset ... */
    }
    smoothingStateRef.current = smoothFix(smoothingStateRef.current, { ...rawCoord, timestampMs: ts });
    /* ... pushFix ... */
}, []);
```

```tsx
// After
const ingestFix = useCallback((fix: MarkerFix) => {
    const now = Date.now();
    const rawCoord = { latitude: fix.latitude, longitude: fix.longitude };
    const ts = /* ... (unchanged) */;
    if (isImplausibleJump(lastAcceptedRawFixRef.current, { ...rawCoord, timestampMs: ts })) {
        return; // GPS noise/multipath, not a real position — drop outright
    }
    lastAcceptedRawFixRef.current = { ...rawCoord, timestampMs: ts };
    if (shouldResetBuffer(bufferRef.current, rawCoord, SNAP_DISTANCE_M)) {
        /* ... reset (unchanged) ... */
    }
    smoothingStateRef.current = smoothFix(smoothingStateRef.current, { ...rawCoord, timestampMs: ts });
    /* ... pushFix (unchanged) ... */
}, []);
```

## 8. Rollback plan

No feature flag — a pure, well-tested rejection rule with a strictly-safer failure mode than the pre-existing behavior (§4). Rollback is a plain `git revert`; no live data, ride state, or money path touched.

## 9. Verification performed

- [x] `npx jest __tests__/components/CarMarker.test.tsx` — 11/11 passed, including 3 new integration tests: (a) an implausible fix (600m/2ms) does not reach `pushFix`, (b) a plausible fix (11m/2s) does, (c) a large-but-plausible post-gap fix (3km/5min) does. Timing controlled deterministically via `jest.setSystemTime` rather than real wall-clock, after discovering `ingestFix`'s own 60s device-clock sanity guard would otherwise substitute real `Date.now()` for small synthetic test timestamps and make the tests timing-dependent/flaky — caught and fixed within this session before committing.
- [x] `npx jest driver-app/__tests__/gpsSmoothing.test.ts` — 10/10 passed (unchanged from the prior commit; `isImplausibleJump`'s own pure-function tests already covered the plausible/implausible/gap/sub-jitter cases).
- [x] `npx tsc --noEmit -p tsconfig.json` for the full driver-app project — clean, 0 errors.
- [x] Blast-radius grep performed: same single call site as the prior smoothing-filter commit; re-confirmed no other consumer of `isImplausibleJump`.
- [x] Reviewed against relevant `CLAUDE.md` conventions: surgical, no PIPEDA concern, no state-machine/money path.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert).
- [x] Blast radius is stated, not assumed.
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 completed).

## What was NOT verified

Not tested against real GPS multipath data or a physical device — the 60 m/s (216 km/h) ceiling is a documented, reasoned choice (matches `markerPlayback.ts`'s own existing constant so the two pipeline stages agree) rather than one empirically validated against a real corpus of Spinr driving traces or a real captured multipath glitch. If a future report shows this ceiling is either too strict (rejecting a real fix during, e.g., legitimate high-speed highway driving with an unusually large fix-to-fix gap) or too loose (still letting some glitch class through), it should be tuned with real trip data rather than guessed further — this commit's confidence is in the *logic* (elapsed-time-aware, not distance-only), not in the exact numeric ceiling.
