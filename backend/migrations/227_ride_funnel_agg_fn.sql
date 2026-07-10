-- 227_ride_funnel_agg_fn.sql
--
-- Purpose:
--   Ops-funnel counts for GET /api/admin/earnings/overview (Operational
--   health). Answers, for the rides REQUESTED in a window (a created_at
--   cohort, so the numbers read top-to-bottom as a funnel):
--     * requested            — rides created in the window (any status)
--     * reached_searching    — of those, how many entered the dispatch search
--                              (status left 'scheduled'; a scheduled ride that
--                              has not yet dispatched has not searched)
--     * completed            — of those, how many were travelled (completed)
--     * rider_cancelled      — cancelled by the rider/user
--     * driver_cancelled     — cancelled by the driver
--     * cancelled_after_start — cancelled with ride_started_at set (a trip that
--                              had already begun; per the state machine this is
--                              ~0, but surfaced so the operator sees anomalies)
--   Plus price_searches: fare-estimate lookups (migration 226) in the window —
--   the very top of the funnel, before any ride row exists.
--
--   NOTE this is a created_at cohort, so `completed` here differs from the
--   completion-date-based "Completed Trips" KPI on the same page. That is
--   intentional — a funnel follows one cohort of requests through to outcome.
--
-- Cancel attribution mirrors admin_earnings_overview_agg (mig 163): a reason
-- containing 'driver' is a driver cancel; otherwise a reason containing 'rider'
-- or 'user' is a rider cancel (driver takes precedence).
--
-- Indexes: rides(created_at) already exists (mig 50/120, referenced by mig 165);
-- price_searches(created_at) / (service_area_id, created_at) ship in mig 226. No
-- new index here.
--
-- Function safety: read-only, STABLE, SECURITY DEFINER, pinned search_path,
-- EXECUTE revoked from anon/authenticated.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_ride_funnel_agg(timestamptz, timestamptz, text);

CREATE OR REPLACE FUNCTION public.admin_ride_funnel_agg(
    p_start           timestamptz,
    p_end             timestamptz,
    p_service_area_id text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
WITH cohort AS (
    SELECT status, cancellation_reason, ride_started_at
    FROM rides
    WHERE created_at >= p_start AND created_at <= p_end
      AND (p_service_area_id IS NULL OR service_area_id::text = p_service_area_id)
)
SELECT jsonb_build_object(
    'requested',         (SELECT COUNT(*) FROM cohort),
    'reached_searching', (SELECT COUNT(*) FROM cohort WHERE status <> 'scheduled'),
    'completed',         (SELECT COUNT(*) FROM cohort WHERE status = 'completed'),
    'driver_cancelled',  (
        SELECT COUNT(*) FROM cohort
        WHERE status = 'cancelled'
          AND lower(COALESCE(cancellation_reason, '')) LIKE '%driver%'
    ),
    'rider_cancelled',   (
        SELECT COUNT(*) FROM cohort
        WHERE status = 'cancelled'
          AND lower(COALESCE(cancellation_reason, '')) NOT LIKE '%driver%'
          AND (lower(COALESCE(cancellation_reason, '')) LIKE '%rider%'
               OR lower(COALESCE(cancellation_reason, '')) LIKE '%user%')
    ),
    'cancelled_after_start', (
        SELECT COUNT(*) FROM cohort
        WHERE status = 'cancelled' AND ride_started_at IS NOT NULL
    ),
    'price_searches',    (
        SELECT COUNT(*) FROM price_searches
        WHERE created_at >= p_start AND created_at <= p_end
          AND (p_service_area_id IS NULL OR service_area_id = p_service_area_id)
    )
);
$$;

COMMENT ON FUNCTION public.admin_ride_funnel_agg(timestamptz, timestamptz, text) IS
    'Ops-funnel counts (price searches → requested → searching → travelled → cancels) for a created_at cohort window; served by GET /api/admin/earnings/overview.';

REVOKE EXECUTE ON FUNCTION public.admin_ride_funnel_agg(timestamptz, timestamptz, text) FROM anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_ride_funnel_agg(timestamptz, timestamptz, text) TO service_role;

NOTIFY pgrst, 'reload schema';
