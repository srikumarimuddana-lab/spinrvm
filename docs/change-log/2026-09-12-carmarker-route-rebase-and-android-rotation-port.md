# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code (audit follow-through, roadmap items R1 + R3) |
| Surface(s) | rider-app, shared |
| Domain (Sentry tag) | rides |
| PR / commit link | srikumarimuddana-lab/spinrvm#5290 |
| Related issue or gap ID | `docs/audit/ride-experience/ROADMAP.md` R1 (P1) + R3 (P2); `docs/known-forks.md` CarMarker entry |

## 1. Issue / gap identified

`shared/components/CarMarker.tsx` — the car marker every rider-app ride screen renders — had
two known rendering defects that `driver-app/components/CarMarker.tsx` (an intentional fork of
the same file) had already fixed for itself, on 2026-09-11 and 2026-09-09 respectively, with
neither fix ever ported to the shared copy.

## 2. Root cause

**R1 (route-rebase):** the shared file's route-coordinate-changed effect only stored the new
route reference; it never re-based `lastRouteSegmentIndexRef` against the new polyline. Since
the in-trip live route is re-anchored at the car's current position on every poll, an old
segment index carried forward restricted the next `snapToRoute` search to segments ahead of the
car — approaching a turn, the first segment inside that restricted window was the post-turn one,
so the marker took a ~90°-wrong bearing while the car was still on the straight (the "car drives
sideways" glitch driver-app's own drivers reported on a 2026-09-11 test ride).

**R3 (Android rotation):** Android's `Marker.rotation` is a plain native prop, not an
`Animated.Value`. The shared file stepped it directly to each tick's target bearing every
500 ms (`TICK_MS`); a turn's angular rate can exceed that step's visual tolerance, so the icon
visibly snapped through corners even though position stayed smooth via
`animateMarkerToCoordinate`. driver-app fixed this on 2026-09-09 with a
`requestAnimationFrame`-driven interpolation loop; the fix was never ported.

Both gaps were root-caused, not just observed, by the 2026-09-12 ride-experience
industry-benchmark audit (`docs/audit/ride-experience/module-c-shared.md`), which diffed the two
files at a feature level specifically because they are a registered fork.

## 3. Fix / remediation

Ported both fixes from `driver-app/components/CarMarker.tsx` into
`shared/components/CarMarker.tsx` verbatim — both depend only on primitives
(`snapToRoute`, `prevTargetRef`, `lastRouteSegmentIndexRef`, `MAX_ROUTE_SNAP_M`,
`shortestArcRotationTarget`, `rotationValueRef`, `hasBearingRef`, `MAX_ROTATE_MS`) already
present in the shared file, confirmed before porting. Also added a header comment to both
`CarMarker.tsx` copies naming the other as a tracked fork and pointing at
`docs/known-forks.md` / `module-c-shared.md` (roadmap item R6), so the next engineer touching
either file gets a pointer instead of having to rediscover this audit's diff from scratch.

## 4. Risk & impact on existing functionality

- **What else reads the same component:** every rider-app ride screen that renders a driver's
  live position — full list below (§6/consumer reference). driver-app is unaffected; it already
  has both fixes in its own fork.
- **Could this regress a working flow?** The ported code is the exact logic already running in
  production in driver-app since 2026-09-09/2026-09-11 with no reported regression there — this
  is the strongest evidence available short of a rider-app device test (see below).
- **Blast radius:** single-surface (rider-app rendering only; no backend, no ride-state, no
  money path touched). Full consumer list (per `module-c-shared.md`'s own blast-radius
  reference — not re-derived here):
  - `rider-app/app/(tabs)/index.tsx`
  - `rider-app/app/ride-options.tsx`
  - `rider-app/app/driver-arriving.tsx`
  - `rider-app/app/driver-arrived.tsx`
  - `rider-app/app/ride-in-progress.tsx`
  - Tests: `rider-app/__tests__/{rideOptionsScreen,homeScreen,driverArrivingScreen,driverArrivedScreen,rideInProgressScreen,carMarkerPositionChange}.test.tsx`
- **Background loops / state machine / money:** none touched. This is pure client-side marker
  rendering geometry — no ride-state transition, no wallet/fare code path.

## 5. User-experience effect

- **Who sees a difference:** riders, on every ride screen that shows the driver's live position
  (the five screens above). No driver-app, admin, or corporate-facing change.
- **Visible mid-ride?** Yes — a rider already mid-ride at deploy time will simply stop seeing
  the "car drives sideways near a turn" glitch and the Android rotation snap on their *next*
  route/bearing update; there is no jarring transition, no data migration, no session state to
  reconcile. This is a pure rendering-quality improvement, not a behavior change a rider would
  need to be told about.
- **Copy/notification change:** none.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/components/CarMarker.tsx` | Route-coordinate-changed effect now re-bases `lastRouteSegmentIndexRef` via `snapToRoute` on every route identity change (was a no-op store); Android rotation now interpolates via a `requestAnimationFrame` loop (`animateAndroidRotationTo`/`stepAndroidRotation`) instead of stepping directly to each tick's target; added tracked-fork header comment. | R1 (route-rebase, P1) + R3 (Android rotation, P2) + R6 (fork comment) from `docs/audit/ride-experience/ROADMAP.md` |
| `driver-app/components/CarMarker.tsx` | Added tracked-fork header comment only — no behavior change (this file already has both fixes). | R6 |

## 7. Before / after

```tsx
// Before (shared/components/CarMarker.tsx) — route-rebase effect
const routeRef = useRef(routeCoordinates);
useEffect(() => {
    routeRef.current = routeCoordinates;
}, [routeCoordinates]);
```

```tsx
// After — re-bases the continuity hint on every route identity change,
// matching driver-app's already-proven fix
const routeRef = useRef(routeCoordinates);
useEffect(() => {
    const prev = routeRef.current;
    routeRef.current = routeCoordinates;
    if (prev === routeCoordinates) return;
    if (!routeCoordinates || routeCoordinates.length < 2) {
        lastRouteSegmentIndexRef.current = null;
        return;
    }
    const rebased = snapToRoute(prevTargetRef.current, routeCoordinates, MAX_ROUTE_SNAP_M, null);
    lastRouteSegmentIndexRef.current = rebased?.segmentIndex ?? null;
}, [routeCoordinates]);
```

```tsx
// Before — Android rotation hard-stepped to target every tick
if (isAndroid) {
    setAndroidRotation(((bearing % 360) + 360) % 360);
}
```

```tsx
// After — interpolated via requestAnimationFrame over the same TICK_MS window position animates over
if (isAndroid) {
    animateAndroidRotationTo(bearing, TICK_MS);
}
```

## 8. Rollback plan

`git revert` is a true, sufficient rollback here: no data is mutated, no migration is involved,
no ride-state or money path is touched, and no feature flag exists or is needed for a pure
client-side rendering fix with no server-side counterpart to roll back in step.

## 9. Verification performed

- [x] `npm run typecheck` (shared package) run before and after the change — identical
      pre-existing error count (20, all unrelated to this diff: module-resolution/implicit-any
      issues in code this change doesn't touch) confirms zero new type errors introduced.
- [x] Diffed the ported logic block-by-block against `driver-app/components/CarMarker.tsx`'s
      already-shipped version to confirm a byte-for-byte-equivalent port (via `/code-review`,
      medium effort — no findings).
- [x] Confirmed every symbol the ported code depends on (`snapToRoute`, `prevTargetRef`,
      `lastRouteSegmentIndexRef`, `MAX_ROUTE_SNAP_M`, `shortestArcRotationTarget`,
      `rotationValueRef`, `hasBearingRef`, `MAX_ROTATE_MS`) already existed in the shared file
      before porting (grepped, not assumed).
- [ ] Manual repro steps followed in staging — **not done, no staging/device access in this
      session** (see §"What was NOT verified" below).
- [x] Blast-radius grep performed — full consumer list above, sourced from the audit's own
      grep-verified reference rather than re-derived.
- [x] Reviewed against relevant CLAUDE.md conventions — no ride-state, money, or RLS path
      touched; this is pure rendering geometry.
- [ ] Feature-flagged — **not flagged.** Justification: this is a bug-fix bringing rider-app's
      rendering into line with driver-app's already-shipped, already-proven behavior, not new
      user-visible functionality; CLAUDE.md's flagging guidance is for new/changed UX, not for
      closing a rendering-correctness gap against an existing, already-live reference
      implementation. A flag would add complexity with no real rollback benefit (the true
      rollback is `git revert`, per §8).

### What was NOT verified

- **No real device or emulator test.** Both apps have zero automated visual-regression tooling
  (confirmed repo-wide in `docs/audit/ride-experience/REPORT.md`), so this fix is verified by
  code-level equivalence to driver-app's proven fix and a clean typecheck — not by watching a
  marker render. This is exactly the gap `docs/audit/ride-experience/ROADMAP.md` item R14
  (device/ops access gap) names as causally upstream of this bug existing in the first place.
- **No live-route/GPS integration test exists for this exact scenario** (a route array
  identity change near a turn) in either app's test suite as of this change; the consumer test
  list in §4 covers screen rendering, not this specific geometric edge case.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data/migration involvement).
- [x] Blast radius is stated, not assumed (sourced from the audit's grep-verified consumer list).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5
      states the visible difference explicitly: the glitch stops occurring, no user action or
      notice needed).
