-- 352_efficiency_and_financial_fns.sql
--
-- Purpose:
--   The second half of the marketplace-metrics set begun in 351. Adds:
--
--     admin_efficiency_metrics(p_start, p_end, p_service_area_id)
--       Time-to-match, time-to-pickup, promised-vs-actual pickup ETA error,
--       and deadhead ratio. These are the rider-experience and
--       driver-economics levers: a rising time-to-match means supply is
--       thin before the cancellation rate has moved, and deadhead is
--       unpaid driver km, which matters more here than at a
--       commission-taking operator because the driver keeps 100% of the
--       fare and absorbs that cost directly.
--
--     admin_financial_metrics(p_start, p_end, p_service_area_id)
--       Gross bookings, fare composition, surge penetration, corporate vs
--       consumer split, and rider repeat rate.
--
--   Both read-only, STABLE, SECURITY DEFINER, pinned search_path, EXECUTE
--   revoked from anon/authenticated. Regina business days (350). Legacy
--   imports excluded (349).
--
-- ── Definitions, and their honest limits ────────────────────────────────
--
--   time_to_match  = assigned_at - created_at, over rides that were matched
--                    at all. Rides that never matched are EXCLUDED rather
--                    than counted as infinite — the funnel's match_rate
--                    (351) is where unmatched demand is measured. Reporting
--                    a median that silently omits failures is only honest
--                    if the failure count is visible elsewhere; it is.
--                    `matched_sample` is returned so the denominator is
--                    never guessed.
--
--   time_to_pickup = ride_started_at - assigned_at. Spans driver
--                    acceptance AND the drive to the pickup point, because
--                    no arrival timestamp column exists on `rides`. It is
--                    therefore an upper bound on drive time, not a pure
--                    one — named "assignment to trip start" in the API to
--                    avoid implying otherwise.
--
--   eta_error      = (ride_started_at - responded_at) - eta_seconds, using
--                    the ACCEPTED offer's promised eta_seconds. Positive =
--                    the rider waited longer than promised. Only rides with
--                    both an accepted offer carrying a non-null
--                    eta_seconds and a trip start qualify; `eta_sample` is
--                    returned so a small sample cannot masquerade as a
--                    fleet-wide figure.
--
--   deadhead_ratio = SUM(pickup_to_driver_km) / SUM(actual_distance_km),
--                    i.e. unpaid approach km per paid km. Computed as a
--                    ratio of sums, not a mean of per-ride ratios, so one
--                    short trip with a long approach cannot dominate.
--
--   repeat_rate    = riders with >= 2 completed rides in the window, over
--                    distinct riders with >= 1. This is a WITHIN-WINDOW
--                    measure, not a retention cohort: on a 7-day window it
--                    reads far lower than on 90 days, by construction. The
--                    API labels it accordingly. A true cohort (rider's
--                    first ride in month N, still riding in N+1) needs a
--                    different query and is deliberately NOT approximated
--                    here.
--
--   Money: SUMs are cast ::text::numeric to match the existing convention
--   in 166/341 (the columns are a mix of numeric and double precision).
--   Every division is done in numeric, never floating point. The Python
--   layer wraps these in Decimal before rounding, per CLAUDE.md.
--
-- ── Indexes ─────────────────────────────────────────────────────────────
--   rides(service_area_id, created_at) (migration 310) covers both
--   functions' predicates — the same window+area scan 350/351 already use.
--   ride_offers is reached by (ride_id) via idx_ride_offers_ride_id
--   (migration 100). No new index required; none is added, since an
--   unused index costs write throughput on the ride path for nothing.
--
-- Forward-compatible: two new functions. No table, column, constraint, or
-- existing function altered. Nothing written or migrated.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_efficiency_metrics(timestamptz, timestamptz, text);
--   DROP FUNCTION IF EXISTS public.admin_financial_metrics(timestamptz, timestamptz, text);

-- ── Efficiency ──────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.admin_efficiency_metrics(
    p_start           timestamptz,
    p_end             timestamptz DEFAULT now(),
    p_service_area_id text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    WITH r AS (
        SELECT
            id,
            assigned_at,
            ride_started_at,
            created_at,
            status,
            pickup_to_driver_km,
            actual_distance_km
        FROM rides
        WHERE created_at >= p_start
          AND created_at <= p_end
          AND legacy_import_metadata = '{}'::jsonb
          AND (p_service_area_id IS NULL OR service_area_id::text = p_service_area_id)
    ),
    ttm AS (
        SELECT EXTRACT(EPOCH FROM (assigned_at - created_at)) AS secs
        FROM r
        WHERE assigned_at IS NOT NULL AND assigned_at >= created_at
    ),
    ttp AS (
        SELECT EXTRACT(EPOCH FROM (ride_started_at - assigned_at)) AS secs
        FROM r
        WHERE ride_started_at IS NOT NULL
          AND assigned_at IS NOT NULL
          AND ride_started_at >= assigned_at
    ),
    eta AS (
        SELECT
            EXTRACT(EPOCH FROM (r.ride_started_at - o.responded_at)) - o.eta_seconds AS err_secs
        FROM r
        JOIN ride_offers o
          ON o.ride_id = r.id
         AND o.status = 'accepted'
         AND o.responded_at IS NOT NULL
         AND o.eta_seconds IS NOT NULL
        WHERE r.ride_started_at IS NOT NULL
          AND r.ride_started_at >= o.responded_at
    )
    SELECT jsonb_build_object(
        'matched_sample',        (SELECT COUNT(*) FROM ttm),
        'time_to_match_p50_secs', (SELECT ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY secs)::numeric, 1) FROM ttm),
        'time_to_match_p95_secs', (SELECT ROUND(percentile_cont(0.95) WITHIN GROUP (ORDER BY secs)::numeric, 1) FROM ttm),
        'pickup_sample',         (SELECT COUNT(*) FROM ttp),
        'time_to_pickup_p50_secs', (SELECT ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY secs)::numeric, 1) FROM ttp),
        'time_to_pickup_p95_secs', (SELECT ROUND(percentile_cont(0.95) WITHIN GROUP (ORDER BY secs)::numeric, 1) FROM ttp),
        'eta_sample',            (SELECT COUNT(*) FROM eta),
        'eta_error_p50_secs',    (SELECT ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY err_secs)::numeric, 1) FROM eta),
        'eta_error_p95_secs',    (SELECT ROUND(percentile_cont(0.95) WITHIN GROUP (ORDER BY err_secs)::numeric, 1) FROM eta),
        -- Share of pickups that beat the promised ETA.
        'eta_on_time_pct', (
            SELECT CASE WHEN COUNT(*) > 0
                        THEN ROUND(COUNT(*) FILTER (WHERE err_secs <= 0)::numeric / COUNT(*) * 100, 1)
                        ELSE 0 END
            FROM eta
        ),
        'deadhead_km',    COALESCE((SELECT ROUND(SUM(pickup_to_driver_km)::numeric, 1) FROM r WHERE status = 'completed'), 0),
        'paid_km',        COALESCE((SELECT ROUND(SUM(actual_distance_km)::numeric, 1) FROM r WHERE status = 'completed'), 0),
        -- Ratio of sums, not mean of ratios: one short trip with a long
        -- approach must not dominate the fleet figure.
        'deadhead_ratio_pct', (
            SELECT CASE WHEN COALESCE(SUM(actual_distance_km), 0) > 0
                        THEN ROUND((SUM(pickup_to_driver_km) / SUM(actual_distance_km) * 100)::numeric, 1)
                        ELSE 0 END
            FROM r WHERE status = 'completed'
        )
    );
$$;

COMMENT ON FUNCTION public.admin_efficiency_metrics(timestamptz, timestamptz, text) IS
    'Time-to-match, assignment-to-trip-start, promised-vs-actual pickup ETA error and deadhead '
    'ratio for /analytics/efficiency. Returns sample sizes alongside every percentile. '
    'Excludes legacy imports (349).';

REVOKE EXECUTE ON FUNCTION public.admin_efficiency_metrics(timestamptz, timestamptz, text) FROM anon, authenticated;

-- ── Financial ───────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.admin_financial_metrics(
    p_start           timestamptz,
    p_end             timestamptz DEFAULT now(),
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
            rider_id,
            corporate_account_id,
            surge_multiplier,
            (created_at AT TIME ZONE 'America/Regina')::date AS d,
            total_fare::text::numeric      AS fare,
            COALESCE(tip_amount, 0)::text::numeric      AS tip,
            COALESCE(tax_amount, 0)::text::numeric      AS tax,
            COALESCE(discount_amount, 0)::text::numeric AS discount
        FROM rides
        WHERE status = 'completed'
          AND created_at >= p_start
          AND created_at <= p_end
          AND legacy_import_metadata = '{}'::jsonb
          AND (p_service_area_id IS NULL OR service_area_id::text = p_service_area_id)
    ),
    riders AS (
        SELECT rider_id, COUNT(*) AS rides
        FROM c WHERE rider_id IS NOT NULL GROUP BY rider_id
    )
    SELECT jsonb_build_object(
        'completed_rides', (SELECT COUNT(*) FROM c),
        'gross_bookings',  COALESCE((SELECT ROUND(SUM(fare), 2) FROM c), 0),
        'tips',            COALESCE((SELECT ROUND(SUM(tip), 2) FROM c), 0),
        'tax',             COALESCE((SELECT ROUND(SUM(tax), 2) FROM c), 0),
        'discounts',       COALESCE((SELECT ROUND(SUM(discount), 2) FROM c), 0),
        'avg_fare', (
            SELECT CASE WHEN COUNT(*) > 0 THEN ROUND(SUM(fare) / COUNT(*), 2) ELSE 0 END FROM c
        ),
        -- Surge penetration: how much of the book is priced above 1.0x, and
        -- the incremental revenue attributable to the multiplier.
        'surge_rides',   (SELECT COUNT(*) FROM c WHERE COALESCE(surge_multiplier, 1) > 1),
        'surge_pct', (
            SELECT CASE WHEN COUNT(*) > 0
                        THEN ROUND(COUNT(*) FILTER (WHERE COALESCE(surge_multiplier, 1) > 1)::numeric / COUNT(*) * 100, 1)
                        ELSE 0 END
            FROM c
        ),
        'avg_surge_multiplier', (
            SELECT COALESCE(ROUND(AVG(surge_multiplier::text::numeric), 2), 1)
            FROM c WHERE COALESCE(surge_multiplier, 1) > 1
        ),
        'surge_revenue', COALESCE((
            SELECT ROUND(SUM(fare - fare / surge_multiplier::text::numeric), 2)
            FROM c WHERE COALESCE(surge_multiplier, 1) > 1
        ), 0),
        'corporate_rides',    (SELECT COUNT(*) FROM c WHERE corporate_account_id IS NOT NULL),
        'corporate_bookings', COALESCE((SELECT ROUND(SUM(fare), 2) FROM c WHERE corporate_account_id IS NOT NULL), 0),
        'consumer_rides',     (SELECT COUNT(*) FROM c WHERE corporate_account_id IS NULL),
        'consumer_bookings',  COALESCE((SELECT ROUND(SUM(fare), 2) FROM c WHERE corporate_account_id IS NULL), 0),
        'unique_riders', (SELECT COUNT(*) FROM riders),
        'repeat_riders', (SELECT COUNT(*) FROM riders WHERE rides >= 2),
        -- WITHIN-WINDOW repeat share, not a retention cohort. Reads lower on
        -- short windows by construction; the API labels it as such.
        'repeat_rate_pct', (
            SELECT CASE WHEN COUNT(*) > 0
                        THEN ROUND(COUNT(*) FILTER (WHERE rides >= 2)::numeric / COUNT(*) * 100, 1)
                        ELSE 0 END
            FROM riders
        ),
        'daily', (
            SELECT COALESCE(jsonb_agg(obj ORDER BY obj->>'date'), '[]'::jsonb)
            FROM (
                SELECT jsonb_build_object(
                    'date',           d::text,
                    'rides',          COUNT(*),
                    'gross_bookings', ROUND(SUM(fare), 2),
                    'avg_fare',       ROUND(SUM(fare) / COUNT(*), 2)
                ) AS obj
                FROM c WHERE d IS NOT NULL GROUP BY d
            ) x
        )
    );
$$;

COMMENT ON FUNCTION public.admin_financial_metrics(timestamptz, timestamptz, text) IS
    'Gross bookings, fare composition, surge penetration, corporate/consumer split and '
    'within-window rider repeat rate for /analytics/financial. Regina business days (350). '
    'Excludes legacy imports (349). repeat_rate_pct is within-window, NOT a retention cohort.';

REVOKE EXECUTE ON FUNCTION public.admin_financial_metrics(timestamptz, timestamptz, text) FROM anon, authenticated;
