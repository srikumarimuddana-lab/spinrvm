# Route Finalization Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve chronological fallback GPS evidence, retry incomplete OSRM reconstruction without publishing partial snapshots, and safely regenerate affected completed rides.

**Architecture:** Normalize legacy breadcrumbs by their device timestamp before v2 sequence validation, and include `captured_at` in the compatibility WebSocket fallback. The finalizer keeps unresolved reconstructions pending with bounded backoff and publishes statistics/snapshots only for complete revisions. Shared UI utilities distinguish retrying from terminal failure and continue polling while partial geometry is pending.

**Tech Stack:** Python 3.12/FastAPI/pytest, React Native/Expo/TypeScript/Jest, Supabase Postgres/Storage, OSRM Match/Route/Nearest.

## Global Constraints

- Pickup and authorized completion remain the route endpoint guardrails.
- Keep `MAX_INFERRED_CONNECTORS = 20`; OSRM returned HTTP 200 and false fragmentation caused the overflow.
- Never use OSRM Trip or a public OSRM instance for production GPS.
- Never log raw coordinates or exact addresses.
- Never change settled fare, tax, tip, earnings, payment, or lifecycle fields.
- Each implementation task modifies at most two files and ends in one commit.

---

### Task 1: Chronologically normalize legacy breadcrumbs

**Files:**
- Modify: `backend/utils/route_segments.py`
- Test: `backend/tests/test_route_segments.py`

**Interfaces:**
- Consumes legacy rows with `timestamp` and no durable identity.
- Produces `segment_route(...)` output ordered by parsed capture time without false clock regressions.

- [ ] Add a failing test with shuffled legacy rows whose `captured_at` values are null and whose `timestamp` values are chronological only after sorting. Assert every point survives, order is chronological, and `rejected_point_count == 0`.
  ```python
  def _legacy_point(seconds: int) -> dict:
      point = _point(seconds // 60, seconds)
      return {"timestamp": point["captured_at"], "lat": point["lat"], "lng": point["lng"]}

  rows = [_legacy_point(120), _legacy_point(0), _legacy_point(60)]
  segmented = segment_route(rows, _lifecycle(), completion_point=None)
  assert [p["timestamp"] for p in _flatten(segmented)] == [
      _legacy_point(0)["timestamp"], _legacy_point(60)["timestamp"], _legacy_point(120)["timestamp"]
  ]
  assert segmented.quality.rejected_point_count == 0
  ```
- [ ] Run `python -m pytest -q --no-cov backend/tests/test_route_segments.py -k shuffled_legacy` and verify the current code rejects points.
- [ ] Extend `_ParsedPoint` with `is_legacy: bool`; skip sequence-clock-regression rejection for legacy rows while retaining it for v2 sessions. Keep the final timestamp-first sort.
  ```python
  if prior is not None and not candidate.is_legacy and candidate.captured_at < prior.captured_at:
      rejected.append(_reject(candidate.point, "clock_regression"))
      continue
  ```
- [ ] Run `python -m pytest -q --no-cov backend/tests/test_route_segments.py` and verify all tests pass.
- [ ] Commit with `fix(routes): order legacy breadcrumbs by capture time`.

### Task 2: Preserve capture time in the WebSocket fallback

**Files:**
- Modify: `driver-app/hooks/useDriverDashboard.ts`
- Test: `driver-app/hooks/__tests__/wsLocationBatch.test.ts`

**Interfaces:**
- Consumes `TripLocationPoint.captured_at` after durable enqueue.
- Produces a legacy-compatible `driver_location` message containing `captured_at` but no `recording_session_id` or `sequence_number`.

- [ ] Add a failing source-contract test asserting the fallback payload contains `captured_at: point.captured_at` and still omits v2 identity fields.
  ```typescript
  const tripPayload = hookSource.slice(
    hookSource.indexOf("type: 'driver_location',"),
    hookSource.indexOf('}));', hookSource.indexOf("type: 'driver_location',")),
  );
  expect(hookSource).toContain('captured_at: point.captured_at');
  expect(tripPayload).not.toContain('recording_session_id:');
  expect(tripPayload).not.toContain('sequence_number:');
  ```
- [ ] Run `yarn test hooks/__tests__/wsLocationBatch.test.ts --runInBand` and verify it fails on missing capture time.
- [ ] Add only `captured_at: point.captured_at` to the trip WebSocket payload.
  ```typescript
  captured_at: point.captured_at,
  ```
- [ ] Run the focused test and `yarn test utils/__tests__/tripLocationRecorder.test.ts --runInBand`.
- [ ] Commit with `fix(driver): preserve fallback capture timestamps`.

### Task 3: Retry unresolved reconstruction and prohibit partial snapshots

**Files:**
- Modify: `backend/utils/route_finalizer.py`
- Test: `backend/tests/test_route_finalizer.py`

**Interfaces:**
- Produces pending retry rows with diagnostic partial geometry and `route_quality.reconstruction_status = "retrying"`.
- Produces terminal incomplete rows after `MAX_ROUTE_FINALIZER_RETRIES = 5` with `reconstruction_status = "failed"`.

- [ ] Add failing tests proving unresolved `failed_gaps` schedules a retry, does not call `_recompute_ride_distance_stats`, does not call `_publish_finalized_snapshot`, and preserves non-sensitive quality diagnostics.
  ```python
  assert result["processing_status"] == "pending"
  assert payload["route_quality"]["reconstruction_status"] == "retrying"
  assert payload["next_retry_at"] is not None
  recompute.assert_not_awaited()
  publish_snapshot.assert_not_awaited()
  ```
- [ ] Add a failing exhaustion test proving attempt five becomes terminal `incomplete` without a snapshot.
- [ ] Run the two tests and verify RED.
- [ ] Add the retry limit and build the atomic projection payload: pending attempts increment `retry_count`, set exponential `next_retry_at`, clear claim/finalization/snapshot references, and retain partial diagnostics. Exhausted attempts set terminal incomplete state.
  ```python
  MAX_ROUTE_FINALIZER_RETRIES = 5
  def _retry_delay_seconds(retry_count: int) -> int:
      return min(300, 15 * (2 ** min(retry_count - 1, 4)))

  retryable = processing_status == "incomplete" and quality["incomplete_reason"] == "osrm_reconstruction_failed"
  retry_count = int(route_row.get("retry_count") or 0) + 1
  if retryable and retry_count < MAX_ROUTE_FINALIZER_RETRIES:
      processing_status = "pending"
      quality["reconstruction_status"] = "retrying"
      next_retry_at = now + timedelta(seconds=_retry_delay_seconds(retry_count))
  elif retryable:
      quality["reconstruction_status"] = "failed"
  ```
- [ ] Gate distance recompute and snapshot publication on `processing_status == "complete"`.
- [ ] Run `python -m pytest -q --no-cov backend/tests/test_route_finalizer.py backend/tests/test_route_reconstruction.py`.
- [ ] Commit with `fix(routes): retry incomplete reconstruction`.

### Task 4: Make route status copy truthful

**Files:**
- Modify: `shared/utils/routeSegments.ts`
- Test: `admin-dashboard/src/lib/__tests__/route-segments.test.ts`

**Interfaces:**
- Consumes `route_quality.reconstruction_status`.
- Produces “Route reconstruction in progress” for `retrying` and “Route unavailable · reconstruction failed” for `failed`.

- [ ] Add failing tests for retrying and failed status labels.
  ```typescript
  expect(routeQualityLabel({ reconstruction_status: 'retrying' })).toBe('Route reconstruction in progress');
  expect(routeQualityLabel({ reconstruction_status: 'failed' })).toBe('Route unavailable · reconstruction failed');
  ```
- [ ] Run `npm test -- --run src/lib/__tests__/route-segments.test.ts` in `admin-dashboard` and verify RED.
- [ ] Update `routeQualityLabel` to prioritize explicit reconstruction status before coverage copy; retain legacy behavior when absent.
  ```typescript
  if (value?.reconstruction_status === 'retrying') return 'Route reconstruction in progress';
  if (value?.reconstruction_status === 'failed') return 'Route unavailable · reconstruction failed';
  ```
- [ ] Re-run the focused test and verify GREEN.
- [ ] Commit with `fix(routes): distinguish retrying reconstruction`.

### Task 5: Poll while pending partial geometry is visible

**Files:**
- Modify: `shared/hooks/useCompletedRouteRefresh.ts`
- Test: `rider-app/__tests__/useCompletedRouteRefresh.test.tsx`

**Interfaces:**
- Consumes completed v2 rides with `route_geometry_status` pending/processing.
- Produces refresh polling even when diagnostic partial segments exist, stopping when processing reaches complete/incomplete.

- [ ] Add a failing test showing pending partial geometry still returns `true` from `shouldRefreshCompletedRoute`.
  ```typescript
  expect(shouldRefreshCompletedRoute({
    status: 'completed', route_schema_version: 2, route_geometry_status: 'pending',
    actual_route_segments: [{ coordinates: [[50.4, -104.6], [50.5, -104.7]] }],
  })).toBe(true);
  ```
- [ ] Run `yarn test __tests__/useCompletedRouteRefresh.test.tsx --runInBand` and verify RED.
- [ ] Remove the requirement that normalized actual segments be empty; retain the revision-matched snapshot stop condition.
  ```typescript
  return ride.status === 'completed'
    && Number(ride.route_schema_version || 0) >= 2
    && (ride.route_geometry_status === 'pending' || ride.route_geometry_status === 'processing')
    && !hasRevisionMatchedSnapshot(ride);
  ```
- [ ] Run the focused test and the rider/driver route presentation suites.
- [ ] Commit with `fix(routes): refresh pending partial geometry`.

### Task 6: Verify, deploy, and regenerate affected rides

**Files:** No source-file changes.

**Interfaces:**
- Requeues only completed v2 `ride_routes` rows with OSRM reconstruction failure.
- Leaves all `rides` monetary and lifecycle fields untouched.

- [ ] Run backend route suites, rider route suites, driver route suites, admin route tests, and `git diff --check`.
- [ ] Run the Graphify rebuild and confirm the worktree is clean after any generated-output commit.
- [ ] Push `main`; wait for Railway `/health` and `/openapi.json` to respond and confirm `/api/v1/drivers/location-batch` exists.
- [ ] Publish production OTA updates for rider runtime `2.0.0` and driver runtime `2.5.0`.
- [ ] Record the affected ride IDs using a read-only query, then atomically requeue their route rows by setting `processing_status='pending'`, `processing_claimed_at=null`, `next_retry_at=now()`, `finalized_at=null`, and clearing snapshot references. Do not update `rides`.
  ```sql
  update public.ride_routes
  set processing_status = 'pending', processing_claimed_at = null,
      next_retry_at = now(), finalized_at = null,
      snapshot_revision = 0, snapshot_object_path = null, snapshot_url = null
  where route_schema_version >= 2
    and processing_status = 'incomplete'
    and route_quality->>'incomplete_reason' = 'osrm_reconstruction_failed';
  ```
- [ ] Verify `SPR-XUJZH9` reaches `complete`, has zero failed gaps, a revision-matched private snapshot, and unchanged fare/earnings fields.
- [ ] Process remaining affected rides in a bounded batch and report counts for complete, retrying, and terminal incomplete outcomes.
