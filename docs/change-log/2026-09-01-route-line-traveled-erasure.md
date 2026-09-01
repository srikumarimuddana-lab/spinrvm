# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-01 |
| Author | Claude (session on behalf of vikas@ngitservices.com) |
| Surface(s) | shared (RouteLine), rider-app, driver-app |
| Domain (Sentry tag) | rides |
| PR / commit link | (branch `claude/map-vehicle-tracking-animation-3e85y2`) |
| Related issue or gap ID | User-requested competitive gap, identified during a "match/exceed ride-share giants" audit, 2026-09-01 |

## 1. Issue / gap identified

`shared/components/RouteLine.tsx` — the single route-drawing component used by every surface — always rendered the full pickup→destination (or driver→pickup) polyline regardless of how far the vehicle had progressed along it. Uber/Lyft's live tracking screens visibly erase the traveled portion of the line behind the vehicle as it drives; Spinr's line stayed static end-to-end for the whole leg.

## 2. Root cause

`RouteLine` had no concept of vehicle progress — it only ever received the full route geometry and rendered it as one continuous gradient, with no mechanism to know or express "how much of this has already been driven."

## 3. Fix / remediation

Added an optional `vehiclePosition` prop to `RouteLine`. When set, a new `trimTraveled()` helper snaps the vehicle onto the route (reusing `snapToRoute()` from `shared/utils/vehicleTracking.ts` — the same snapping technique `CarMarker` already uses to keep the car icon on the road) and renders only the snapped point plus everything still ahead; the traveled prefix is simply not returned. Falls back to rendering the whole path unchanged when no vehicle position is given, the path is too short, or the vehicle is more than 35m off the route (detour/stale fix — safer to show the whole line than guess where to cut it).

Wired at the three live-tracking call sites where a route is actively being driven:
- `rider-app/app/driver-arriving.tsx` — the driver→pickup leg, using `currentDriver.lat/lng` (the pickup→dropoff preview leg on the same screen is intentionally left untrimmed — nothing has been driven on it yet).
- `rider-app/app/ride-in-progress.tsx` — the pickup→dropoff leg, using `currentDriver.lat/lng`.
- `driver-app/app/driver/(tabs)/index.tsx` — the driver's own planned route, anchored on the same marker-reported position the follow camera uses (continuing the single-source-of-truth pattern from the last two rounds), not a raw GPS fix.

`driver-arrived.tsx` (driver waiting at pickup, rider not yet in the car) and the completed-ride history views (`ride-completed.tsx`, `ride-details.tsx`, which use the `paths` prop for finished trips) were deliberately left untouched — there's no "still ahead" geometry to erase behind on either of those screens.

**A real bug caught by CI-equivalent local checks, not shipped**: the driver-app wiring initially read `markerPosRef.current` directly inside the JSX render path to compute `vehiclePosition`. `eslint`'s `react-hooks/refs` rule correctly flagged this — reading a ref's `.current` during render is unsafe (React has no way to know to re-render when a ref-only value changes, and it can produce stale/inconsistent output under concurrent rendering). Fixed by mirroring the ref into a `useState`, throttled to ~3m of movement (matching the route-trim's own tolerance) so it doesn't force a screen re-render more often than the existing GPS-driven `location` state already does.

## 4. Risk & impact on existing functionality

- **Blast radius:** `shared/components/RouteLine.tsx` is used on ~10 screens (grepped: `driver-arriving.tsx`, `driver-arrived.tsx`, `ride-in-progress.tsx`, `ride-options.tsx`, `ride-completed.tsx`, `ride-details.tsx` in rider-app; driver-app's own dashboard and `ride-detail.tsx`; `carSurface.tsx`/`carRoute.test.ts` in driver-app's Android Auto surface). The new `vehiclePosition` prop is optional and defaults to `undefined` — every call site not explicitly updated renders exactly as before (verified: only the 3 listed call sites pass the new prop; every other consumer is unaffected by construction, not just by inspection).
- The shared gradient-color builders in `shared/constants/routeMapStyle.ts` (also used by the backend's Static-Maps PNG and admin dashboard) were **not touched** — trimming happens before those builders ever see the points, so nothing outside `RouteLine.tsx` itself needed to change.
- `snapToRoute()` is an existing, already-tested utility (used by `CarMarker` in production today) — no new snapping logic was written, just a new consumer of it.

## 5. User-experience effect

- **Rider- and driver-facing.** The route line now visibly shortens from the back as the vehicle drives it, instead of staying full-length for the whole leg — the same behavior riders already expect from Uber/Lyft. Purely visual; no change to ETA, fare, or any underlying route data.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/components/RouteLine.tsx` | Added `vehiclePosition` prop + `trimTraveled()` helper | Core traveled-erasure logic |
| `shared/components/__tests__/RouteLine.test.tsx` | New — 5 tests covering `trimTraveled()` (no vehicle position, too-short path, mid-route trim, off-route fallback, at-start no-op) | Regression coverage for new logic |
| `rider-app/app/driver-arriving.tsx` | Wired `vehiclePosition` on the driver→pickup leg | Live trimming while approaching pickup |
| `rider-app/app/ride-in-progress.tsx` | Wired `vehiclePosition` on the pickup→dropoff leg | Live trimming during the trip |
| `driver-app/app/driver/(tabs)/index.tsx` | Wired `vehiclePosition` on the driver's own route; added a throttled `markerPosForRender` state (fixes a `react-hooks/refs` violation from reading a ref during render) | Live trimming on the driver's own map |

## 7. Before / after

```tsx
// Before
<RouteLine path={driverRouteCoords} />
```
```tsx
// After
<RouteLine
  path={driverRouteCoords}
  vehiclePosition={
    currentDriver?.lat != null && currentDriver?.lng != null
      ? { latitude: currentDriver.lat, longitude: currentDriver.lng }
      : null
  }
/>
```

## 8. Rollback plan

`git revert` — client-side only, additive/optional prop, no data touched, no migration.

## 9. Verification performed

- [x] `npx tsc --noEmit` — clean on rider-app, driver-app, and `shared` (shared's pre-existing `baseUrl` deprecation config warning confirmed present before this change too, via `git stash`; not a file-level error).
- [x] `npx eslint` on every changed source file (both apps) — clean (0 errors, 0 warnings) after fixing the `react-hooks/refs` violation found above.
- [x] rider-app full suite: **139/139 suites, 1942/1942 tests passing** (138/1937 baseline + 1 new suite/5 new tests). The "worker process failed to exit gracefully" warning in the output is pre-existing — reproduced identically on the unmodified baseline via `git stash -u`, unrelated to this change.
- [x] driver-app full suite: **127/127 suites, 1435/1435 tests passing** (unchanged count — no new driver-app-specific test added for the throttle wiring itself; covered indirectly by the existing dashboard screen suite continuing to pass).
- [x] Blast-radius grep: confirmed the new prop is optional and only 3 of ~10 `RouteLine` call sites pass it.
- [ ] Manual/on-device verification — not performed; no device/emulator in this environment. Reasoned about via the unit-tested `trimTraveled()` logic plus the existing production-proven `snapToRoute()` it reuses.

## 10. What was NOT verified

- No live/on-device confirmation that the line visibly erases correctly while actually driving.
- Did not add trimming to the `paths` (multi-section, completed-ride) rendering path — deliberately out of scope, since those views show a finished trip with nothing left "ahead" to erase.
- Did not touch `driver-arrived.tsx`'s single `RouteLine` — the pickup→dropoff leg hasn't started being driven at that point in the ride lifecycle, so trimming would have nothing meaningful to do there yet.
