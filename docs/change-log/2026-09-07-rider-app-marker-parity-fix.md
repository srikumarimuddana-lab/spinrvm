# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-07 |
| Author | Claude Code (session on behalf of ittalenthire.ca@gmail.com) |
| Surface(s) | rider-app (shared component consumed by rider-app only) |
| Domain (Sentry tag) | drivers |
| PR / commit link | (added on branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | Follow-up from 2026-09-07 vehicle-icon animation audit; gaps also documented in `docs/change-log/2026-09-05-driver-route-snap-segment-continuity.md` and `docs/change-log/2026-09-05-driver-gps-smoothing-filter.md` as "driver-app only" |

## 1. Issue / gap identified

Two live-vehicle marker smoothness fixes shipped to `driver-app/components/CarMarker.tsx` on 2026-09-05 were never ported to `shared/components/CarMarker.tsx` — the component rider-app uses to render the assigned driver's car on the rider's own map (`ride-options.tsx`, `ride-in-progress.tsx`, `driver-arrived.tsx`, `driver-arriving.tsx`, `(tabs)/index.tsx`). Riders could still see (1) a one-tick bearing flip at intersections/divided roads and (2) un-damped single-fix GPS jitter, on the exact same underlying driver location stream the driver-app fix already covers.

## 2. Root cause

Both prior fixes were reported and diagnosed from driver-app live testing and were scoped and shipped as driver-app-only changes (see `docs/change-log/2026-09-05-driver-route-snap-segment-continuity.md` §4, explicitly "out of scope for a driver-app-reported bug"). `shared/components/CarMarker.tsx` and `driver-app/components/CarMarker.tsx` are two independent copies of the same logic (not a single shared component), so the fix landing in one does not propagate to the other. Both underlying utilities (`shared/utils/vehicleTracking.ts`'s `preferredFromIndex` param on `snapToRoute`, and `shared/utils/gpsSmoothing.ts`) are already shared and already unit-tested — they were simply never called from the rider-app copy.

## 3. Fix / remediation

Ported both fixes into `shared/components/CarMarker.tsx`, mirroring the driver-app implementation exactly:

1. **Route-segment continuity hint** — track the last snapped route segment index (`lastRouteSegmentIndexRef`) and pass it into every `snapToRoute()` call as `preferredFromIndex`, so the nearest-segment search prefers continuing forward from where the car already was instead of a pure global-nearest search that can momentarily pick a nearby-but-wrong-direction segment.
2. **GPS pre-smoothing + implausible-jump rejection** — run every raw ingested fix through `isImplausibleJump()` (drop physically-impossible speed jumps outright) then `smoothFix()` (a lightweight Kalman-style filter) before it enters the playback buffer, damping single-fix jitter the buffer's spline only smooths *between* fixes, not within one.

No new utility code was written — both fixes reuse the exact same `shared/utils/vehicleTracking.ts` and `shared/utils/gpsSmoothing.ts` functions the driver-app copy already calls and that are already unit-tested independently of either component.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface (rider-app), multiple call sites.** `shared/components/CarMarker.tsx` is imported by 5 rider-app screens (`ride-options.tsx`, `ride-in-progress.tsx`, `driver-arrived.tsx`, `driver-arriving.tsx`, `(tabs)/index.tsx`) — grepped via `from ['"].*CarMarker['"]|shared/components/CarMarker` across the repo; no admin-dashboard or backend consumer exists. `driver-app/components/CarMarker.tsx` is a separate file and is untouched by this change.
- **No change to `driver-app`.** This PR only edits the rider-app copy; the driver-app copy (already running this logic live since 2026-09-05) is unmodified.
- **No change to any props, exported types, or the `_propsAreEqual` memo comparator** — the diff is entirely internal (new refs + extra steps inside `ingestFix` and the position-ticker effect). Callers of `<CarMarker />` need no changes.
- **Behavioral risk is bounded by the fact this exact code has been live in production (driver-app) since 2026-09-05** without a reported regression in this codebase's change-log — this is a port of proven logic, not new logic.
- **Rejection risk**: `isImplausibleJump()` can now silently drop a fix if it looks like an impossible-speed jump (>60 m/s / 216 km/h implied from the last accepted fix). If a genuinely valid fix were ever misclassified, the marker would simply hold its last position for that tick rather than jump — same fail-safe behavior already accepted for driver-app.

## 5. User-experience effect

- **Rider-facing.** Visible to a rider actively watching their assigned driver's car move on the map (`ride-options` while a driver is en route to accept, `driver-arriving`, `driver-arrived`, `ride-in-progress`, and the idle nearby-drivers map on the home tab).
- **Visible mid-session**: yes — a rider already watching the map during an active ride will see smoother motion at intersections and less single-point jitter starting from their next fix after this ships; there is no state to migrate, so it applies to any in-progress ride immediately.
- **No copy or notification change.**

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/components/CarMarker.tsx` | Added `lastRouteSegmentIndexRef`, `smoothingStateRef`, `lastAcceptedRawFixRef`; `ingestFix` now rejects implausible-speed jumps and runs `smoothFix()` before `pushFix()`; the playback ticker's `snapToRoute()` call now passes/updates `preferredFromIndex` | Port the 2026-09-05 driver-app-only route-continuity and GPS-smoothing fixes to rider-app's copy of the same marker, closing the parity gap found in the 2026-09-07 animation audit |
| `docs/change-log/2026-09-07-rider-app-marker-parity-fix.md` | New Change Impact Log entry (this file) | Required for any behavior change to a live-tested surface per `CLAUDE.md` |

## 7. Before / after

```tsx
// Before — ingestFix (shared/components/CarMarker.tsx)
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

// ... in the playback ticker:
const snap = snapToRoute(p.coordinate, routeRef.current, MAX_ROUTE_SNAP_M);
```

```tsx
// After — ingestFix now rejects implausible jumps and pre-smooths every fix
const ingestFix = useCallback((fix: MarkerFix) => {
    const now = Date.now();
    const rawCoord = { latitude: fix.latitude, longitude: fix.longitude };
    const ts = /* ... */;
    if (isImplausibleJump(lastAcceptedRawFixRef.current, { ...rawCoord, timestampMs: ts })) {
        return;
    }
    lastAcceptedRawFixRef.current = { ...rawCoord, timestampMs: ts };
    if (shouldResetBuffer(bufferRef.current, rawCoord, SNAP_DISTANCE_M)) {
        bufferRef.current.length = 0;
        smoothingStateRef.current = null;
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

// ... in the playback ticker, now with the continuity hint:
const snap = snapToRoute(p.coordinate, routeRef.current, MAX_ROUTE_SNAP_M, lastRouteSegmentIndexRef.current);
lastRouteSegmentIndexRef.current = snap?.segmentIndex ?? null;
```

## 8. Rollback plan

No feature flag / DB config gate exists for CarMarker rendering (it is a pure client-side rendering path with no server-side toggle). Rollback is a plain `git revert` of the single commit — this is safe here (unlike Stripe/wallet/ride-state changes) because:
- No persisted state is written or migrated — every ref (`lastRouteSegmentIndexRef`, `smoothingStateRef`, `lastAcceptedRawFixRef`) is component-local, in-memory, and re-initializes to its default on every screen mount / app restart.
- Nothing server-side or in the database is touched.
- Reverting only restores the pre-2026-09-07 rendering behavior for rider-app's marker (the intersection-flip / jitter it already had); it does not undo any other in-flight change.

## 9. Verification performed

- [x] Automated tests run — unit: ran `npx jest __tests__/vehicleTracking.test.ts __tests__/markerPlayback.test.ts __tests__/carMarkerPositionChange.test.tsx` in `rider-app/` after this change: **3 suites passed, 46/46 tests passed.** `vehicleTracking.test.ts` already exercises `snapToRoute`'s `preferredFromIndex` at the function level (unchanged by this PR); `carMarkerPositionChange.test.tsx` is component-level and exercises the ticker path this diff touches. Also ran `npx tsc --noEmit -p tsconfig.json` and confirmed zero errors referencing `CarMarker.tsx`. Did **not** run the full rider-app suite or `expo lint` — scoped to the affected tests only, per the surgical-change principle; the full suite was not run in this session.
- [x] Blast-radius grep performed — searched `from ['"].*CarMarker['"]|shared/components/CarMarker` repo-wide; 5 rider-app screen call sites found and listed above in §4, no admin-dashboard/backend consumer.
- [x] Reviewed against relevant `CLAUDE.md` convention — this is a frontend rendering path, not the ride state machine, money, RLS, or PIPEDA surface; no such convention applies directly. No new logging/PII surface introduced.
- [ ] Manual repro steps followed in staging — **NOT performed.** No device/simulator was available in this session; see "What was NOT verified" below.
- [ ] Feature-flagged — **not flagged.** Justification: this is a pure client-rendering behavior change with no server dependency, ported from logic already live in production (driver-app) since 2026-09-05 with no reported regression, and rollback is a trivial revert (see §8). Given CLAUDE.md's general preference for flagging non-trivial user-visible changes, this was still a judgment call, not an automatic exemption — flagged here for visibility rather than assumed safe.

## What was NOT verified

- **No on-device or simulator visual confirmation.** This change was verified by reading the code, the already-passing unit/component test suite, and by the fact this exact logic has been running live in driver-app since 2026-09-05. Nobody watched the rider-app map actually render smoother motion on a device.
- **rider-app has no automated visual-regression tooling** (per `CLAUDE.md` §6 of the pre-merge release gates — this is one of the two apps called out as having none at all). This "reasoned about, not screenshotted" disclosure applies in full.
- **No new test was added specifically asserting the ported behavior inside `shared/components/CarMarker.tsx`** (e.g. an intersection-flip regression at the component level, or a jitter-damping assertion at the component level) — coverage relies on the existing function-level tests (`vehicleTracking.test.ts`, and driver-app's `gpsSmoothing.test.ts` for the shared utility) plus the existing component-level `carMarkerPositionChange.test.tsx`, which exercises the same code path but was not extended with a new case targeting this specific diff.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert; no persisted/server state touched)
- [x] Blast radius is stated, not assumed (5 rider-app screens, single surface, no cross-surface impact)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 above)
