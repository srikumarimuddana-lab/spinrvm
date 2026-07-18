# Mobile Durable Recorder Tasks

Required context: read the [master plan](2026-07-17-trip-location-route-integrity.md). Complete Foundation Tasks 1–5 before Tasks 6–11.

### Task 6: Install the SQLite dependency

**Files:** Modify `driver-app/package.json`, `driver-app/yarn.lock`.

- [ ] From `driver-app`, run `npx expo install expo-sqlite expo-crypto`; do not hand-select versions. Use `expo-crypto.randomUUID()` for valid session UUIDs.
- [ ] Run `yarn why expo-sqlite`, `yarn why expo-crypto`, and `npx expo-doctor`; expect one SDK-compatible package for each and no new dependency warning.
- [ ] Inspect the lockfile diff and confirm no unrelated package upgrade.
- [ ] Commit: `build(driver): add durable location storage`.

### Task 7: Durable trip-location outbox

**Files:** Create `driver-app/utils/tripLocationOutbox.ts`, `driver-app/utils/__tests__/tripLocationOutbox.test.ts`.

**Produces:** `TripLocationPoint`, `TripLocationOutbox`, `getTripLocationOutbox()`.

- [ ] Write failing tests for atomic sequence allocation, reuse of one open session per ride across JS contexts, sensor-time preservation, ordered 500-point peek, restart recovery, contiguous acknowledgement, permanent-rejection quarantine, surfaced disk-write failure, and no retry deletion.
- [ ] Run `yarn test utils/__tests__/tripLocationOutbox.test.ts --runInBand`; expect module-not-found.
- [ ] Define the persisted contract:

```ts
export interface TripLocationPoint {
  ride_id: string;
  recording_session_id: string;
  sequence_number: number;
  captured_at: string;
  monotonic_ms: number;
  lat: number; lng: number;
  accuracy: number | null; speed: number | null; heading: number | null;
  altitude: number | null;
  source: 'foreground' | 'background' | 'completion';
  mocked: boolean;
  is_completion_fix: boolean;
}
```

- [ ] Implement SQLite tables `trip_location_sessions`, `trip_location_outbox`, and `trip_location_quarantine`; make `(session_id, sequence_number)` the primary key and allocate sequences inside `withExclusiveTransactionAsync`.
- [ ] Implement `startSession(rideId)`, `enqueue(fix)`, `peek(sessionId, 500)`, `acknowledge(sessionId, ackedThrough, rejected)`, `pendingCount(rideId)`, and `closeSession(rideId)`. Closing marks the session closed but never deletes pending rows.
- [ ] Run the targeted test and `npx tsc --noEmit -p tsconfig.json`; expect PASS. Commit: `feat(driver): add durable trip location outbox`.

### Task 8: Unified recorder and background liveness

**Files:** Create `driver-app/utils/tripLocationRecorder.ts`; modify `driver-app/utils/backgroundLocation.ts`, `driver-app/utils/__tests__/backgroundLocation.test.ts`.

**Consumes:** `TripLocationOutbox`. **Produces:** `startRide`, `recordNativeFix`, `flushPending(transport)`, `captureCompletionFix`, `getRecorderHealth`.

- [ ] Update tests to require `Location.hasStartedLocationUpdatesAsync(TASK_NAME)`, sensor timestamps, outbox enqueue before fetch, retained points on network/401/503 failure, and acknowledgement-driven deletion.
- [ ] Run `yarn test utils/__tests__/backgroundLocation.test.ts --runInBand`; expect failures against registration checks and AsyncStorage queues.
- [ ] `startRide(rideId)` atomically reuses/creates the open SQLite session, allowing foreground and headless JS contexts to share sequence state. Background `recordNativeFix` resolves that open session when no ride ID is available.
- [ ] Implement native-fix conversion with `captured_at: new Date(loc.timestamp).toISOString()` and enqueue before network I/O. Inject the upload transport into `flushPending` so foreground uses the shared API client while the headless task reuses its refresh-token/App Check fetch path without circular imports.
- [ ] Serialize flushes; run at most every 10 seconds or at 25 queued points, plus background/completion flushes, and apply only returned acknowledgements.
- [ ] Replace background AsyncStorage queue functions with recorder calls. Replace every liveness use of `TaskManager.isTaskRegisteredAsync(TASK_NAME)` with `Location.hasStartedLocationUpdatesAsync(TASK_NAME)`; registration may remain only for task-definition diagnostics.
- [ ] Add a 30-second active-trip watchdog that exposes degraded health without logging coordinates. Do not claim force-quit recovery; geofence re-entry may only restart future capture.
- [ ] Run the targeted test and driver lint; expect PASS. Commit: `feat(driver): unify background trip recording`.

### Task 9: Foreground recorder integration

**Files:** Modify `driver-app/hooks/useDriverDashboard.ts`, `driver-app/hooks/__tests__/locationBatch.test.ts`, `driver-app/hooks/__tests__/wsLocationBatch.test.ts`.

- [ ] Rewrite tests to assert foreground fixes use `loc.timestamp`, enter the durable recorder regardless of WebSocket state, survive more than three upload failures, and send only a non-durable live marker over WebSocket.
- [ ] Run both test files; expect failures because the hook uses `new Date()`, clears after three retries, and owns two capped buffers.
- [ ] On active-ride phase entry call `tripLocationRecorder.startRide(rideId)`. Remove `LOCATION_BUFFER_KEY`, `MAX_LOCATION_RETRIES`, REST-buffer clearing, and durable `wsBatchRef` behavior. In the watcher callback call:

```ts
void tripLocationRecorder.recordNativeFix(loc, 'foreground', rideId).then((point) => {
  ws.send(JSON.stringify({ type: 'driver_location', durable: false, ...point }));
  return tripLocationRecorder.flushPending();
});
```

- [ ] Ensure phase remains server-derived and the live payload contains no raw-location logging. Show the existing warning UI when recorder health is degraded.
- [ ] Run both tests and `yarn lint`; expect PASS. Commit: `fix(driver): preserve foreground trip breadcrumbs`.

### Task 10: Keep WebSocket live markers ephemeral

**Files:** Modify `backend/routes/websocket.py`; create `backend/tests/test_websocket_live_location.py`.

- [ ] Add a failing test proving authenticated `driver_location` with `durable:false` updates/fans out the live marker but never calls `buffer_ride_breadcrumb`; legacy messages without the flag still persist.
- [ ] Run `pytest backend/tests/test_websocket_live_location.py -q`; expect the buffer assertion to fail.
- [ ] Guard only the breadcrumb write:

```py
if data.get("durable", True):
    await buffer_ride_breadcrumb(driver_id, data, active_ride=active_ride)
```

- [ ] Keep integrity checks, presence, rider fan-out, and admin throttling unchanged.
- [ ] Run the targeted test and Ruff; expect PASS. Commit: `fix(ws): separate live markers from durable breadcrumbs`.

### Task 11: Driver completion fix and confirmation

**Files:** Modify `driver-app/store/driverStore.ts`, `driver-app/store/__tests__/driverStore.test.ts`, `driver-app/app/driver/(tabs)/index.tsx`.

**Produces:** `completeRide(rideId, offRouteConfirmation?)` with the approved `RideCompletionRequest`.

- [ ] Add tests that completion captures/enqueues a fresh fix, sends final session/sequence and pending count, applies returned `location_ack`, preserves a null fix with explicit confirmation, and retries only after an off-route confirmation response.
- [ ] Run the focused `completeRide` tests; expect the API-body assertions to fail.
- [ ] Call `tripLocationRecorder.captureCompletionFix(rideId)` before POST and send:

```ts
{
  completion_fix: result.point,
  final_session_id: result.point?.recording_session_id ?? null,
  final_sequence_number: result.point?.sequence_number ?? null,
  pending_outbox_count: result.pendingCount,
  off_route_confirmation: confirmation,
}
```

- [ ] In the tab screen, handle backend `completion_confirmation_required`: 200 m–1 km uses confirmation; >1 km requires a selected reason (`rider_requested_stop`, `changed_destination`, or `emergency`). GPS-unavailable confirmation uses `location_unavailable`.
- [ ] After success, apply `location_ack` before closing the session; older pending points remain queued for the completed-ride REST path.
- [ ] Run the store tests, relevant component test, and lint; expect PASS. Commit: `feat(driver): anchor ride completion to a fresh fix`.
