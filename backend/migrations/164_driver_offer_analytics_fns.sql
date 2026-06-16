-- 164_driver_offer_analytics_fns.sql
--
-- Purpose:
--   The two Driver Offers analytics endpoints each scanned the append-only
--   ride_offers ledger with limit=100000 and rolled it up in Python:
--     * /driver-offer-stats  — per-driver accepted/declined/ignored/preempted/
--       pending counts + average response time.
--     * /driver-offer-trends — per-day offer-outcome counts.
--   These functions do the GROUP BY in Postgres, returning ~N-driver / ~N-day
--   rows instead of streaming up to 100k offer rows into the app.
--
--   Existing indexes cover the predicates: idx_ride_offers_offered_at
--   (migration 130) for the window, idx_ride_offers_driver_status for the
--   per-driver/status rollups — so no new index is added here.
--
-- Semantics mirror the old Python exactly:
--   * offered = every offer in the window (incl. 'cancelled'/'pending'); the
--     status FILTER counts only their respective statuses ('expired' == ignored).
--   * avg_response_secs = AVG(responded_at - offered_at) over offers the driver
--     actually answered (accepted/declined, responded_at >= offered_at); NULL
--     when none (caller renders None).
--   * day bucket = offered_at at UTC calendar day.
--   * area scope = driver_id in drivers with that service_area_id; for trends,
--     a driver_id filter takes precedence and ignores the area (matches old).
--
-- Function safety: read-only, STABLE, SECURITY DEFINER, pinned search_path,
-- EXECUTE revoked from anon/authenticated. (Counts only — no money.)
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_driver_offer_stats(timestamptz, text);
--   DROP FUNCTION IF EXISTS public.admin_driver_offer_trends(timestamptz, text, text);

CREATE OR REPLACE FUNCTION public.admin_driver_offer_stats(
    p_start           timestamptz,
    p_service_area_id text DEFAULT NULL
)
RETURNS TABLE (
    driver_id         text,
    offered           bigint,
    accepted          bigint,
    declined          bigint,
    ignored           bigint,
    preempted         bigint,
    pending           bigint,
    avg_response_secs numeric
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT
        o.driver_id,
        COUNT(*)                                          AS offered,
        COUNT(*) FILTER (WHERE o.status = 'accepted')     AS accepted,
        COUNT(*) FILTER (WHERE o.status = 'declined')     AS declined,
        COUNT(*) FILTER (WHERE o.status = 'expired')      AS ignored,
        COUNT(*) FILTER (WHERE o.status = 'preempted')    AS preempted,
        COUNT(*) FILTER (WHERE o.status = 'pending')      AS pending,
        AVG(EXTRACT(EPOCH FROM (o.responded_at - o.offered_at)))
            FILTER (WHERE o.status IN ('accepted', 'declined')
                      AND o.responded_at IS NOT NULL
                      AND o.responded_at >= o.offered_at)  AS avg_response_secs
    FROM ride_offers o
    WHERE o.offered_at >= p_start
      AND o.driver_id IS NOT NULL
      AND (
        p_service_area_id IS NULL
        OR o.driver_id IN (SELECT d.id FROM drivers d WHERE d.service_area_id::text = p_service_area_id)
      )
    GROUP BY o.driver_id;
$$;

CREATE OR REPLACE FUNCTION public.admin_driver_offer_trends(
    p_start           timestamptz,
    p_driver_id       text DEFAULT NULL,
    p_service_area_id text DEFAULT NULL
)
RETURNS TABLE (
    day       date,
    offered   bigint,
    accepted  bigint,
    declined  bigint,
    ignored   bigint,
    preempted bigint
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT
        (o.offered_at AT TIME ZONE 'UTC')::date           AS day,
        COUNT(*)                                          AS offered,
        COUNT(*) FILTER (WHERE o.status = 'accepted')     AS accepted,
        COUNT(*) FILTER (WHERE o.status = 'declined')     AS declined,
        COUNT(*) FILTER (WHERE o.status = 'expired')      AS ignored,
        COUNT(*) FILTER (WHERE o.status = 'preempted')    AS preempted
    FROM ride_offers o
    WHERE o.offered_at >= p_start
      AND (p_driver_id IS NULL OR o.driver_id = p_driver_id)
      AND (
        p_driver_id IS NOT NULL
        OR p_service_area_id IS NULL
        OR o.driver_id IN (SELECT d.id FROM drivers d WHERE d.service_area_id::text = p_service_area_id)
      )
    GROUP BY 1
    ORDER BY 1;
$$;

COMMENT ON FUNCTION public.admin_driver_offer_stats(timestamptz, text) IS
    'Per-driver ride_offers rollup for /driver-offer-stats. Replaces a 100k-row scan.';
COMMENT ON FUNCTION public.admin_driver_offer_trends(timestamptz, text, text) IS
    'Per-day ride_offers outcome counts for /driver-offer-trends. Replaces a 100k-row scan.';

REVOKE EXECUTE ON FUNCTION public.admin_driver_offer_stats(timestamptz, text) FROM anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.admin_driver_offer_trends(timestamptz, text, text) FROM anon, authenticated;
