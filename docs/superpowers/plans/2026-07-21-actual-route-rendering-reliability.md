# Actual Route Rendering Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draw only timestamped, recorded GPS route segments on completed-ride maps, prevent marker-only snapshots from hiding valid geometry, and start durable recording at the authoritative ride-start transition.

**Architecture:** The backend remains the source of truth for segmented route evidence and explicitly distinguishes drawable from insufficient geometry. The driver store starts durable capture as soon as the start transition succeeds. Each mobile completed-ride surface renders native `actual_route_segments` first and refits after asynchronously finalized geometry arrives; snapshots remain receipt/export artifacts.

**Tech Stack:** Python 3.12, FastAPI, pytest, React Native, Expo SDK 55, TypeScript, Zustand, Jest, `react-native-maps`.

## Global Constraints

- Completed v2 maps may draw only timestamped GPS evidence; never call Directions or connect booking markers to manufacture a route.
- Preserve every segment boundary; never flatten segments or bridge a GPS gap.
- Pickup, destination, and completion coordinates are validation markers, not substitute polyline geometry.
- Fare settlement and settled billing distance remain immutable.
- Raw GPS coordinates must never appear in logs, diagnostics, analytics, or test output from production data.
- A finalized route is drawable only when at least one segment contains two valid coordinates.
- Historical rides without drawable stored evidence remain `Actual route unavailable`.
- Each task changes at most three files and is committed before the next task starts.

## File Map

- `backend/utils/route_finalizer.py` — classify drawable evidence and suppress marker-only verified snapshots.
- `backend/tests/test_route_finalizer.py` — regression coverage for insufficient evidence and provider-unavailable observed fallbacks.
- `backend/repositories/ride_repo.py` — expose a minimal authorized completion marker with v2 route detail.
- `backend/tests/test_ride_route_contract.py` — verify the completion marker projection without exposing raw completion metadata.
- `shared/types/api/ride.ts` — define the shared completion-marker response shape.
- `driver-app/store/driverStore.ts` — initialize durable recording at both authoritative start transitions.
- `driver-app/store/__tests__/driverStore.test.ts` — state-machine coverage for immediate recorder startup and non-fatal retry behavior.
- `rider-app/app/ride-completed.tsx` — native actual-segment rendering on the immediate completion screen.
- `rider-app/__tests__/ride-completed-route.test.tsx` — completion-screen rendering contract.
- `rider-app/store/rideStore.ts` — carry the minimal completion marker on the rider ride type.
- `rider-app/app/ride-details.tsx` — native actual-segment rendering in rider history.
- `rider-app/__tests__/ride-details-route.test.tsx` — rider-history rendering contract.
- `driver-app/app/driver/ride-detail.tsx` — native actual-segment rendering in driver history.
- `driver-app/__tests__/screens/ride-detail-route.test.tsx` — driver-history rendering contract.

---

### Task 1: Reject Undrawable Finalized Routes

**Files:**
- Modify: `backend/tests/test_route_finalizer.py`
- Modify: `backend/utils/route_finalizer.py`

**Interfaces:**
- Consumes: `SegmentedRoute.observed_segments`, projected segment dictionaries with `coordinates`, and existing `_final_status` / `_quality_projection` output.
- Produces: `_has_drawable_route(route_segments: list[dict]) -> bool`; incomplete reason `insufficient_route_points`; snapshot publication only for drawable projections.

- [ ] **Step 1: Write the failing backend tests**

Add a regression proving one completion-adjacent GPS point cannot produce a verified map:

```python
def test_single_point_route_is_incomplete_and_does_not_publish_snapshot(monkeypatch):
    update = AsyncMock(return_value={"ride_id": "ride_1"})
    publish_snapshot = AsyncMock()
    monkeypatch.setattr(route_finalizer.db_supabase, "get_rows", AsyncMock(return_value=[_point(0, 590)]))
    monkeypatch.setattr(route_finalizer.db_supabase, "get_ride", AsyncMock(return_value=_ride()))
    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", update)
    monkeypatch.setattr(route_finalizer, "_publish_finalized_snapshot", publish_snapshot)
    monkeypatch.setattr(route_finalizer, "_get_route_row", AsyncMock(return_value=_route_row()))
    monkeypatch.setattr(
        route_finalizer,
        "compute_segmented_road_route",
        AsyncMock(return_value={
            "segments": [],
            "distance_km": 0.0,
            "provider": None,
            "failures": [{"segment_index": 0, "reason": "insufficient_points"}],
        }),
    )

    result = _run(route_finalizer.finalize_route("ride_1"))

    payload = update.await_args.args[2]
    assert result["processing_status"] == "incomplete"
    assert payload["route_quality"]["incomplete_reason"] == "insufficient_route_points"
    assert payload["road_matched_segments"][0]["coordinates"] == [[50.445, -104.618]]
    publish_snapshot.assert_not_awaited()
```

Update the provider-unavailable test to express the intended two-point fallback contract:

```python
assert result["processing_status"] == "complete"
assert payload["route_quality"]["incomplete_reason"] is None
assert len(payload["road_matched_segments"][0]["coordinates"]) == 2
```

- [ ] **Step 2: Run the tests and verify the new regression fails for the expected reason**

Run:

```powershell
pytest -q --no-cov backend/tests/test_route_finalizer.py
```

Expected: the single-point test fails because the result is currently `complete` and the snapshot publisher is called.

- [ ] **Step 3: Implement drawable-route classification**

Add the focused predicate and pass its result into quality/status decisions:

```python
def _has_drawable_route(route_segments: list[dict]) -> bool:
    return any(
        isinstance(segment, dict)
        and isinstance(segment.get("coordinates"), list)
        and len(segment["coordinates"]) >= 2
        for segment in route_segments
    )
```

Change `_quality_projection` and `_final_status` to accept `drawable: bool`. Give `missing_completion_fix` precedence, then `insufficient_route_points`, then real road-match failures:

```python
incomplete_reason = (
    "missing_completion_fix"
    if quality.missing_tail
    else "insufficient_route_points"
    if not drawable
    else "road_match_partial_failure"
    if real_failures
    else None
)
```

In `finalize_route`, compute `display_segments` before status/quality, derive `drawable`, and publish only when it is true:

```python
display_segments = _matched_projection(segmented, matched_route)
drawable = _has_drawable_route(display_segments)
processing_status = _final_status(segmented, matched_route, drawable)
quality = _quality_projection(segmented, matched_route, drawable)

if drawable:
    await _publish_finalized_snapshot(
        ride_id,
        ride,
        revision,
        display_segments,
        quality,
        (route_row or {}).get("completion_point"),
        finalized_at=now,
    )
```

- [ ] **Step 4: Run backend route tests**

Run:

```powershell
pytest -q --no-cov backend/tests/test_route_finalizer.py backend/tests/test_ride_route_contract.py backend/tests/test_route_segments.py
```

Expected: 24 tests pass with no route-finalizer assertion failures.

- [ ] **Step 5: Commit the backend correction**

```powershell
git add backend/tests/test_route_finalizer.py backend/utils/route_finalizer.py
git commit -m "fix(routes): reject undrawable finalized routes"
```

---

### Task 2: Project the Authorized Completion Marker

**Files:**
- Modify: `backend/tests/test_ride_route_contract.py`
- Modify: `backend/repositories/ride_repo.py`
- Modify: `shared/types/api/ride.ts`

**Interfaces:**
- Consumes: private `ride_routes.completion_point` metadata after ride authorization.
- Produces: `actual_completion_point?: { latitude: number; longitude: number }`; sequence, timestamp, accuracy, and other raw completion metadata remain private.

- [ ] **Step 1: Write the failing API projection test**

Extend the v2 route-contract fixture with a completion point containing extra private metadata, then assert only the coordinate pair is projected:

```python
"completion_point": {
    "lat": 50.46,
    "lng": -104.63,
    "captured_at": "2026-07-21T12:10:00Z",
    "sequence_number": 42,
},
```

```python
assert ride["actual_completion_point"] == {"latitude": 50.46, "longitude": -104.63}
assert "completion_point" not in ride
assert "captured_at" not in ride["actual_completion_point"]
```

- [ ] **Step 2: Run the contract test and verify RED**

Run:

```powershell
pytest -q --no-cov backend/tests/test_ride_route_contract.py
```

Expected: the new `actual_completion_point` assertion fails because the field is absent.

- [ ] **Step 3: Add the minimal v2 projection**

Inside `_project_route_detail`, validate and project only finite coordinates in legal ranges:

```python
completion = route.get("completion_point")
if isinstance(completion, dict):
    try:
        completion_lat = float(completion["lat"])
        completion_lng = float(completion["lng"])
    except (KeyError, TypeError, ValueError):
        completion_lat = completion_lng = None
    if (
        completion_lat is not None
        and completion_lng is not None
        and -90 <= completion_lat <= 90
        and -180 <= completion_lng <= 180
    ):
        ride["actual_completion_point"] = {
            "latitude": completion_lat,
            "longitude": completion_lng,
        }
```

Add the shared optional field:

```typescript
actual_completion_point?: {
  latitude: number;
  longitude: number;
};
```

- [ ] **Step 4: Run route-contract tests**

Run:

```powershell
pytest -q --no-cov backend/tests/test_ride_route_contract.py backend/tests/test_route_finalizer.py
```

Expected: all route contract and finalizer tests pass.

- [ ] **Step 5: Commit the marker projection**

```powershell
git add backend/tests/test_ride_route_contract.py backend/repositories/ride_repo.py shared/types/api/ride.ts
git commit -m "feat(routes): expose authorized completion marker"
```

---

### Task 3: Start Durable Recording at the Ride Transition

**Files:**
- Modify: `driver-app/store/__tests__/driverStore.test.ts`
- Modify: `driver-app/store/driverStore.ts`

**Interfaces:**
- Consumes: `tripLocationRecorder.startRide(rideId: string): Promise<PendingTripLocationSession>` and existing `recordNonFatal` diagnostics.
- Produces: both `verifyOTP` and `startRide` initialize the active durable recording session immediately after backend success; recorder failure does not revert `trip_in_progress`.

- [ ] **Step 1: Extend the recorder mock and write failing state-transition tests**

Add `startRide: jest.fn()` to the recorder mock and default it to a resolved session:

```typescript
mockTripLocationRecorder.startRide.mockResolvedValue({
  recording_session_id: 'session-123',
  ride_id: 'ride-123',
  opened_at: '2026-07-21T12:00:00.000Z',
  closed_at: null,
});
```

Extend the existing success tests:

```typescript
expect(mockTripLocationRecorder.startRide).toHaveBeenCalledWith('ride-123');
expect(mockTripLocationRecorder.startRide.mock.invocationCallOrder[0]).toBeLessThan(
  mockApi.get.mock.invocationCallOrder[0],
);
```

Add a non-fatal failure test:

```typescript
test('keeps the ride in progress when durable recorder startup needs dashboard retry', async () => {
  mockApi.post.mockResolvedValueOnce({ data: {} });
  mockApi.get.mockResolvedValueOnce(makeActiveRideResponse('in_progress') as any);
  mockTripLocationRecorder.startRide.mockRejectedValueOnce(new Error('database is locked'));

  await useDriverStore.getState().startRide('ride-123');

  expect(useDriverStore.getState().rideState).toBe('trip_in_progress');
});
```

- [ ] **Step 2: Run the focused store tests and verify they fail because `startRide` is not invoked**

Run:

```powershell
cd driver-app
yarn test store/__tests__/driverStore.test.ts --runInBand
```

Expected: new recorder-start assertions fail with zero calls.

- [ ] **Step 3: Start recording in both backend-confirmed transition paths**

Immediately after successful `/verify-otp` and `/start` responses, transition local state and initialize recording before fetching the active ride:

```typescript
set({ rideState: 'trip_in_progress' });
try {
  await tripLocationRecorder.startRide(rideId);
} catch (recordingError: unknown) {
  recordNonFatal(recordingError, {
    store: 'driverStore',
    action: 'startTripLocationRecording',
    rideId,
  });
}
await get().fetchActiveRide();
```

Use the same action label and behavior in both paths so the existing dashboard effect can retry idempotently.

- [ ] **Step 4: Run the store suite**

Run:

```powershell
cd driver-app
yarn test store/__tests__/driverStore.test.ts --runInBand
```

Expected: all state-machine tests pass, including recorder rejection without ride rollback.

- [ ] **Step 5: Commit immediate recorder startup**

```powershell
git add driver-app/store/__tests__/driverStore.test.ts driver-app/store/driverStore.ts
git commit -m "fix(driver): start route recording with the trip"
```

---

### Task 4: Render Native Actual Geometry on Rider Completion

**Files:**
- Modify: `rider-app/__tests__/ride-completed-route.test.tsx`
- Modify: `rider-app/app/ride-completed.tsx`
- Modify: `rider-app/store/rideStore.ts`

**Interfaces:**
- Consumes: `actualSegments: ReactNativeRouteCoordinate[][]` and `mapCoordinates` already derived through `toReactNativeSegments`.
- Produces: the immediate completion card always uses `MapView` for v2 route presentation and refits after asynchronously arriving segments.

- [ ] **Step 1: Write failing source-contract assertions**

Replace snapshot-precedence expectations with native-geometry expectations:

```typescript
expect(screenSource).toContain('const [routeMapReady, setRouteMapReady] = useState(false)');
expect(screenSource).toContain('if (!routeMapReady || mapCoordinates.length < 2) return');
expect(screenSource).toContain('setRouteMapReady(true)');
expect(screenSource).not.toContain('{routeSnapshotUrl ? (');
expect(screenSource).toContain('actualSegments.map((coordinates, index) => (');
expect(screenSource).toContain('currentRide.actual_completion_point');
expect(rideStoreSource).toContain('actual_completion_point?:');
```

Keep the assertions that reject `MapViewDirections`, flattened segments, and planned v2 fallback.

- [ ] **Step 2: Run the completion route test and verify RED**

Run:

```powershell
cd rider-app
yarn test __tests__/ride-completed-route.test.tsx --runInBand
```

Expected: assertions for `routeMapReady` and snapshot-independent rendering fail.

- [ ] **Step 3: Make actual segments primary and refit on arrival**

Add map readiness state and a geometry-change effect:

```typescript
const [routeMapReady, setRouteMapReady] = useState(false);

useEffect(() => {
  if (!routeMapReady || mapCoordinates.length < 2) return;
  mapRef.current?.fitToCoordinates(mapCoordinates, {
    edgePadding: { top: 30, right: 30, bottom: 30, left: 30 },
    animated: false,
  });
}, [routeMapReady, mapCoordinates]);
```

Render the `MapView` directly instead of selecting `<Image>` from `routeSnapshotUrl`. Change `onMapReady` to `setRouteMapReady(true)`; the effect performs both initial and late-data fitting. Preserve the independent map over `actualSegments`, legacy-only planned segments, and all three markers.

Add the optional `actual_completion_point` coordinate pair to the local `Ride` interface and render its orange completion marker independently of the route line:

```typescript
{currentRide.actual_completion_point && (
  <Marker coordinate={currentRide.actual_completion_point} anchor={{ x: 0.5, y: 0.5 }}>
    <View style={[styles.mapPin, { backgroundColor: '#F59E0B' }]}>
      <Ionicons name="checkmark" size={14} color="#FFF" />
    </View>
  </Marker>
)}
```

Make route copy segment-driven:

```typescript
const routeStatus = hasActualRoute
  ? routeQuality
  : isV2Route
    ? routeIsProcessing
      ? 'Actual route processing'
      : 'Actual route unavailable'
    : 'Planned route preview';
```

- [ ] **Step 4: Run the focused rider completion tests**

Run:

```powershell
cd rider-app
yarn test __tests__/ride-completed-route.test.tsx __tests__/useCompletedRouteRefresh.test.tsx --runInBand
```

Expected: both suites pass and no Directions fallback is present.

- [ ] **Step 5: Commit the rider completion correction**

```powershell
git add rider-app/__tests__/ride-completed-route.test.tsx rider-app/app/ride-completed.tsx rider-app/store/rideStore.ts
git commit -m "fix(rider): render actual segments on completion"
```

---

### Task 5: Render Native Actual Geometry in Rider History

**Files:**
- Modify: `rider-app/__tests__/ride-details-route.test.tsx`
- Modify: `rider-app/app/ride-details.tsx`

**Interfaces:**
- Consumes: the same normalized `actualSegments` contract used by Task 3.
- Produces: rider history uses native route evidence while `buildReceiptHtml` retains revision-matched snapshot support for PDF export.

- [ ] **Step 1: Write failing rider-history assertions**

Add assertions scoped to the screen source:

```typescript
expect(source).toContain('const [routeMapReady, setRouteMapReady] = useState(false)');
expect(source).toContain('if (!routeMapReady || mapCoordinates.length < 2) return');
expect(source).toContain('setRouteMapReady(true)');
expect(source).toContain('hasActualRoute && actualSegments.map');
expect(source).toContain('ride.actual_completion_point');
expect(source).not.toContain('{routeSnapshotUrl ? (');
```

Retain PDF assertions for `Actual route (revision ${routeRevision})` and `routeQualityLabel` so this task cannot accidentally remove receipt snapshots.

- [ ] **Step 2: Run the rider-history test and verify RED**

Run:

```powershell
cd rider-app
yarn test __tests__/ride-details-route.test.tsx --runInBand
```

Expected: readiness/refit assertions fail and snapshot precedence is still detected.

- [ ] **Step 3: Implement native history rendering**

Add the same readiness state and refit effect as Task 4 with 30-pixel edge padding. Remove only the component map's `routeSnapshotUrl ? <Image> : <MapView>` branch and render `MapView` directly. Do not change the separate `buildReceiptHtml` function, where signed snapshot images remain valid export artifacts.

Change `onMapReady` to:

```typescript
onMapReady={() => setRouteMapReady(true)}
```

Keep actual segments independent and planned geometry restricted to `!isV2Route && !hasActualRoute`.

Render `ride.actual_completion_point`, when present, as an independent orange marker using the existing `styles.pin` container and a checkmark icon. Do not add it to a route segment or connect it to the destination.

- [ ] **Step 4: Run rider history and refresh suites**

Run:

```powershell
cd rider-app
yarn test __tests__/ride-details-route.test.tsx __tests__/useCompletedRouteRefresh.test.tsx --runInBand
```

Expected: both suites pass; PDF snapshot assertions remain green.

- [ ] **Step 5: Commit rider-history rendering**

```powershell
git add rider-app/__tests__/ride-details-route.test.tsx rider-app/app/ride-details.tsx
git commit -m "fix(rider): render actual segments in ride history"
```

---

### Task 6: Render Native Actual Geometry in Driver History

**Files:**
- Modify: `driver-app/__tests__/screens/ride-detail-route.test.tsx`
- Modify: `driver-app/app/driver/ride-detail.tsx`

**Interfaces:**
- Consumes: `actualSegments` and `mapCoordinates` from the shared segment normalizer.
- Produces: driver history follows the same actual-only map contract as both rider screens.

- [ ] **Step 1: Write failing driver-history assertions**

Add:

```typescript
expect(source).toContain('const [routeMapReady, setRouteMapReady] = useState(false)');
expect(source).toContain('if (!routeMapReady || mapCoordinates.length < 2) return');
expect(source).toContain('setRouteMapReady(true)');
expect(source).not.toContain('{routeSnapshotUrl ? (');
expect(source).toContain('actualSegments.map((coordinates, index) => (');
expect(source).toContain('ride.actual_completion_point');
```

Keep assertions that planned geometry is legacy-only and no Directions component is used.

- [ ] **Step 2: Run the driver route test and verify RED**

Run:

```powershell
cd driver-app
yarn test __tests__/screens/ride-detail-route.test.tsx --runInBand
```

Expected: readiness/refit assertions fail and snapshot precedence is detected.

- [ ] **Step 3: Implement native driver-history rendering**

Add readiness state and a refit effect with 40-pixel edge padding:

```typescript
const [routeMapReady, setRouteMapReady] = useState(false);

useEffect(() => {
  if (!routeMapReady || mapCoordinates.length < 2) return;
  mapRef.current?.fitToCoordinates(mapCoordinates, {
    edgePadding: { top: 40, right: 40, bottom: 40, left: 40 },
    animated: false,
  });
}, [routeMapReady, mapCoordinates]);
```

Render `MapView` directly, use `onMapReady={() => setRouteMapReady(true)}`, retain independent actual segments and legacy-only planned segments, and make route status depend on actual geometry rather than snapshot availability.

Render `ride.actual_completion_point`, when present, as an independent orange marker using the existing map-pin style and a checkmark icon. It remains a guardrail marker and is never added to `actualSegments`.

- [ ] **Step 4: Run driver route and lifecycle tests**

Run:

```powershell
cd driver-app
yarn test __tests__/screens/ride-detail-route.test.tsx store/__tests__/driverStore.test.ts --runInBand
```

Expected: both suites pass.

- [ ] **Step 5: Commit driver-history rendering**

```powershell
git add driver-app/__tests__/screens/ride-detail-route.test.tsx driver-app/app/driver/ride-detail.tsx
git commit -m "fix(driver): render actual segments in ride history"
```

---

### Task 7: Integrated Verification and Graph Refresh

**Files:**
- Verify only; Graphify tracked outputs may update if the repository no longer ignores them.

**Interfaces:**
- Consumes: all five committed changes.
- Produces: evidence that actual geometry, capture startup, route truthfulness, and repository graph are current.

- [ ] **Step 1: Run the focused backend suite without the repository-wide coverage gate**

```powershell
pytest -q --no-cov backend/tests/test_route_finalizer.py backend/tests/test_ride_route_contract.py backend/tests/test_route_segments.py backend/tests/test_ride_completion_location.py
```

Expected: all selected tests pass.

- [ ] **Step 2: Run the focused rider suites**

```powershell
cd rider-app
yarn test __tests__/ride-completed-route.test.tsx __tests__/ride-details-route.test.tsx __tests__/useCompletedRouteRefresh.test.tsx --runInBand
```

Expected: all suites pass.

- [ ] **Step 3: Run the focused driver suites**

```powershell
cd driver-app
yarn test __tests__/screens/ride-detail-route.test.tsx store/__tests__/driverStore.test.ts --runInBand
```

Expected: all suites pass.

- [ ] **Step 4: Run whitespace and targeted static checks**

```powershell
git diff --check
cd rider-app
yarn tsc --noEmit
cd ..\driver-app
yarn tsc --noEmit
```

Expected: no new TypeScript error points to files changed by this plan. Record pre-existing unrelated failures separately rather than claiming a clean global typecheck.

- [ ] **Step 5: Refresh Graphify after code changes**

```powershell
python -m graphify update .
```

Expected: Graphify completes successfully and reports the current HEAD. If Graphify outputs are ignored, no commit is needed.

- [ ] **Step 6: Confirm repository state and deployment requirements**

```powershell
git status -sb
git log -6 --oneline
```

Expected: only intentional Graphify output, if tracked, remains. Backend code requires normal backend deployment; mobile JS requires a production EAS OTA update. Historical rides with no stored intermediate GPS remain unavailable by design.
