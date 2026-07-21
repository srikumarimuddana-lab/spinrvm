# Completed Ride Actual-Route-Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent completed v2 ride screens from drawing booking-time planned geometry and refresh briefly until timestamp-derived actual route geometry is finalized.

**Architecture:** Add a shared bounded-refresh hook that recognizes only completed v2 routes in `pending` or `processing` state with no actual geometry. Each mobile surface will use actual segments or a revision-matched snapshot; while those are unavailable it will render pickup/drop-off markers only and refresh through the shared hook. Legacy pre-v2 rides retain their explicitly labelled planned preview.

**Tech Stack:** TypeScript, React hooks, React Native Maps, Jest, Expo rider and driver apps.

## Global Constraints

- Completed rides with `route_schema_version >= 2` must never draw `planned_route_polyline`.
- Actual segments must remain independent polylines; never flatten segment boundaries.
- Refresh every 3 seconds for no longer than 60 seconds.
- Stop refreshing on actual geometry, a terminal processing state, unmount, or timeout.
- Legacy rides with `route_schema_version < 2` retain the labelled planned preview.
- No Google Directions reconstruction for completed rides.

---

### Task 1: Shared bounded actual-route refresh hook

**Files:**
- Create: `shared/hooks/useCompletedRouteRefresh.ts`
- Create: `shared/hooks/__tests__/useCompletedRouteRefresh.test.tsx`

**Interfaces:**
- Consumes: a ride-shaped object and an async or synchronous refresh callback.
- Produces: `shouldRefreshCompletedRoute(ride): boolean` and `useCompletedRouteRefresh(ride, refresh): void`.

- [ ] **Step 1: Write the failing tests**

Test the pure predicate for completed v2 pending routes, rejection of legacy/terminal/actual routes, and the hook's 3-second bounded timer with Jest fake timers:

```tsx
expect(shouldRefreshCompletedRoute({
  status: 'completed', route_schema_version: 2,
  route_geometry_status: 'pending', actual_route_segments: [],
})).toBe(true);
expect(shouldRefreshCompletedRoute({
  status: 'completed', route_schema_version: 2,
  route_geometry_status: 'complete', actual_route_segments: [],
})).toBe(false);
```

- [ ] **Step 2: Run the test and verify RED**

Run: `cd rider-app && yarn test ../shared/hooks/__tests__/useCompletedRouteRefresh.test.tsx --runInBand`

Expected: FAIL because `useCompletedRouteRefresh` does not exist.

- [ ] **Step 3: Implement the minimal hook**

```ts
export const COMPLETED_ROUTE_REFRESH_INTERVAL_MS = 3_000;
export const COMPLETED_ROUTE_REFRESH_LIMIT_MS = 60_000;

export function shouldRefreshCompletedRoute(ride: CompletedRouteLike | null | undefined): boolean {
  return Boolean(
    ride?.status === 'completed' &&
    Number(ride.route_schema_version || 0) >= 2 &&
    (ride.route_geometry_status === 'pending' || ride.route_geometry_status === 'processing') &&
    normalizeActualRouteSegments(ride.actual_route_segments).length === 0
  );
}
```

The hook stores the latest callback in a ref, starts one interval only while the predicate is true, clears it after 60 seconds, and clears both timers on unmount.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `cd rider-app && yarn test ../shared/hooks/__tests__/useCompletedRouteRefresh.test.tsx --runInBand`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/hooks/useCompletedRouteRefresh.ts shared/hooks/__tests__/useCompletedRouteRefresh.test.tsx
git commit -m "feat(routes): add bounded actual-route refresh"
```

### Task 2: Rider completion screen actual-only contract

**Files:**
- Modify: `rider-app/app/ride-completed.tsx`
- Modify: `rider-app/__tests__/ride-completed-route.test.tsx`

**Interfaces:**
- Consumes: `useCompletedRouteRefresh(currentRide, () => fetchRide(rideId))` from Task 1.
- Produces: a completed v2 map that shows only actual segments/snapshot or marker-only processing state.

- [ ] **Step 1: Extend the source contract test and verify RED**

Assert the screen imports and invokes `useCompletedRouteRefresh`, derives `isV2Route`, chooses planned segments only when `!isV2Route`, and has no completed-v2 planned polyline branch.

Run: `cd rider-app && yarn test __tests__/ride-completed-route.test.tsx --runInBand`

Expected: FAIL because the screen still maps `plannedSegments` whenever actual segments are absent.

- [ ] **Step 2: Implement actual-only rendering**

Use:

```ts
const isV2Route = toNum(currentRide?.route_schema_version) >= 2;
const displaySegments = hasActualRoute ? actualSegments : isV2Route ? [] : plannedSegments;
const routeLabel = hasActualRoute ? 'Actual route' : isV2Route ? 'Actual route' : 'Planned route';
useCompletedRouteRefresh(currentRide, () => rideId ? fetchRide(rideId) : undefined);
```

Fit and draw `displaySegments`. For v2 with no actual geometry, draw markers only and show `Actual route processing` for pending/processing, otherwise `Actual route unavailable`.

- [ ] **Step 3: Run rider completion test and typecheck**

Run: `cd rider-app && yarn test __tests__/ride-completed-route.test.tsx --runInBand`

Run: `cd rider-app && yarn tsc --noEmit`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add rider-app/app/ride-completed.tsx rider-app/__tests__/ride-completed-route.test.tsx
git commit -m "fix(rider): show actual-only completed route"
```

### Task 3: Rider history/detail actual-only contract

**Files:**
- Modify: `rider-app/app/ride-details.tsx`
- Modify: `rider-app/__tests__/ride-details-route.test.tsx`

**Interfaces:**
- Consumes: `useCompletedRouteRefresh(ride, fetchRide)` from Task 1.
- Produces: actual-only v2 history maps and unchanged legacy planned previews.

- [ ] **Step 1: Extend the source contract test and verify RED**

Assert `isV2Route`, bounded refresh invocation, `displaySegments`, marker-only v2 fallback, and absence of `!hasActualRoute && plannedSegments.map`.

Run: `cd rider-app && yarn test __tests__/ride-details-route.test.tsx --runInBand`

Expected: FAIL against the current planned fallback.

- [ ] **Step 2: Implement actual-only history rendering**

Use the same `isV2Route` and `displaySegments` contract as Task 2. Invoke the shared refresh hook with the existing `fetchRide` function. Retain planned geometry only for `route_schema_version < 2`.

- [ ] **Step 3: Run focused tests and typecheck**

Run: `cd rider-app && yarn test __tests__/ride-details-route.test.tsx __tests__/ride-completed-route.test.tsx --runInBand`

Run: `cd rider-app && yarn tsc --noEmit`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add rider-app/app/ride-details.tsx rider-app/__tests__/ride-details-route.test.tsx
git commit -m "fix(rider): remove planned route from v2 history"
```

### Task 4: Driver history/detail actual-only contract

**Files:**
- Modify: `driver-app/app/driver/ride-detail.tsx`
- Modify: `driver-app/__tests__/screens/ride-detail-route.test.tsx`

**Interfaces:**
- Consumes: `useCompletedRouteRefresh(ride, loadRide)` from Task 1.
- Produces: actual-only v2 driver history maps and unchanged legacy planned previews.

- [ ] **Step 1: Extend the source contract test and verify RED**

Assert the shared hook, `isV2Route`, marker-only v2 fallback, and removal of the unconditional planned fallback.

Run: `cd driver-app && yarn test __tests__/screens/ride-detail-route.test.tsx --runInBand`

Expected: FAIL against the current planned fallback.

- [ ] **Step 2: Implement actual-only driver rendering**

Use `displaySegments = hasActualRoute ? actualSegments : isV2Route ? [] : plannedSegments`, invoke the shared refresh hook, and use processing/unavailable status copy for v2 routes without actual geometry.

- [ ] **Step 3: Run focused tests and typecheck**

Run: `cd driver-app && yarn test __tests__/screens/ride-detail-route.test.tsx --runInBand`

Run: `cd driver-app && yarn tsc --noEmit`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add driver-app/app/driver/ride-detail.tsx driver-app/__tests__/screens/ride-detail-route.test.tsx
git commit -m "fix(driver): show actual-only completed route"
```

### Task 5: Final verification and graph refresh

**Files:**
- Modify through generated output only if Graphify reports tracked changes: `graphify-out/graph.json`, `graphify-out/GRAPH_REPORT.md`, `graphify-out/manifest.json`

**Interfaces:**
- Consumes: all four completed tasks.
- Produces: verified mobile route contracts and a current code graph.

- [ ] **Step 1: Run all focused tests**

```bash
cd rider-app && yarn test __tests__/ride-completed-route.test.tsx __tests__/ride-details-route.test.tsx ../shared/hooks/__tests__/useCompletedRouteRefresh.test.tsx --runInBand
cd driver-app && yarn test __tests__/screens/ride-detail-route.test.tsx --runInBand
```

Expected: all tests PASS.

- [ ] **Step 2: Run both typechecks**

```bash
cd rider-app && yarn tsc --noEmit
cd driver-app && yarn tsc --noEmit
```

Expected: both commands exit 0.

- [ ] **Step 3: Refresh Graphify**

Run from repository root:

```bash
python -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"
```

- [ ] **Step 4: Inspect final diff and commit generated graph changes separately if present**

```bash
git status --short
git diff --check
git add graphify-out/graph.json graphify-out/GRAPH_REPORT.md graphify-out/manifest.json
git commit -m "chore(graphify): refresh route map graph"
```
