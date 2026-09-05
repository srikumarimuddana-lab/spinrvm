# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | vikas@ngitservices.com |
| Surface(s) | shared (markerPlayback.ts, consumed by both driver-app and rider-app CarMarkers) |
| Domain (Sentry tag) | drivers |
| PR / commit link | (added at PR open) |
| Related issue or gap ID | User-reported: "even when stopped it went on and then came back and was in reverse direction to the route" |

## 1. Issue / gap identified

The car marker sometimes visibly continues moving forward for up to ~1.5s after the vehicle has actually stopped (braked from real driving speed), then snaps back to the true (behind) position once a real fix lands or the extrapolation window expires.

## 2. Root cause

`playbackPosition()`'s dead-reckoning branch (used when the GPS fix buffer runs dry) already has one guard, `MIN_EXTRAPOLATION_SPEED_MPS`, added for a related but narrower bug ("car drove through a red light it was stopped at" — see the code's own dated comment). That guard only blocks extrapolation when the last buffered segment's average speed was **already below 1.5 m/s** before the gap began — i.e. a car that was already idling/crawling. It does nothing for the much more common case: a car doing real speed (e.g. 25 km/h) that brakes hard to a stop. The last buffered segment right before that stop still reads well above 1.5 m/s, so the existing guard passes it through, and the full ~1.5s cap gets used to project the car forward through the stop.

## 3. Fix / remediation

When at least 3 fixes are buffered, compare the two most recent segments' average speeds. If the most recent segment is slower than the one before it (a decelerating trend — the signature of braking), scale the extrapolation time window (`maxExtrapolationMs`) down proportionally to that speed ratio, capped so it can only ever shrink the window, never lengthen it. With fewer than 3 buffered fixes (nothing to compare against) or a steady/accelerating trend, the window is unchanged from today's behavior. This uses only data already in the pipeline — no new sensor input, no change to `MarkerFix`/`fixFeed`.

## 4. Risk & impact on existing functionality

- Blast radius: `playbackPosition()` is shared by both driver-app's `CarMarker.tsx` and rider-app's `shared/components/CarMarker.tsx` (both consume the same `markerPlayback.ts`) — this fix therefore also benefits the rider-facing driver-tracking marker, not just the driver's own view. Grepped for other consumers of `playbackPosition`; none beyond these two.
- The change only ever **shrinks** the extrapolation window relative to today's behavior (the `Math.min(1, ...)` clamp guarantees the ratio never exceeds 1) — it cannot make the marker extrapolate farther or hold less often than before. The worst-case regression this could introduce is holding *slightly earlier* than before during a genuine, brief GPS gap while decelerating (e.g. slowing for a turn, not stopping) — a strictly safer failure mode (holding at the last known-good position) than the bug being fixed (visibly driving through a stop).
- All 15 pre-existing `markerPlayback.test.ts` tests pass unchanged, including the constant-velocity "dead-reckons past the newest fix" test (verified by hand: equal segment speeds → `decelRatio = 1` → no shrink) and the near-stopped-segment regression test (unrelated code path, still passes).
- No change to `splineSample`, `pushFix`, `shouldResetBuffer`, or the interpolation branch — isolated to the extrapolation branch only.

## 5. User-experience effect

Driver- and rider-facing (both CarMarker consumers share this code): should reduce the "car drives through the stop, then snaps back" artifact specifically when braking from real speed — the more common, more jarring version of a bug this file's own history shows was only partially fixed before. No new UI, no copy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/utils/markerPlayback.ts` | `playbackPosition`'s extrapolation branch now computes a deceleration ratio from the last two buffered segments and scales `maxExtrapolationMs` by it before checking `overshootMs` against the window | Stop projecting the car forward through a hard stop from real speed |
| `rider-app/__tests__/markerPlayback.test.ts` | 3 new tests: shrink-on-deceleration, no-shrink-on-acceleration/steady-speed, unchanged-behavior-with-fewer-than-3-fixes | Cover the new logic and its safe-fallback cases |

## 7. Before / after

```ts
// Before
const overshootMs = renderTimeMs - newest.timestampMs;
const prev = buffer.length >= 2 ? buffer[buffer.length - 2] : null;
if (prev && overshootMs <= maxExtrapolationMs) {
  // ...extrapolate using the full cap...
}
```

```ts
// After
const overshootMs = renderTimeMs - newest.timestampMs;
const prev = buffer.length >= 2 ? buffer[buffer.length - 2] : null;
const priorToPrev = buffer.length >= 3 ? buffer[buffer.length - 3] : null;
let effectiveMaxExtrapolationMs = maxExtrapolationMs;
if (prev && priorToPrev) {
  const priorSpeed = /* speed of the segment before the last one */;
  const lastSpeed = /* speed of the last buffered segment */;
  if (priorSpeed > 0) {
    const decelRatio = Math.min(1, lastSpeed / priorSpeed);
    effectiveMaxExtrapolationMs = maxExtrapolationMs * decelRatio;
  }
}
if (prev && overshootMs <= effectiveMaxExtrapolationMs) {
  // ...extrapolate using the (possibly shrunk) window...
}
```

## 8. Rollback plan

No feature flag — a bounded, well-tested change that only ever shrinks an existing time window, with a strictly-safer failure mode (§4) than the pre-existing behavior. Rollback is a plain `git revert`; no live data, ride state, or money path touched.

## 9. Verification performed

- [x] `npx jest rider-app/__tests__/markerPlayback.test.ts` — 18/18 passed (15 pre-existing unchanged + 3 new).
- [x] `npx jest rider-app/__tests__/carMarkerPositionChange.test.tsx` — 2/2 passed (rider-app's own marker, confirming the shared-file change doesn't break that consumer).
- [x] `npx jest driver-app/__tests__/components/CarMarker.test.tsx` — unaffected (that suite mocks `playbackPosition` entirely, so this internal-logic change has no interaction with it — confirmed by inspection of the mock).
- [x] `npx tsc --noEmit` clean for both `driver-app` and `rider-app`.
- [x] Blast-radius grep performed: confirmed both `playbackPosition` consumers (driver-app, rider-app CarMarkers) and that both benefit from, not regress from, this change.
- [x] Reviewed against relevant `CLAUDE.md` conventions: surgical, no PIPEDA concern, no state-machine/money path.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert).
- [x] Blast radius is stated, not assumed (both shared consumers identified, both benefit).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 completed).

## What was NOT verified

This is explicitly a heuristic, not a guarantee — stated as such when proposed and confirmed by the user as the accepted first-step approach (vs. the alternative of plumbing real instantaneous speed through `MarkerFix`/`fixFeed`, a larger change). It was not tested against real GPS traces of actual braking events; the included tests use hand-constructed synthetic segments with a clear speed drop, not a real device's noisy fix-to-fix speed curve during genuine braking. A gentle, gradual deceleration (coasting to a stop rather than braking hard) may show a smaller speed ratio between the last two segments and shrink the window less than a hard stop would — this is an accepted limitation of a 2-segment heuristic, not a bug. If the reported symptom persists after this change, the next step is plumbing `coords.speed` through the fix pipeline (option (b) from the original proposal) for a direct instantaneous-speed signal rather than an inferred trend.
