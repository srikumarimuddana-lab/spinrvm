# Consumer Surface and Operations Tasks

Required context: complete Tasks 1–20. Execute Tasks 21–30 in order.

### Task 21: Shared route rendering utility

**Files:** Create `shared/utils/routeSegments.ts`; modify `shared/package.json`; create `admin-dashboard/src/lib/__tests__/route-segments.test.ts`.

- [ ] Test invalid coordinates are rejected, segment boundaries survive normalization, React Native coordinates are `{latitude,longitude}`, GeoJSON is `MultiLineString` in `[lng,lat]`, and incomplete quality produces approved copy.
- [ ] Run `npm test -- src/lib/__tests__/route-segments.test.ts` in `admin-dashboard`; expect missing export.
- [ ] Implement and export `normalizeActualRouteSegments`, `toReactNativeSegments`, `toGeoJsonMultiLineString`, and `routeQualityLabel`; add `"./utils/routeSegments"` to shared exports.
- [ ] Run the test and `npx tsc --project ../shared/tsconfig.build.json --noEmit`; expect PASS. Commit: `feat(shared): normalize segmented route geometry`.

### Task 22: Rider ride details and client PDF

**Files:** Modify `rider-app/app/ride-details.tsx`; create `rider-app/app/__tests__/ride-details-route.test.tsx`.

- [ ] Test snapshot preference, multiple actual polylines, no chord, planned-only label, coverage/lifecycle labels, incomplete note, and snapshot/note in `buildReceiptHtml`.
- [ ] Run `yarn test app/__tests__/ride-details-route.test.tsx --runInBand`; expect failures against flat `route_polyline` and mapless PDF HTML.
- [ ] Render `actual_route_segments` through the shared utility. Fit the map to flattened points only for viewport calculation; render one `<Polyline>` per segment.
- [ ] Remove completed-ride endpoint Directions as an actual fallback. Use `planned_route_polyline` only with “Planned route”; export `buildReceiptHtml` and include revisioned image plus quality note.
- [ ] Run test and lint; expect PASS. Commit: `fix(rider): show truthful routes in ride details`.

### Task 23: Rider ride-completed surface

**Files:** Modify `rider-app/app/ride-completed.tsx`, `rider-app/store/rideStore.ts`; create `rider-app/app/__tests__/ride-completed-route.test.tsx`.

- [ ] Test one polyline per actual segment, snapshot revision, incomplete note, planned label, and lifecycle duration independent from GPS coverage.
- [ ] Run the targeted test; expect flat-polyline/fallback failures.
- [ ] Add the optional v2 route fields to the local `Ride` interface. Use the shared segment utility and remove transparent Directions reconstruction for completed actual routes. Keep planned preview separate and labelled.
- [ ] Run test and lint; expect PASS. Commit: `fix(rider): preserve route gaps after completion`.

### Task 24: Driver ride-details surface

**Files:** Modify `driver-app/app/driver/ride-detail.tsx`; create `driver-app/app/driver/__tests__/ride-detail-route.test.tsx`.

- [ ] Test snapshot preference, segmented actual route, planned-only fallback label, completion marker, incomplete tail copy, coverage duration, and lifecycle duration.
- [ ] Run the targeted test; expect current flattened-route assertions to fail.
- [ ] Render v2 segments with the shared utility, never endpoint Directions as actual, and show planned drop-off separately when it differs from completion.
- [ ] Run test and lint; expect PASS. Commit: `fix(driver): show verified completed route segments`.

### Task 25: Admin route map and labels

**Files:** Modify `admin-dashboard/src/app/dashboard/rides/_components/ride-route-map.tsx`, `admin-dashboard/src/app/dashboard/rides/_components/ride-detail-modal.tsx`; create `admin-dashboard/src/app/dashboard/rides/_components/ride-route-map.test.tsx`.

- [ ] Test actual geometry is `MultiLineString`, no line bridges two segments, completion/planned markers differ, and the card shows 49-minute lifecycle plus 13-minute GPS coverage for the regression fixture.
- [ ] Run the targeted Vitest file; expect `LineString` and duration-label failures.
- [ ] Change map props from flat `tripTrail` to `tripSegments`; build GeoJSON with the shared utility. Keep pickup and planned layers independent.
- [ ] In the modal, prefer v2 segments, display route revision/quality/max gap, compute lifecycle from `ride_started_at`/`ride_completed_at`, and label phase duration as GPS coverage.
- [ ] Run targeted test, lint, and `npm run build`; expect PASS. Commit: `fix(admin): render segmented actual ride routes`.

### Task 26: Active-ride GPS heartbeat and monitor

**Files:** Modify `backend/utils/breadcrumbs.py`; create `backend/utils/location_gap_monitor.py`, `backend/tests/test_location_gap_monitor.py`.

- [ ] Test accepted inserts refresh a non-PII Redis heartbeat, duplicate/no-row inserts do not, >30-second gaps alert once, recovery clears suppression, and Redis/DB failure is visible without coordinates.
- [ ] Run `pytest backend/tests/test_location_gap_monitor.py -q`; expect module-not-found.
- [ ] After a newly inserted active-ride point, set `ride:{ride_id}:gps_heartbeat` with receipt time. The monitor batch-loads active rides, checks heartbeats concurrently, sends targeted driver/admin events, and uses `ride:{ride_id}:gps_gap_alerted` for suppression.
- [ ] Emit `spinr.rides.location_gap.count` with IDs only in logs. Run tests and Ruff; expect PASS. Commit: `feat(ops): detect active ride GPS gaps`.

### Task 27: Wire the GPS gap-monitor loop

**Files:** Modify `backend/core/lifespan.py`; create `backend/tests/test_lifespan_location_gap_monitor.py`.

- [ ] Add a failing static/startup test requiring `_spawn("location_gap_monitor (15s)", location_gap_monitor_loop)` and watchdog registration.
- [ ] Register the 15-second replay-safe loop with the existing dual import/startup error pattern.
- [ ] Run the targeted test and Ruff; expect PASS. Commit: `feat(ops): start GPS gap monitoring`.

### Task 28: Three-year derived-route retention

**Files:** Create `backend/migrations/234_trip_route_integrity_retention.sql`; modify `backend/utils/retention_purge.py`, `backend/tests/test_retention_purge.py`.

- [ ] Test the migration defines a `SECURITY DEFINER` function with pinned `search_path`, dry-run behavior, audit output, and clearing of segments/completion/quality/snapshot fields after three years.
- [ ] Test the daily purge invokes both existing `purge_pii_retention` and new `purge_trip_route_integrity_retention`, surfacing either DB failure.
- [ ] Run `pytest backend/tests/test_retention_purge.py -q`; expect the second-RPC assertion to fail.
- [ ] Implement the append-only migration and second RPC call; do not alter earlier migrations or weaken existing 90-day raw-point purge.
- [ ] Run tests and Ruff; expect PASS. Commit: `feat(privacy): purge derived trip routes after three years`.

### Task 29: End-to-end regression and device matrix

**Files:** Create `backend/tests/test_e2e_trip_route_integrity.py`, `docs/testing/trip-route-integrity-device-matrix.md`.

- [ ] Build the exact 49-minute fixture: 293 in-trip points, 26- and 10-minute gaps, late upload, duplicate replay, 5.25 km jump, and fresh completion fix.
- [ ] Assert exactly-once storage, capture-time order, separate observed/matched segments, low confidence, missing coverage, no chord, revision increment, and identical snapshot revision in every API/receipt projection.
- [ ] Run `pytest backend/tests/test_e2e_trip_route_integrity.py -q`; expect PASS only after Tasks 1–28.
- [ ] Document physical tests for foreground, background, lock screen, offline recovery, low-power mode, permission downgrade, process restart, force-quit limitation, completion away from planned drop-off, and receipt verification on supported iOS/Android versions.
- [ ] Commit: `test(routes): cover complete trip integrity lifecycle`.

### Task 30: Full verification, Graphify, and rollout gate

**Files:** Update only generated `graphify-out/graph.json`, `graphify-out/GRAPH_REPORT.md`, `graphify-out/manifest.json` if rebuilding changes them.

- [ ] Run `pytest -m "not slow"` and `ruff check .` from `backend`.
- [ ] Run `yarn test --runInBand`, `yarn lint`, and `npx expo-doctor` from `driver-app`; run tests/lint from `rider-app`.
- [ ] Run shared TypeScript build; run `npm test`, `npm run lint`, and `npm run build` from `admin-dashboard`.
- [ ] Run `python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"` from the repository root and inspect generated changes.
- [ ] Confirm shadow mode records v2/legacy comparisons without exposing v2, then enable `on` in staging and verify capture coverage, maximum gaps, completion distance, finalizer retries, snapshot revision, and receipt route status.
- [ ] Commit generated graph outputs if changed: `chore(graph): rebuild route integrity graph`.
- [ ] Production enablement requires zero chord regressions, no unacknowledged-point loss in physical tests, all acceptance gates passing, and a documented rollback to `route_integrity_v2_mode='shadow'`.
