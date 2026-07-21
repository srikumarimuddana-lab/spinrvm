# Route Pipeline Diagnostics — "Why is the actual route not showing?"

When a completed ride's detail screen falls back to the planned route, the
caption tells you which state the pipeline is in before you touch the DB:

| UI caption | Surface | Meaning |
|---|---|---|
| `Actual route · Route verified · N% GPS coverage` | rider/driver | Healthy: segments served, trace covers the ride window |
| `Actual route · Route incomplete · N% GPS coverage` | rider/driver | Segments served but the trace has gaps or a missing tail (`route_quality.missing_tail` / `incomplete_reason`) |
| `Planned route · Actual GPS route unavailable` | rider `ride-details` | `actual_route_segments` from `GET /rides/{id}` is **empty** |
| `Planned route · Route snapshot unavailable · GPS route is still processing` | driver `ride-detail` | Same as above AND `route_schema_version >= 2` — the v2 row exists but has no observed/matched segments yet |
| `Planned route preview` | driver `ride-detail` | Legacy (pre-v2) ride — no `ride_routes` v2 row at all |

Server side, `actual_route_segments` is projected in
`backend/repositories/ride_repo.py` as
`ride_routes.road_matched_segments OR ride_routes.observed_segments`.
Both empty means one of:

1. **No GPS points stored** in `driver_location_history` for the ride
   (recorder never started, uploads failing, or every fix dropped as
   `mocked` — simulated locations are rejected by design);
2. **Finalizer never completed** — `ride_routes.processing_status` stuck in
   `pending`/`processing` (the 15 s `route_finalizer_loop` not running, or
   every tick failing with `retry_count` climbing);
3. Finalizer ran but the **segment parser rejected every point**
   (`route_quality.rejected_point_count ≈ point_count`).

Run queries 3 + 4 below for the affected ride first; query 6 immediately
reveals a deployment-wide stuck finalizer.

> Run in the Supabase SQL editor (service role — these tables deny direct
> client access by RLS design). Queries 4–8 avoid selecting raw lat/lng;
> keep it that way when pasting results into tickets or chat (PIPEDA).

## 1) Ride history — recent completed rides + route pipeline state

```sql
SELECT r.id, r.status, r.ride_started_at, r.ride_completed_at,
       r.planned_distance_km, r.distance_km, r.actual_distance_km,
       r.gps_points_count, r.route_geometry_status, r.route_geometry_error,
       r.route_quality
FROM rides r
WHERE r.ride_completed_at >= now() - interval '3 days'
ORDER BY r.ride_completed_at DESC
LIMIT 30;
```

## 2) Driver ride history — one driver's recent rides

```sql
SELECT id, status, ride_started_at, ride_completed_at, gps_points_count,
       planned_distance_km, actual_distance_km, route_geometry_status
FROM rides
WHERE driver_id = '<DRIVER_ID>'
ORDER BY created_at DESC
LIMIT 30;
```

## 3) Route pipeline state for one ride (the key query)

```sql
SELECT ride_id, route_schema_version, route_revision, processing_status,
       processing_claimed_at, next_retry_at, retry_count,
       jsonb_array_length(observed_segments)     AS observed_segments,
       jsonb_array_length(road_matched_segments) AS matched_segments,
       completion_point, route_quality,
       snapshot_revision, snapshot_object_path, snapshot_attempts,
       finalized_at, computed_at
FROM ride_routes
WHERE ride_id = '<RIDE_ID>';
```

Drop `snapshot_attempts` from the column list if migration 243 isn't applied
on the target environment.

## 4) GPS breadcrumb inventory for one ride (counts only — no coordinates)

```sql
SELECT COUNT(*)                                                        AS total_points,
       COUNT(*) FILTER (WHERE tracking_phase = 'trip_in_progress')     AS trip_points,
       COUNT(*) FILTER (WHERE tracking_phase = 'navigating_to_pickup') AS nav_points,
       COUNT(*) FILTER (WHERE source = 'background')                   AS background_points,
       COUNT(*) FILTER (WHERE source = 'foreground')                   AS foreground_points,
       COUNT(*) FILTER (WHERE mocked)                                  AS mocked_points,
       COUNT(*) FILTER (WHERE is_completion_fix)                       AS completion_fixes,
       COUNT(DISTINCT recording_session_id)                            AS sessions,
       MIN(captured_at)                                                AS first_point,
       MAX(captured_at)                                                AS last_point
FROM driver_location_history
WHERE ride_id = '<RIDE_ID>';
```

Compare `first_point`/`last_point` against the ride's `ride_started_at` /
`ride_completed_at` from query 1 — a `last_point` minutes before completion
is the classic stranded-tail signature.

## 5) Gap timeline for one ride — where the trace breaks

Any `gap_from_previous` over 60 seconds splits the route into separate
segments (`MAX_CONTINUOUS_GAP_SECONDS` in `utils/route_segments.py`).

```sql
SELECT sequence_number, tracking_phase, source, accuracy, captured_at,
       captured_at - LAG(captured_at) OVER (ORDER BY captured_at) AS gap_from_previous
FROM driver_location_history
WHERE ride_id = '<RIDE_ID>'
ORDER BY captured_at;
```

## 6) Finalizer queue health — stuck rows = dead or failing loop

```sql
SELECT ride_id, processing_status, processing_claimed_at, retry_count,
       next_retry_at, now() - next_retry_at AS overdue_by,
       route_revision, snapshot_revision
FROM ride_routes
WHERE processing_status IN ('pending', 'processing')
ORDER BY next_retry_at NULLS FIRST
LIMIT 50;
```

Healthy: this is empty or rows are seconds old. Rows overdue by minutes
mean the `route_finalizer_loop` (15 s tick, spawned in `core/lifespan.py`)
is not running or every tick is failing — check backend logs for
`route finalization failed`.

## 7) Gap events recorded by the live monitor

```sql
SELECT gap_started_at, detected_at, gap_resolved_at, gap_seconds,
       threshold_seconds, status
FROM ride_location_gap_events
WHERE ride_id = '<RIDE_ID>'
ORDER BY detected_at;
```

## 8) Driver's GPS volume per ride for a day

```sql
SELECT ride_id, COUNT(*) AS points,
       MIN(captured_at) AS first_pt, MAX(captured_at) AS last_pt
FROM driver_location_history
WHERE driver_id = '<DRIVER_ID>'
  AND captured_at >= '<DAY>'::timestamptz
  AND captured_at <  '<DAY>'::timestamptz + interval '1 day'
GROUP BY ride_id
ORDER BY first_pt;
```

## Interpretation matrix

| Query 4 shows | Query 3 shows | Root cause | Fix direction |
|---|---|---|---|
| `total_points = 0` | any | Client never uploaded: recorder not started, `/drivers/location-batch` auth/App Check failing, or every fix dropped as `mocked` (simulator/dev testing) | Check driver-app logs; simulated-location test rides are rejected by design — retest on a real device |
| points > 0, `mocked_points ≈ total_points` | any | Simulated-location test ride; mocked points are stored flagged but the completion fix and trust gates treat them as untrusted | Expected in dev; use a real device for route validation |
| points > 0 | `pending` with `next_retry_at` in the past (query 6 shows overdue rows) | Finalizer loop not running or crashing on this deployment | Restart/inspect the backend; overdue rows self-heal once the loop runs |
| points > 0 | `retry_count` climbing, status flapping pending↔processing | Every finalize attempt throws — road-match provider unreachable, or branch code deployed without migrations 242/243 | Read the logged exception; apply pending migrations |
| points > 0 | `complete` but `observed_segments = 0` | Segment parser rejected everything — compare `route_quality.rejected_point_count` vs `point_count` | Inspect query 5 rows for null timestamps / absurd values |
| points stop mid-trip | any | Stranded-tail bug (pre-fix build) — the last outbox batch never uploaded | Fixed by the completion force-flush changes; a late upload within retention also self-heals the route |

## 9) Re-finalize rides with empty segments (one-time fix after v1 parser patch)

If the segment parser was rejecting v1 legacy breadcrumbs (points with NULL
`recording_session_id` / `sequence_number`), rides that were finalized during
that window have `processing_status = 'complete'` but zero observed segments.
After deploying the parser fix, re-queue them:

```sql
-- Preview: which rides would be re-queued?
SELECT rr.ride_id, rr.processing_status, rr.route_revision,
       jsonb_array_length(rr.observed_segments) AS observed_segs,
       (SELECT COUNT(*) FROM driver_location_history dlh
        WHERE dlh.ride_id = rr.ride_id) AS gps_points
FROM ride_routes rr
WHERE rr.processing_status IN ('complete', 'incomplete')
  AND jsonb_array_length(rr.observed_segments) = 0
  AND EXISTS (
    SELECT 1 FROM driver_location_history dlh
    WHERE dlh.ride_id = rr.ride_id
  );

-- Execute: re-queue them for finalization
UPDATE ride_routes
SET processing_status = 'pending',
    next_retry_at = now(),
    retry_count = 0
WHERE processing_status IN ('complete', 'incomplete')
  AND jsonb_array_length(observed_segments) = 0
  AND EXISTS (
    SELECT 1 FROM driver_location_history dlh
    WHERE dlh.ride_id = ride_routes.ride_id
  );
```

The finalizer loop (15 s tick) will pick these up automatically once the
backend with the v1 parser fix is deployed.

## Related

- `backend/utils/route_finalizer.py` — 15 s finalization loop, retry/claim state machine
- `backend/utils/breadcrumbs.py` — batch acceptance rules (`after_ride_window` is per-point `captured_at`, never delivery time)
- `backend/utils/route_segments.py` — segmentation/coverage/missing-tail rules
- Metrics to watch: `spinr_drivers_late_tail_batches_total`,
  `spinr_rides_snapshot_fallback_total`, `spinr_rides_gps_gap_detected_total`
