-- Migration 204: Leaderboard aggregate RPC (F4)
--
-- GET /drivers/leaderboard aggregated per-driver rides/earnings/tips by
-- scanning the fleet in Python: 500 drivers × (rides query + users lookup)
-- ≈ 1,001 sequential round-trips per driver-app screen open, with a silent
-- 1,000-ride cap per driver that undercounted long-period earnings.
-- driver_daily_stats (migration 16) already holds the nightly per-day
-- rollup; this function reduces the whole board to one GROUP BY round-trip.
-- A fleet×days row fetch through PostgREST was rejected as the fix because
-- server-side max-rows would silently truncate month/all-time periods.
--
-- Also returns MAX(stat_date) per driver so the caller can top up with
-- rides newer than the last rollup without double-counting (the rollup
-- endpoint accepts an arbitrary target_date, so "stats end yesterday" is
-- not a safe assumption).
--
-- Read-only (STABLE). Forward-compatible: CREATE OR REPLACE FUNCTION only.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS driver_leaderboard_totals(DATE);

CREATE OR REPLACE FUNCTION driver_leaderboard_totals(p_start DATE)
RETURNS TABLE(
    driver_id     TEXT,
    rides         BIGINT,
    earnings      NUMERIC,
    tips          NUMERIC,
    last_stat_date DATE
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT
        s.driver_id,
        COALESCE(SUM(s.rides_completed), 0)::BIGINT AS rides,
        -- total_earnings/total_tips are FLOAT columns (migration 16); cast
        -- each row to NUMERIC BEFORE summing so the aggregation itself
        -- doesn't accumulate double-precision drift. The stored per-day
        -- values keep whatever float error they were written with — fixing
        -- that requires altering 16's column types (tracked separately).
        COALESCE(SUM(s.total_earnings::NUMERIC), 0) AS earnings,
        COALESCE(SUM(s.total_tips::NUMERIC), 0)     AS tips,
        MAX(s.stat_date)                            AS last_stat_date
    FROM driver_daily_stats s
    WHERE s.stat_date >= p_start
    GROUP BY s.driver_id
$$;

-- Fleet-wide earnings are sensitive: backend-only, same REVOKE/GRANT pattern
-- as migrations 196/203 (Supabase exposes every public function at
-- /rest/v1/rpc/, and revoking PUBLIC strips service_role's inherited
-- EXECUTE, so it must be granted back explicitly).
REVOKE EXECUTE ON FUNCTION driver_leaderboard_totals(DATE)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driver_leaderboard_totals(DATE)
    TO service_role;
