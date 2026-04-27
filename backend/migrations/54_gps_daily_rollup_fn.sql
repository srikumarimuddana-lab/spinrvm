-- 54_gps_daily_rollup_fn.sql
--
-- Rollback: DROP FUNCTION IF EXISTS compute_driver_phase_distances(TEXT, TIMESTAMPTZ, TIMESTAMPTZ);
--           DROP INDEX IF EXISTS idx_dlh_driver_ts_notnull;
--
-- Purpose: replace the Python haversine loop in routes/admin/maintenance.py
-- (admin_rollup_driver_daily) with a server-side SQL function.
-- The old code fetched up to 100k GPS rows per driver into Python memory and
-- ran haversine() in a loop, causing OOM on busy days. Moving the aggregation
-- to Postgres means only 7 scalars are returned per driver call.
--
-- The function is STABLE (pure reads, no side effects) and SECURITY DEFINER
-- so the backend service role can call it without needing direct table access.
-- search_path is pinned to prevent search_path injection.

CREATE OR REPLACE FUNCTION compute_driver_phase_distances(
    p_driver_id    TEXT,
    p_day_start    TIMESTAMPTZ,
    p_day_end      TIMESTAMPTZ
)
RETURNS TABLE(
    idle_km         FLOAT,
    navigating_km   FLOAT,
    trip_km         FLOAT,
    online_minutes  INT,
    first_online_at TIMESTAMPTZ,
    last_online_at  TIMESTAMPTZ,
    point_count     INT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
WITH ordered_points AS (
    -- Window over time-sorted GPS points for this driver+day.
    -- LAG gives us the previous point's coordinates for segment distance.
    SELECT
        lat,
        lng,
        "timestamp",
        tracking_phase,
        LAG(lat)  OVER (ORDER BY "timestamp") AS prev_lat,
        LAG(lng)  OVER (ORDER BY "timestamp") AS prev_lng
    FROM driver_location_history
    WHERE driver_id  = p_driver_id
      AND "timestamp" >= p_day_start
      AND "timestamp"  < p_day_end
      AND lat IS NOT NULL
      AND lng IS NOT NULL
),
segments AS (
    -- Haversine formula (degrees → km) applied to each consecutive pair.
    -- FILTER below splits by tracking_phase without a second pass.
    SELECT
        tracking_phase,
        2.0 * 6371.0 * ASIN(
            SQRT(
                POWER(SIN(RADIANS(lat - prev_lat) / 2.0), 2) +
                COS(RADIANS(prev_lat)) * COS(RADIANS(lat)) *
                POWER(SIN(RADIANS(lng - prev_lng) / 2.0), 2)
            )
        ) AS dist_km
    FROM ordered_points
    WHERE prev_lat IS NOT NULL
      AND prev_lng IS NOT NULL
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
    COALESCE(SUM(dist_km) FILTER (WHERE tracking_phase = 'online_idle'),           0.0) AS idle_km,
    COALESCE(SUM(dist_km) FILTER (WHERE tracking_phase = 'navigating_to_pickup'),  0.0) AS navigating_km,
    COALESCE(SUM(dist_km) FILTER (WHERE tracking_phase = 'trip_in_progress'),      0.0) AS trip_km,
    COALESCE(
        EXTRACT(EPOCH FROM (tb.last_ts - tb.first_ts))::INT / 60,
        0
    )                                                                                    AS online_minutes,
    tb.first_ts                                                                          AS first_online_at,
    tb.last_ts                                                                           AS last_online_at,
    tb.n_points                                                                          AS point_count
FROM segments
CROSS JOIN time_bounds tb
GROUP BY tb.first_ts, tb.last_ts, tb.n_points;
$$;

-- Supporting index: covers the driver_id + timestamp filter used by both
-- the function above and the cleanup endpoint. Partial index on non-null
-- coordinates keeps it narrow (rows missing lat/lng are never useful for
-- distance calculations and will be pruned by the retention purge anyway).
CREATE INDEX IF NOT EXISTS idx_dlh_driver_ts_notnull
    ON driver_location_history (driver_id, "timestamp")
    WHERE lat IS NOT NULL AND lng IS NOT NULL;
