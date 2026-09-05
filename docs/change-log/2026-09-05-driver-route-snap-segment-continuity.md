# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | vikas@ngitservices.com |
| Surface(s) | driver-app (wired); shared (new opt-in parameter, rider-app's own marker not yet wired) |
| Domain (Sentry tag) | drivers |
| PR / commit link | (added at PR open) |
| Related issue or gap ID | User-reported: car icon "either in opposite direction or 90 degrees like drifting on the route" |

## 1. Issue / gap identified

The driver's car icon sometimes points 90–180° off from the actual direction of travel while still appearing to stay on the route.

## 2. Root cause

`snapToRoute()` (`shared/utils/vehicleTracking.ts`) picks whichever route segment is nearest by perpendicular distance to the current fix, with no awareness of which way the car is actually moving or which segment it was snapped to the previous tick. Wherever two segments of the route polyline both fall within `maxSnapMeters` (35m) of the car — a divided road, an out-and-back street where the outbound and return legs run close together, a crossing street near an intersection, or simply GPS jitter nudging the point a meter sideways — the globally-nearest segment can legitimately run backward or across relative to true travel. `selectBearing`'s `'route'` branch then renders that segment's own direction verbatim, with no cross-check against the spline tangent, reported heading, or the previous tick's bearing.

## 3. Fix / remediation

Added an optional `preferredFromIndex` parameter to `snapToRoute` — the previous tick's own `segmentIndex`. When given, the search is restricted to segments at or after `preferredFromIndex - 1` (a 1-segment backward tolerance for legitimate GPS correction) before ever falling back to the original unrestricted global search (only when nothing in that window is within `maxSnapMeters` — e.g. after a genuine reroute past the tolerance, so this can never permanently block re-snapping). Wired into `driver-app/components/CarMarker.tsx`'s playback ticker via a new `lastRouteSegmentIndexRef`, updated after every snap attempt (including to `null` when the car goes off-route, so a stale hint from before a detour doesn't anchor the next on-route search to an irrelevant location).

## 4. Risk & impact on existing functionality

- Blast radius: `snapToRoute` has 2 other call sites — `shared/components/RouteLine.tsx` and rider-app's own `shared/components/CarMarker.tsx` (grepped and confirmed) — both call with the original 3-argument signature; the new 4th parameter defaults to `undefined`, which the function treats identically to the pre-existing behavior (unrestricted global search). **Neither is behaviorally changed by this commit.**
- Rider-app's own `CarMarker.tsx` has the same underlying ambiguity (shares `vehicleTracking.ts`) but was deliberately left unwired — out of scope for a driver-app-reported bug; noted here rather than silently expanded.
- `markerPlayback.ts` and `selectBearing`'s own priority logic are unmodified — this only makes the ROUTE-branch's segment choice itself more consistent with recent history.
- Backward-tolerance is intentionally small (1 segment) — enough to absorb normal GPS jitter/correction without being so generous it reintroduces the original ambiguity across nearby segments.

## 5. User-experience effect

Driver-facing only, visible only as the absence of the reported wrong-direction icon while route-following. No new UI, no copy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/utils/vehicleTracking.ts` | `snapToRoute` gained an optional `preferredFromIndex` param; internal search refactored into a reusable `nearestFrom(startIndex)` closure, called once with the continuity window then once unrestricted as fallback if needed | Prefer segment continuity with the previous tick over a pure global-nearest search |
| `driver-app/components/CarMarker.tsx` | Added `lastRouteSegmentIndexRef`; passed into `snapToRoute`; updated after every snap attempt | Wire the continuity hint into the one place driver-app calls `snapToRoute` |
| `rider-app/__tests__/vehicleTracking.test.ts` | 4 new tests: wrong-segment-without-hint, correct-segment-with-hint, 1-segment backward tolerance, fallback-to-global-search | Cover both the bug being fixed and the new parameter's edge cases |

## 7. Before / after

```ts
// Before
export function snapToRoute(
  point: TrackingLatLng,
  route: readonly TrackingLatLng[] | null | undefined,
  maxSnapMeters = 35,
): RouteSnapResult | null {
  // ...single unrestricted nearest-distance loop over the whole route...
}
```

```ts
// After
export function snapToRoute(
  point: TrackingLatLng,
  route: readonly TrackingLatLng[] | null | undefined,
  maxSnapMeters = 35,
  preferredFromIndex?: number | null,
): RouteSnapResult | null {
  // ...same loop, now a nearestFrom(startIndex) closure...
  const minIndex = preferredFromIndex != null
    ? Math.max(0, Math.floor(preferredFromIndex) - SEGMENT_BACKWARD_TOLERANCE)
    : 0;
  let best = nearestFrom(minIndex);
  if ((!best || best.deviationMeters > maxSnapMeters) && minIndex > 0) {
    best = nearestFrom(0); // fallback: nothing close enough in the window
  }
  // ...
}
```

## 8. Rollback plan

No feature flag — the new parameter is opt-in (defaults to the exact prior behavior), and the one place that opts in (`CarMarker.tsx`) is a bounded, well-tested change. Rollback is a plain `git revert`; no live data, ride state, or money path touched.

## 9. Verification performed

- [x] `npx jest rider-app/__tests__/vehicleTracking.test.ts` — 26/26 passed (22 pre-existing unchanged + 4 new, including a hand-verified geometric scenario proving the wrong-segment bug reproduces without the hint and is fixed with it).
- [x] `npx jest driver-app/__tests__/components/CarMarker.test.tsx` — 15/15 passed unchanged.
- [x] `npx tsc --noEmit` clean for both `driver-app` and `rider-app` (the shared file's change touches both apps' type-checking).
- [x] Blast-radius grep performed: confirmed all `snapToRoute` call sites and that the 2 unwired ones are unaffected by the new optional parameter.
- [x] Reviewed against relevant `CLAUDE.md` conventions: surgical, additive/backward-compatible, no PIPEDA concern (route/position data only, no new logging), no state-machine/money path.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert; new param is a no-op if omitted).
- [x] Blast radius is stated, not assumed (2 other call sites enumerated, confirmed unaffected).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 completed).

## What was NOT verified

Not confirmed against a real GPS trace or physical device — the fix targets a geometric ambiguity that depends on real route shapes (divided roads, loops, nearby intersections) that weren't available to reproduce exactly as reported. The included tests construct hand-verified synthetic geometry that reproduces the exact ambiguity class described (parallel opposite-direction segments a meter apart) and prove the fix resolves it; they don't prove this is the *only* remaining cause of a wrong-direction icon, or that the 1-segment backward tolerance is optimal for real-world route shapes rather than just sufficient for the tested case. Recommend the user re-test on the next build and report whether the specific reported drift is gone.
