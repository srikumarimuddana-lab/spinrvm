-- 194_admin_dashboard_money_fn.sql
--
-- Money aggregation for the main admin dashboard (GET /api/admin/analytics/dashboard).
-- PostgREST aggregate functions are disabled on the project, so the dashboard's
-- revenue .sum()s failed; do the sums in Postgres instead (one round-trip),
-- mirroring admin_analytics_overview / admin_earnings_overview_agg. Counts stay
-- on PostgREST count="exact" (unaffected). Window is [p_start, p_end); optional
-- service area.
--
-- Spinr takes 0% of ride fares: driver_earnings is the fare drivers keep, and
-- platform_revenue is Spinr Pass subscription revenue (+ corporate later), NEVER
-- a slice of ride_volume. driver_earnings is FLOAT NOT NULL DEFAULT 0, so a 0
-- (un-snapshotted historical row) falls back to grand_total; the float is cast
-- via ::text::numeric to recover the cent-exact value (matches mig 163/161).
--
-- Columns confirmed present: rides.grand_total (mig 46), rides.driver_earnings
-- (written by mig 110), rides.service_area_id (added out-of-band in
-- backend/sql/04_rides_admin_overhaul.sql, used by mig 163),
-- subscription_payments.{amount,driver_id,created_at} (mig 151).
--
-- Forward-compatible: CREATE OR REPLACE, read-only, no schema change.
-- Rollback: DROP FUNCTION IF EXISTS admin_dashboard_money(timestamptz, timestamptz, text);

CREATE OR REPLACE FUNCTION admin_dashboard_money(
    p_start timestamptz,
    p_end timestamptz,
    p_service_area_id text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT (
        SELECT jsonb_build_object(
            'ride_volume', ROUND(COALESCE(SUM(r.grand_total), 0), 2),
            'driver_earnings', ROUND(COALESCE(SUM(
                CASE
                    WHEN r.driver_earnings IS NULL OR r.driver_earnings = 0 THEN r.grand_total
                    ELSE r.driver_earnings::text::numeric
                END
            ), 0), 2)
        )
        FROM rides r
        WHERE r.status = 'completed'
          AND r.created_at >= p_start AND r.created_at < p_end
          AND (p_service_area_id IS NULL OR r.service_area_id = p_service_area_id)
    ) || (
        SELECT jsonb_build_object(
            -- platform_revenue == spinr_pass_earnings today (Spinr Pass is the
            -- only platform revenue source; 0% on rides). Kept distinct so a
            -- future corporate component can extend platform_revenue alone.
            'spinr_pass_earnings', ROUND(COALESCE(SUM(sp.amount), 0), 2),
            'platform_revenue', ROUND(COALESCE(SUM(sp.amount), 0), 2)
        )
        FROM subscription_payments sp
        WHERE sp.created_at >= p_start AND sp.created_at < p_end
          AND (p_service_area_id IS NULL
               OR sp.driver_id IN (SELECT id FROM drivers WHERE service_area_id = p_service_area_id))
    );
$$;

REVOKE EXECUTE ON FUNCTION admin_dashboard_money(timestamptz, timestamptz, text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION admin_dashboard_money(timestamptz, timestamptz, text) TO service_role;

COMMENT ON FUNCTION admin_dashboard_money(timestamptz, timestamptz, text) IS
    'Main admin dashboard money sums (ride_volume, driver_earnings, spinr_pass_earnings, platform_revenue) for a [start,end) window + optional service area, in one round-trip. platform_revenue = Spinr Pass (0% ride commission). Served by GET /api/admin/analytics/dashboard.';

NOTIFY pgrst, 'reload schema';
