-- 161_ride_money_rollup_fn.sql
--
-- Purpose:
--   /stats, /rides/stats and /rides/financials each fetched completed rides
--   (limit 5,000–10,000) only to SUM money columns in a Python loop. This
--   function returns those sums for a [p_start, p_end) window of completed
--   rides (by ride_completed_at) in one round-trip.
--
--   p_end NULL means "no upper bound" (matches the /stats and /rides/stats
--   "today"/"month" queries, which use ride_completed_at >= start only).
--
-- Cent-faithful: the money columns are FLOAT, so every component is cast
-- ::text::numeric BEFORE summing — and driver_revenue casts each of
-- base/distance/time separately before adding — so the totals match the old
-- per-row Decimal(str(float)) arithmetic exactly. rider_paid uses
-- COALESCE(grand_total, total_fare, 0) so a comped $0 ride (grand_total = 0)
-- counts as 0, not the pre-promo fare — matching the Python _rider_paid().
--
-- Money-function safety: read-only, STABLE, SECURITY DEFINER, pinned
-- search_path, EXECUTE revoked from anon/authenticated.
--
-- Note: the index uses CONCURRENTLY (rides is a hot table); the runner detects
-- CONCURRENTLY and applies the file in autocommit mode.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_ride_money_rollup(timestamptz, timestamptz);
--   DROP INDEX IF EXISTS idx_rides_completed_at_completed;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_completed_at_completed
    ON public.rides (ride_completed_at) WHERE status = 'completed';

CREATE OR REPLACE FUNCTION public.admin_ride_money_rollup(
    p_start timestamptz,
    p_end   timestamptz DEFAULT NULL
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    WITH c AS (
        SELECT
            COALESCE(total_fare, 0)::text::numeric      AS total_fare,
            COALESCE(tip_amount, 0)::text::numeric      AS tip_amount,
            COALESCE(tax_amount, 0)::text::numeric      AS tax_amount,
            COALESCE(discount_amount, 0)::text::numeric AS discount_amount,
            COALESCE(area_fees_total, 0)::text::numeric AS area_fees_total,
            COALESCE(admin_earnings, 0)::text::numeric  AS admin_earnings,
            COALESCE(driver_earnings, 0)::text::numeric AS driver_earnings,
            COALESCE(base_fare, 0)::text::numeric
              + COALESCE(distance_fare, 0)::text::numeric
              + COALESCE(time_fare, 0)::text::numeric   AS driver_revenue,
            COALESCE(grand_total, total_fare, 0)::text::numeric AS rider_paid
        FROM rides
        WHERE status = 'completed'
          AND ride_completed_at >= p_start
          AND (p_end IS NULL OR ride_completed_at < p_end)
    )
    SELECT jsonb_build_object(
        'completed_count',     (SELECT COUNT(*) FROM c),
        'sum_total_fare',      COALESCE((SELECT SUM(total_fare) FROM c), 0),
        'sum_tip',             COALESCE((SELECT SUM(tip_amount) FROM c), 0),
        'sum_tax',             COALESCE((SELECT SUM(tax_amount) FROM c), 0),
        'sum_discount',        COALESCE((SELECT SUM(discount_amount) FROM c), 0),
        'sum_area_fees',       COALESCE((SELECT SUM(area_fees_total) FROM c), 0),
        'sum_admin_earnings',  COALESCE((SELECT SUM(admin_earnings) FROM c), 0),
        'sum_driver_earnings', COALESCE((SELECT SUM(driver_earnings) FROM c), 0),
        'sum_driver_revenue',  COALESCE((SELECT SUM(driver_revenue) FROM c), 0),
        'sum_rider_paid',      COALESCE((SELECT SUM(rider_paid) FROM c), 0)
    );
$$;

COMMENT ON FUNCTION public.admin_ride_money_rollup(timestamptz, timestamptz) IS
    'Completed-ride money sums for a [start,end) window (end NULL = open). '
    'Replaces row-fetch+Python-sum in /stats, /rides/stats, /rides/financials.';

REVOKE EXECUTE ON FUNCTION public.admin_ride_money_rollup(timestamptz, timestamptz)
    FROM anon, authenticated;
