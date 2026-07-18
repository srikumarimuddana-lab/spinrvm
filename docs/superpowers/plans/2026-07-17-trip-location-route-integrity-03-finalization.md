# Route Finalization and Receipt Tasks

Required context: complete Tasks 1–11. Execute Tasks 12–20 in order.

### Task 12: Backend completion-fix validation

**Files:** Modify `backend/routes/drivers/ride_complete.py`; create `backend/tests/test_ride_completion_location.py`.

- [ ] Add failing async tests for fresh/accurate fix acceptance, stale fix, missing fix, 200 m/1 km confirmation bands, invalid reason, inline idempotent persistence, and legacy-client incomplete completion.
- [ ] Run `pytest backend/tests/test_ride_completion_location.py -q`; expect request-body failures.
- [ ] Add optional `RideCompletionRequest` models. Validate capture age and integrity, persist `completion_fix` through `persist_trip_location_batch` before aggregation, and upsert `ride_routes.completion_point`.
- [ ] In `on` mode, return `409` with `code=completion_confirmation_required`, `band`, and distance only when required confirmation is absent. Never return coordinates. In `shadow` mode, record the same reason without blocking.
- [ ] Legacy/no-fix completion remains possible and records `missing_tail=true`; the ride state transition still uses `_require_ride_in_state`.
- [ ] Run the targeted test and Ruff; expect PASS. Commit: `feat(rides): validate completion location`.

### Task 13: Deterministic route segmentation

**Files:** Create `backend/utils/route_segments.py`, `backend/tests/test_route_segments.py`.

**Produces:** `segment_route(points, lifecycle, completion_point) -> SegmentedRoute`.

- [ ] Write failing tests for capture-time ordering, equal-time sequence tie-break, late upload, duplicate identity, >60-second gap, >300-metre gap, impossible speed, clock regression, session boundary, coverage, and missing-tail thresholds.
- [ ] Add the 49-minute fixture with 26- and 10-minute gaps plus the 5.25 km jump; assert separate segments and no synthesized coordinates.
- [ ] Run `pytest backend/tests/test_route_segments.py -q`; expect module-not-found.
- [ ] Implement pure functions with no DB/provider calls:

```py
def point_order(p: dict) -> tuple:
    return (parse_iso_utc(p["captured_at"]), str(p["recording_session_id"]), int(p["sequence_number"]))

def completion_tolerance_m(final: dict, completion: dict) -> float:
    return max(75.0, float(final.get("accuracy") or 0) + float(completion.get("accuracy") or 0) + 25.0)
```

- [ ] Return reason-coded rejected points and quality only; never log coordinates. Run tests and Ruff; expect PASS. Commit: `feat(routes): segment timestamp-ordered GPS traces`.

### Task 14: Chunked per-segment map matching

**Files:** Modify `backend/utils/route_distance.py`, `backend/tests/test_route_distance_osrm.py`, `backend/tests/test_route_distance.py`.

**Produces:** `compute_segmented_road_route(observed_segments) -> {segments, distance_km, provider, failures}`.

- [ ] Add failing tests that 293 points produce overlapping calls of at most 100 coordinates, OSRM `matchings` stay separate, gaps never share a geometry array, Google fallback preserves boundaries, and distance sums only returned segments.
- [ ] Run the focused route-distance tests; expect current one-call/downsample/flatten assertions to fail.
- [ ] Replace global `_downsample` use with:

```py
def _overlapping_chunks(points: list[dict], size: int = 90, overlap: int = 10):
    start = 0
    while start < len(points):
        yield points[start:start + size]
        if start + size >= len(points): break
        start += size - overlap
```

- [ ] Pass monotonically ordered sensor timestamps and accuracy radiuses to OSRM. Deduplicate overlap only inside the same provider matching; never concatenate different matchings or observed segments.
- [ ] Run both test files and Ruff; expect PASS. Commit: `fix(routes): preserve matcher segment boundaries`.

### Task 15: Replay-safe route finalizer

**Files:** Create `backend/utils/route_finalizer.py`, `backend/tests/test_route_finalizer.py`; modify `backend/routes/drivers/ride_complete.py`.

- [ ] Add failing tests for pending creation, timestamp-ordered query, segmenter/matcher composition, revision increment, shadow/on exposure, incomplete quality, provider failure retry state, and no fare mutation.
- [ ] Run `pytest backend/tests/test_route_finalizer.py -q`; expect module-not-found.
- [ ] Implement `mark_route_pending(ride_id, completion_point)` and `finalize_route(ride_id)`. Persist `observed_segments`, `road_matched_segments`, `route_quality`, revision, final status, and timestamps in one update.
- [ ] Make completion mark the route pending after settlement inputs are frozen. Remove snapshot/geometry publication from the request hot path; do not change charged fare or lifecycle duration in the async finalizer.
- [ ] On failure, set `pending`, increment retry count, set exponential `next_retry_at`, and log only IDs/reason codes.
- [ ] Run the targeted test and Ruff; expect PASS. Commit: `feat(routes): finalize versioned route geometry`.

### Task 16: Finalizer loop and stale-claim recovery

**Files:** Modify `backend/utils/route_finalizer.py`, `backend/core/lifespan.py`; create `backend/tests/test_route_finalizer_loop.py`.

- [ ] Test two concurrent claims yield one winner, processing claims older than five minutes return to pending, retries honor `next_retry_at`, and the loop survives a tick failure.
- [ ] Run `pytest backend/tests/test_route_finalizer_loop.py -q`; expect missing loop/claim failures.
- [ ] Implement atomic `pending -> processing` update filters, 15-second jittered polling, stale-claim recovery, and replay-safe save. Register `_spawn("route_finalizer (15s)", route_finalizer_loop)` in lifespan.
- [ ] Run targeted tests and Ruff; expect PASS. Commit: `feat(routes): run durable route finalization loop`.

### Task 17: Re-finalize after late acknowledged points

**Files:** Modify `backend/utils/breadcrumbs.py`, `backend/tests/test_breadcrumb_persistence.py`.

- [ ] Add failing tests that newly inserted points for a completed ride debounce a pending revision by 30 seconds, while duplicate replay does not.
- [ ] Track rows actually returned from conflict-safe insert. Only when at least one new row was inserted, update existing `ride_routes` to `pending` without changing `route_revision`.
- [ ] Run breadcrumb tests and Ruff; expect PASS. Commit: `feat(routes): revise routes for late GPS uploads`.

### Task 18: Route-detail API projection

**Files:** Modify `backend/repositories/ride_repo.py`; create `backend/tests/test_ride_route_contract.py`.

- [ ] Test authorized ride detail returns matched segments or observed fallback, quality, revision, status, and snapshot; list endpoints do not return heavy geometry.
- [ ] Run `pytest backend/tests/test_ride_route_contract.py -q`; expect missing v2 fields.
- [ ] Project `actual_route_segments = road_matched_segments or observed_segments`, copy lightweight quality/revision/status, and retain legacy fields only for old rows.
- [ ] Run the targeted test and Ruff; expect PASS. Commit: `feat(api): expose versioned route segments`.

### Task 19: Segmented revisioned snapshots

**Files:** Modify `backend/utils/route_snapshot.py`, `backend/routes/drivers/_shared.py`, `backend/tests/test_utils_extended.py`.

- [ ] Add tests proving one Static Maps path per segment, no cross-gap chord, pickup/completion/planned markers, incomplete banner, and revisioned object key.
- [ ] Run route-snapshot tests; expect signature/path failures.
- [ ] Accept `route_segments`, `route_quality`, and `route_revision`; emit separate `path=` parameters and use Pillow to add the coverage banner. Keep legacy `_split_on_gaps` only for old rows.
- [ ] Publish keys like `ride-routes/{ride_id}/route-v{revision}.png`; save URL and `snapshot_revision` together.
- [ ] Run tests and Ruff; expect PASS. Commit: `fix(receipts): publish gap-safe route snapshots`.

### Task 20: HTML and backend PDF route receipts

**Files:** Modify `backend/utils/email_receipt.py`, `backend/utils/receipt_pdf.py`; create `backend/tests/test_receipt_route_snapshot.py`.

- [ ] Test HTML image/revision/note, incomplete fallback copy, PDF image embedding, PDF note, and no “actual route” label for planned fallback.
- [ ] Run `pytest backend/tests/test_receipt_route_snapshot.py -q`; expect PDF/quality failures.
- [ ] Email waits on bounded initial finalization, embeds the revisioned URL and quality note, downloads snapshot bytes asynchronously, then passes bytes and note to `generate_receipt_pdf`.
- [ ] PDF embeds bytes when present and always prints incomplete/unavailable status when applicable. Receipt failure remains independent of payment settlement.
- [ ] Run receipt tests and Ruff; expect PASS. Commit: `feat(receipts): include truthful route snapshots`.
