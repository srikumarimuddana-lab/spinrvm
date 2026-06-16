-- 165_analytics_count_fns.sql
--
-- Purpose:
--   Two more admin analytics endpoints aggregated rows in Python:
--     * /driver-acceptance — fetched up to 10,000 rides and rolled per-driver
--       completed/cancelled-by-driver counts.
--     * /cancellation-breakdown — fetched up to 5,000 cancelled rides and
--       classified reasons / party / hour in Python.
--   These functions do the grouping in Postgres. Counts only — no money.
--
--   Existing indexes cover the predicates: idx_rides_driver_created
--   (driver_id, created_at) for acceptance; rides(created_at) + the cancelled
--   partial index idx_rides_cancelled_created (migration 163) for the
--   cancellation window — so no new index here.
--
-- Semantics mirror the old Python exactly:
--   * acceptance: over rides with created_at >= p_start (optionally area-scoped
--     via drivers); per driver_id: total = COUNT(*), completed = status
--     'completed', cancelled_by_driver = status 'cancelled' AND reason ILIKE
--     '%driver%'. Drivers with no rides in window simply don't appear (caller
--     defaults them to zero from its own driver list).
--   * cancellation reason classification uses the SAME priority chain as the
--     old Python (no-driver -> rider -> driver -> timeout/expired -> scheduled
--     -> other; empty -> unspecified) and the same party attribution; the hour
--     bucket is the UTC hour of COALESCE(cancelled_at, updated_at, created_at).
--
-- Function safety: read-only, STABLE, SECURITY DEFINER, pinned search_path,
-- EXECUTE revoked from anon/authenticated.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_driver_acceptance_rates(timestamptz, text);
--   DROP FUNCTION IF EXISTS public.admin_cancellation_breakdown(timestamptz, text);

CREATE OR REPLACE FUNCTION public.admin_driver_acceptance_rates(
    p_start           timestamptz,
    p_service_area_id text DEFAULT NULL
)
RETURNS TABLE (
    driver_id           text,
    total_rides         bigint,
    completed           bigint,
    cancelled_by_driver bigint
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT
        r.driver_id,
        COUNT(*)                                       AS total_rides,
        COUNT(*) FILTER (WHERE r.status = 'completed') AS completed,
        COUNT(*) FILTER (
            WHERE r.status = 'cancelled' AND lower(r.cancellation_reason) LIKE '%driver%'
        )                                              AS cancelled_by_driver
    FROM rides r
    WHERE r.created_at >= p_start
      AND r.driver_id IS NOT NULL
      AND (
        p_service_area_id IS NULL
        OR r.driver_id IN (SELECT d.id FROM drivers d WHERE d.service_area_id::text = p_service_area_id)
      )
    GROUP BY r.driver_id;
$$;

CREATE OR REPLACE FUNCTION public.admin_cancellation_breakdown(
    p_start           timestamptz,
    p_service_area_id text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    WITH c AS (
        SELECT
            CASE
                WHEN cancellation_reason IS NULL OR cancellation_reason = '' THEN 'unspecified'
                WHEN lower(cancellation_reason) LIKE '%no nearby drivers%'
                  OR lower(cancellation_reason) LIKE '%no driver%' THEN 'no_drivers_available'
                WHEN lower(cancellation_reason) LIKE '%rider%' THEN 'rider_cancelled'
                WHEN lower(cancellation_reason) LIKE '%driver%' THEN 'driver_cancelled'
                WHEN lower(cancellation_reason) LIKE '%timeout%'
                  OR lower(cancellation_reason) LIKE '%expired%' THEN 'search_timeout'
                WHEN lower(cancellation_reason) LIKE '%scheduled%' THEN 'scheduled_cancelled'
                ELSE 'other'
            END AS reason,
            CASE
                WHEN cancellation_reason IS NULL OR cancellation_reason = '' THEN 'unknown'
                WHEN lower(cancellation_reason) LIKE '%no nearby drivers%'
                  OR lower(cancellation_reason) LIKE '%no driver%' THEN 'unknown'
                WHEN lower(cancellation_reason) LIKE '%rider%' THEN 'rider'
                WHEN lower(cancellation_reason) LIKE '%driver%' THEN 'driver'
                WHEN lower(cancellation_reason) LIKE '%timeout%'
                  OR lower(cancellation_reason) LIKE '%expired%' THEN 'unknown'
                WHEN lower(cancellation_reason) LIKE '%scheduled%' THEN 'rider'
                ELSE 'unknown'
            END AS party,
            EXTRACT(HOUR FROM (COALESCE(cancelled_at, updated_at, created_at) AT TIME ZONE 'UTC'))::int AS hr
        FROM rides
        WHERE status = 'cancelled'
          AND created_at >= p_start
          AND (p_service_area_id IS NULL OR service_area_id::text = p_service_area_id)
    )
    SELECT jsonb_build_object(
        'total', (SELECT COUNT(*) FROM c),
        'reasons', (
            SELECT COALESCE(jsonb_agg(jsonb_build_object('reason', reason, 'count', cnt) ORDER BY cnt DESC), '[]'::jsonb)
            FROM (SELECT reason, COUNT(*) AS cnt FROM c GROUP BY reason) r
        ),
        'by_party', (
            SELECT COALESCE(jsonb_agg(jsonb_build_object('party', party, 'count', cnt) ORDER BY cnt DESC), '[]'::jsonb)
            FROM (SELECT party, COUNT(*) AS cnt FROM c GROUP BY party) p
        ),
        'hourly', (
            SELECT COALESCE(jsonb_object_agg(hr::text, cnt), '{}'::jsonb)
            FROM (SELECT hr, COUNT(*) AS cnt FROM c WHERE hr IS NOT NULL GROUP BY hr) h
        )
    );
$$;

COMMENT ON FUNCTION public.admin_driver_acceptance_rates(timestamptz, text) IS
    'Per-driver total/completed/cancelled-by-driver ride counts for /driver-acceptance.';
COMMENT ON FUNCTION public.admin_cancellation_breakdown(timestamptz, text) IS
    'Cancellation reason/party/hour breakdown for /cancellation-breakdown.';

REVOKE EXECUTE ON FUNCTION public.admin_driver_acceptance_rates(timestamptz, text) FROM anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.admin_cancellation_breakdown(timestamptz, text) FROM anon, authenticated;
