-- 348_phase_distances_fn_v2.sql
--
-- Rollback: DROP FUNCTION IF EXISTS compute_driver_phase_distances(UUID, TIMESTAMPTZ, TIMESTAMPTZ);
--           then re-apply the CREATE OR REPLACE FUNCTION body from
--           54_gps_daily_rollup_fn.sql (the v1 shape). Callers read result
--           columns by name and treat missing keys as 0, so the window
--           between drop and re-create only affects the admin rollup RPC.
--
-- Purpose: v2 of the daily-rollup distance function. Two changes:
--
-- 1. ANOMALY FILTER PARITY WITH SETTLEMENT. v1 summed every consecutive
--    segment raw; one GPS teleport (tower handoff) inflated a day's trip_km
--    by hundreds of km, and tests/test_phase_distance_parity.py pinned that
--    divergence as "known". v2 applies the SAME three caps settlement's
--    utils/trip_distance.py uses on every segment:
--        segment displacement <= 5.0 km        (MAX_SEG_KM)
--        segment time gap     <= 300 s         (MAX_SEG_GAP_S)
--        implied ground speed <= 150 km/h      (MAX_SEG_KMH, only when gap > 0)
--    An ACCURACY cap was considered and deliberately OMITTED: settlement
--    does not filter on accuracy, and the two implementations diverging in
--    the opposite direction is exactly the bug class this migration removes.
--
-- 2. PER-PHASE SECONDS. The Distance Travelled table shows duration per
--    phase; v2 sums accepted-segment time gaps per phase (mirroring
--    trip_distance.py: only gaps 0 < gap <= 300 s count, so a stale
--    breadcrumb can never inflate a phase by hours).
--
-- Phase bucketing: arrived_at_pickup folds into navigating_to_pickup — the
-- product's three buckets are "driving around" / "on pickup way" / "on ride",
-- and waiting at the pickup point belongs to the pickup-way bucket. Raw
-- phases remain queryable from driver_location_history.
--
-- Return-shape change (hence DROP + CREATE — Postgres cannot alter a
-- function's RETURNS TABLE in place): adds idle_seconds, navigating_seconds,
-- trip_seconds, rejected_segments. Also, v2 ALWAYS returns exactly one row
-- (v1 returned zero rows for a day with < 2 points); callers already treat
-- both shapes identically (rows[0] if rows else {}).
--
-- DROP + CREATE run inside this migration's single transaction, so
-- concurrent callers see either v1 or v2, never a missing function.

DROP FUNCTION IF EXISTS compute_driver_phase_distances(UUID, TIMESTAMPTZ, TIMESTAMPTZ);

CREATE FUNCTION compute_driver_phase_distances(
    p_driver_id    UUID,
    p_day_start    TIMESTAMPTZ,
    p_day_end      TIMESTAMPTZ
)
RETURNS TABLE(
    idle_km             FLOAT,
    navigating_km       FLOAT,
    trip_km             FLOAT,
    idle_seconds        INT,
    navigating_seconds  INT,
    trip_seconds        INT,
    online_minutes      INT,
    first_online_at     TIMESTAMPTZ,
    last_online_at      TIMESTAMPTZ,
    point_count         INT,
    rejected_segments   INT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
WITH ordered_points AS (
    SELECT
        lat,
        lng,
        "timestamp",
        tracking_phase,
        LAG(lat)         OVER w AS prev_lat,
        LAG(lng)         OVER w AS prev_lng,
        LAG("timestamp") OVER w AS prev_ts
    FROM driver_location_history
    WHERE driver_id  = p_driver_id
      AND "timestamp" >= p_day_start
      AND "timestamp"  < p_day_end
      AND lat IS NOT NULL
      AND lng IS NOT NULL
    WINDOW w AS (ORDER BY "timestamp")
),
segments AS (
    -- Haversine (degrees -> km) per consecutive pair; phase attribution to
    -- the CURRENT point's phase, same as v1 and settlement.
    SELECT
        CASE
            WHEN tracking_phase = 'arrived_at_pickup' THEN 'navigating_to_pickup'
            ELSE tracking_phase
        END AS phase_bucket,
        2.0 * 6371.0 * ASIN(
            SQRT(
                POWER(SIN(RADIANS(lat - prev_lat) / 2.0), 2) +
                COS(RADIANS(prev_lat)) * COS(RADIANS(lat)) *
                POWER(SIN(RADIANS(lng - prev_lng) / 2.0), 2)
            )
        ) AS dist_km,
        EXTRACT(EPOCH FROM ("timestamp" - prev_ts)) AS gap_s
    FROM ordered_points
    WHERE prev_lat IS NOT NULL
      AND prev_lng IS NOT NULL
),
accepted AS (
    -- Mirror of trip_distance.py's rejection ladder. gap_s is never NULL
    -- here (the window ORDER BY guarantees prev_ts when prev_lat exists);
    -- gap_s <= 0 (duplicate timestamps) skips the speed check, as in Python.
    SELECT phase_bucket, dist_km, gap_s
    FROM segments
    WHERE dist_km <= 5.0
      AND gap_s  <= 300
      AND (gap_s <= 0 OR dist_km / (gap_s / 3600.0) <= 150.0)
),
agg AS (
    SELECT
        COALESCE(SUM(dist_km) FILTER (WHERE phase_bucket = 'online_idle'),          0.0) AS idle_km,
        COALESCE(SUM(dist_km) FILTER (WHERE phase_bucket = 'navigating_to_pickup'), 0.0) AS navigating_km,
        COALESCE(SUM(dist_km) FILTER (WHERE phase_bucket = 'trip_in_progress'),     0.0) AS trip_km,
        COALESCE(SUM(gap_s)   FILTER (WHERE phase_bucket = 'online_idle'          AND gap_s > 0), 0)::INT AS idle_seconds,
        COALESCE(SUM(gap_s)   FILTER (WHERE phase_bucket = 'navigating_to_pickup' AND gap_s > 0), 0)::INT AS navigating_seconds,
        COALESCE(SUM(gap_s)   FILTER (WHERE phase_bucket = 'trip_in_progress'     AND gap_s > 0), 0)::INT AS trip_seconds,
        COUNT(*)::INT AS n_accepted
    FROM accepted
),
seg_count AS (
    SELECT COUNT(*)::INT AS n_total FROM segments
),
time_bounds AS (
    SELECT
        MIN("timestamp")  AS first_ts,
        MAX("timestamp")  AS last_ts,
        COUNT(*)::INT     AS n_points
    FROM driver_location_history
    WHERE driver_id  = p_driver_id
      AND "timestamp" >= p_day_start
      AND "timestamp"  < p_day_end
      AND lat IS NOT NULL
)
SELECT
    a.idle_km,
    a.navigating_km,
    a.trip_km,
    a.idle_seconds,
    a.navigating_seconds,
    a.trip_seconds,
    COALESCE(EXTRACT(EPOCH FROM (tb.last_ts - tb.first_ts))::INT / 60, 0) AS online_minutes,
    tb.first_ts  AS first_online_at,
    tb.last_ts   AS last_online_at,
    tb.n_points  AS point_count,
    (sc.n_total - a.n_accepted)::INT AS rejected_segments
FROM agg a
CROSS JOIN seg_count sc
CROSS JOIN time_bounds tb;
$$;

COMMENT ON FUNCTION compute_driver_phase_distances IS
  'v2 (migration 348): per-phase km AND seconds for one driver-day, with the '
  'same segment anomaly caps as settlement (5 km / 300 s / 150 km/h). '
  'arrived_at_pickup folds into navigating_to_pickup. Always returns one row.';

-- SECURITY DEFINER bypasses driver_location_history's RLS and the function
-- takes an arbitrary driver_id with no auth.uid() check — without this
-- lockdown any anon/authenticated PostgREST caller could pull another
-- driver's per-day activity via /rest/v1/rpc/. v1 (migration 54) never had
-- the REVOKE; closing that gap here, same pattern as migration 204.
REVOKE EXECUTE ON FUNCTION compute_driver_phase_distances(UUID, TIMESTAMPTZ, TIMESTAMPTZ)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION compute_driver_phase_distances(UUID, TIMESTAMPTZ, TIMESTAMPTZ)
    TO service_role;
