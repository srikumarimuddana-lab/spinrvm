-- 349_exclude_legacy_cancelled_from_cancellation_analytics.sql
--
-- migration-override-ok: intentional CREATE OR REPLACE of four existing
-- functions — public.admin_cancellation_breakdown (165),
-- public.admin_driver_acceptance_rates (165),
-- public.admin_analytics_overview (166), and
-- public.admin_earnings_overview_agg (last defined 341, originally 163/227)
-- — to add the legacy-import exclusion predicate. Same amendment pattern as
-- 302/303/341. Not an accidental redefinition collision; see Purpose below.
--
-- Purpose:
--   This session's cancelled/failed legacy-booking import (A41 follow-up,
--   docs/change-log/2026-08-20-legacy-cancelled-failed-booking-import.md)
--   extends booking_import_service.py to import legacy `cancelled`/`failed`
--   bookings as rides.status='cancelled' rows (previously only `completed`
--   bookings were importable). Four admin analytics functions read
--   status='cancelled' rides with no legacy exclusion and will start
--   silently counting these historical, pre-Spinr rows once the importer
--   ships, skewing live-operational KPIs (rider/driver cancellation rate is
--   a monitored KPI, CLAUDE.md target <=8%/<=3%):
--
--     admin_cancellation_breakdown  (165) -- GET /admin/cancellation-breakdown's
--       reason/party/hour cancellation breakdown. Had ZERO legacy exclusion.
--     admin_driver_acceptance_rates (165) -- GET /admin/driver-acceptance's
--       per-driver total/completed/cancelled_by_driver counts. Had ZERO
--       legacy exclusion. Found via this migration's own blast-radius check
--       (same file as admin_cancellation_breakdown, same rides-scan
--       pattern) rather than named ahead of time in the task -- flagged
--       explicitly in the change log as a 6th function beyond the 5
--       originally scoped. A legacy row only reaches this function when its
--       driver_id matched a real Spinr driver by phone (269/712 cancelled +
--       14/225 failed rows in the actual export), and the cancelled_by_driver
--       bucket keys off `cancellation_reason ILIKE '%driver%'` -- which the
--       new import's own synthetic fallback text ("No driver found (legacy
--       import)") would itself match, on top of genuine legacy free-text
--       reasons mentioning "driver". Excluded unconditionally (all three
--       counts: total_rides/completed/cancelled_by_driver), matching how
--       302 excluded admin_ride_money_rollup unconditionally, since a
--       matched driver's legacy-imported completed rides (already shipped
--       ride import, pre-dating this session) skew total_rides/completed
--       here too -- not a new gap introduced by this migration, but the
--       same predicate fixes both in one pass with no schema change.
--     admin_analytics_overview      (166) -- GET /analytics/overview's
--       total/completed/cancelled/in_progress/searching/scheduled counts +
--       daily/hourly buckets. Had ZERO legacy exclusion on any key.
--       Excluded unconditionally at the source CTE, matching how 341's
--       admin_dashboard_money (the homepage's equivalent overview) excludes
--       legacy rides from its own totals.
--     admin_earnings_overview_agg   (341) -- GET /earnings/overview's
--       cx_count/cx_revenue/cx_rider_cancels/cx_driver_cancels/
--       fn_cancelled_after_start keys. 341's own COMMENT explicitly states
--       "cancelled/funnel keys need no exclusion (completed-only
--       importer)" -- that assumption is exactly what this session's
--       importer change invalidates. Fixed via a NEW `cancelled_src` CTE
--       (legacy-excluded) feeding only the existing `cancelled` CTE.
--
--       `cohort` (fn_requested/fn_reached_searching/fn_completed/
--       fn_price_searches) gets a narrower, targeted fix, NOT the blanket
--       `legacy_import_metadata = '{}'::jsonb` predicate used everywhere
--       else in this migration: `AND NOT (status = 'cancelled' AND
--       legacy_import_metadata != '{}'::jsonb)`. Reasoning (caught in
--       review -- the first draft of this migration left `cohort` fully
--       unfiltered and mis-framed the whole gap as "pre-existing"):
--         - Legacy-imported CANCELLED rows are brand new as of this exact
--           change set (booking_import_service.py's new branch, same
--           session) -- before this, `cohort` could never contain one.
--           Leaving them unfiltered would have silently inflated
--           fn_requested/fn_reached_searching the moment this ships,
--           depressing the apparent funnel conversion rate for any window
--           overlapping the legacy booking dates (2026-07 era) -- a REAL,
--           NEWLY-INTRODUCED skew, not a pre-existing one.
--         - Legacy-imported COMPLETED rides, by contrast, are NOT new --
--           271 of them have been importable and counted in this exact
--           funnel since migration 227 shipped, 2026-07-29-cutover-adjacent,
--           predating this session entirely. Blanket-filtering `cohort`
--           would retroactively change fn_requested/fn_reached_searching/
--           fn_completed numbers for those already-live, already-relied-on
--           rows too -- a materially different, separate decision this
--           migration does not make (CLAUDE.md: no silent behavior change
--           to a live-tested flow; additive over destructive).
--       The narrower predicate closes exactly the new gap and nothing more:
--       a legacy-imported COMPLETED row still counts in cohort exactly as
--       it always has; a legacy-imported CANCELLED row now correctly never
--       does, since it never could have before this session's change.
--
--   NOT changed (verified, no fix needed):
--     admin_earnings_daily_series (341) -- status='completed' only, already
--       excludes legacy (migration 341). Never counts a cancelled row.
--     admin_dashboard_money       (341) -- status='completed' only, already
--       excludes legacy (migration 341). Never counts a cancelled row.
--
--   Same predicate as 302/303/341, for the same documented reason:
--   `legacy_import_metadata = '{}'::jsonb`, NOT `IS NULL` -- the column is
--   `NOT NULL DEFAULT '{}'::jsonb` (migration 268), so `IS NULL` matches
--   zero rows (excludes everything, not just legacy rows).
--
-- Money-function safety: unchanged -- read-only, STABLE, SECURITY DEFINER,
-- pinned search_path, EXECUTE revoked from anon/authenticated.
--
-- Rollback:
--   Re-run 165's admin_cancellation_breakdown / admin_driver_acceptance_rates
--   bodies, 166's admin_analytics_overview body, and 341's
--   admin_earnings_overview_agg body verbatim (restores the unfiltered
--   versions). No new index/column.

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
      AND r.legacy_import_metadata = '{}'::jsonb
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
          AND legacy_import_metadata = '{}'::jsonb
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
    'Per-driver total/completed/cancelled-by-driver ride counts for /driver-acceptance. Excludes legacy-imported rides (migration 349).';
COMMENT ON FUNCTION public.admin_cancellation_breakdown(timestamptz, text) IS
    'Cancellation reason/party/hour breakdown for /cancellation-breakdown. Excludes legacy-imported rides (migration 349).';

REVOKE EXECUTE ON FUNCTION public.admin_driver_acceptance_rates(timestamptz, text) FROM anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.admin_cancellation_breakdown(timestamptz, text) FROM anon, authenticated;

CREATE OR REPLACE FUNCTION public.admin_analytics_overview(
    p_start timestamptz
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    WITH p AS (
        SELECT status, is_scheduled, total_fare, tip_amount,
               (created_at AT TIME ZONE 'UTC')::date              AS d,
               EXTRACT(HOUR FROM (created_at AT TIME ZONE 'UTC'))::int AS hr
        FROM rides
        WHERE created_at >= p_start
          AND legacy_import_metadata = '{}'::jsonb
    )
    SELECT jsonb_build_object(
        'total',       (SELECT COUNT(*) FROM p),
        'completed',   (SELECT COUNT(*) FROM p WHERE status = 'completed'),
        'cancelled',   (SELECT COUNT(*) FROM p WHERE status = 'cancelled'),
        'in_progress', (SELECT COUNT(*) FROM p WHERE status IN ('in_progress', 'driver_arrived', 'driver_accepted')),
        'searching',   (SELECT COUNT(*) FROM p WHERE status = 'searching'),
        'scheduled',   (SELECT COUNT(*) FROM p WHERE is_scheduled),
        'total_revenue', COALESCE((SELECT SUM(total_fare::text::numeric) FROM p WHERE status = 'completed'), 0),
        'total_tips',    COALESCE((SELECT SUM(tip_amount::text::numeric) FROM p WHERE status = 'completed'), 0),
        'daily', (
            SELECT COALESCE(jsonb_object_agg(d::text, obj), '{}'::jsonb)
            FROM (
                SELECT d, jsonb_build_object(
                    'completed', COUNT(*) FILTER (WHERE status = 'completed'),
                    'cancelled', COUNT(*) FILTER (WHERE status = 'cancelled'),
                    'total', COUNT(*)
                ) AS obj
                FROM p WHERE d IS NOT NULL GROUP BY d
            ) x
        ),
        'hourly', (
            SELECT COALESCE(jsonb_object_agg(hr::text, cnt), '{}'::jsonb)
            FROM (SELECT hr, COUNT(*) AS cnt FROM p WHERE hr IS NOT NULL GROUP BY hr) y
        )
    );
$$;

COMMENT ON FUNCTION public.admin_analytics_overview(timestamptz) IS
    'Status counts + completed revenue/tips + daily/hourly buckets for /analytics/overview. Excludes legacy-imported rides (migration 349).';

REVOKE EXECUTE ON FUNCTION public.admin_analytics_overview(timestamptz) FROM anon, authenticated;

CREATE OR REPLACE FUNCTION public.admin_earnings_overview_agg(
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
WITH completed AS (
    SELECT total_fare, admin_earnings, rider_id, driver_id,
           surge_multiplier, discount_amount, tax_breakdown
    FROM rides
    WHERE status = 'completed'
      AND ride_completed_at >= p_start AND ride_completed_at <= p_end
      AND (p_service_area_id IS NULL OR service_area_id::text = p_service_area_id)
      AND legacy_import_metadata = '{}'::jsonb
),
cohort AS (
    -- Rides REQUESTED in the window (created_at cohort, any status) — feeds
    -- the funnel keys only (fn_requested/fn_reached_searching/fn_completed).
    -- Whether legacy-imported COMPLETED rides should count toward funnel
    -- volume is a separate, pre-existing question (271 such rides have been
    -- counted here since migration 227, predating this session) that this
    -- migration deliberately does NOT decide — those rows are untouched.
    -- Legacy-imported CANCELLED rides are different: they did not exist
    -- until this exact change set (booking_import_service.py's new branch,
    -- same session), so excluding them here closes a gap this migration
    -- itself introduces, not a pre-existing one — see 349's header for the
    -- full reasoning. cancellation counting below reads `cancelled_src`
    -- instead of this CTE for the same reason.
    SELECT status, cancelled_by, cancellation_reason, cancellation_fee_admin,
           ride_started_at, is_scheduled, scheduled_dispatched
    FROM rides
    WHERE created_at >= p_start AND created_at <= p_end
      AND (p_service_area_id IS NULL OR service_area_id::text = p_service_area_id)
      AND NOT (status = 'cancelled' AND legacy_import_metadata != '{}'::jsonb)
),
cancelled_src AS (
    -- Same window as `cohort`, plus the legacy exclusion migration 349 adds:
    -- the cancelled/failed legacy booking importer (2026-08-20) now writes
    -- status='cancelled' rows here, which must not inflate cx_*/
    -- fn_cancelled_after_start.
    SELECT status, cancelled_by, cancellation_reason, cancellation_fee_admin,
           ride_started_at
    FROM rides
    WHERE created_at >= p_start AND created_at <= p_end
      AND (p_service_area_id IS NULL OR service_area_id::text = p_service_area_id)
      AND legacy_import_metadata = '{}'::jsonb
),
cancelled AS (
    SELECT cancellation_fee_admin, ride_started_at,
           CASE
               WHEN cancelled_by IN ('rider', 'driver') THEN cancelled_by
               WHEN cancelled_by IS NOT NULL THEN 'system'  -- admin / system / other
               -- Legacy NULL cancelled_by: guarded reason heuristic. No-driver
               -- MUST be checked before '%driver%' ('No nearby drivers found'
               -- contains 'drivers') — mirrors migration 165.
               WHEN lower(COALESCE(cancellation_reason, '')) LIKE '%no nearby driver%'
                 OR lower(COALESCE(cancellation_reason, '')) LIKE '%no driver%' THEN 'system'
               WHEN lower(COALESCE(cancellation_reason, '')) LIKE '%driver%' THEN 'driver'
               WHEN lower(COALESCE(cancellation_reason, '')) LIKE '%rider%'
                 OR lower(COALESCE(cancellation_reason, '')) LIKE '%user%' THEN 'rider'
               ELSE 'system'
           END AS cancel_party
    FROM cancelled_src
    WHERE status = 'cancelled'
),
tax AS (
    SELECT t.key AS k, (t.value ->> 'amount') AS amt
    FROM completed c, LATERAL jsonb_each(c.tax_breakdown) t
    WHERE jsonb_typeof(c.tax_breakdown) = 'object'
)
SELECT jsonb_build_object(
    'gbv',           COALESCE((SELECT SUM(total_fare::text::numeric) FROM completed), 0),
    'platform',      COALESCE((SELECT SUM(admin_earnings::text::numeric) FROM completed), 0),
    'trips',         (SELECT COUNT(*) FROM completed),
    'riders',        (SELECT COUNT(DISTINCT rider_id) FROM completed WHERE rider_id IS NOT NULL),
    'drivers',       (SELECT COUNT(DISTINCT driver_id) FROM completed WHERE driver_id IS NOT NULL),
    'surge_revenue', COALESCE((
        SELECT SUM(total_fare::text::numeric - total_fare::text::numeric / surge_multiplier::text::numeric)
        FROM completed
        WHERE surge_multiplier IS NOT NULL AND surge_multiplier::text::numeric > 1
    ), 0),
    'promo_spend',   COALESCE((SELECT SUM(discount_amount::text::numeric) FROM completed), 0),
    'promo_count',   (SELECT COUNT(*) FROM completed WHERE COALESCE(discount_amount, 0)::text::numeric > 0),
    'gst_collected', COALESCE((SELECT SUM(amt::numeric) FROM tax WHERE lower(k) LIKE '%gst%' OR lower(k) = 'hst'), 0),
    'pst_collected', COALESCE((SELECT SUM(amt::numeric) FROM tax WHERE lower(k) LIKE '%pst%' OR lower(k) LIKE '%qst%'), 0),
    'cx_count',      (SELECT COUNT(*) FROM cancelled),
    'cx_revenue',    COALESCE((SELECT SUM(cancellation_fee_admin::text::numeric) FROM cancelled), 0),
    'cx_rider_cancels',  (SELECT COUNT(*) FROM cancelled WHERE cancel_party = 'rider'),
    'cx_driver_cancels', (SELECT COUNT(*) FROM cancelled WHERE cancel_party = 'driver'),
    'fn_requested',         (SELECT COUNT(*) FROM cohort),
    'fn_reached_searching', (
        SELECT COUNT(*) FROM cohort
        WHERE COALESCE(is_scheduled, false) = false
           OR COALESCE(scheduled_dispatched, false) = true
    ),
    'fn_completed',             (SELECT COUNT(*) FROM cohort WHERE status = 'completed'),
    'fn_cancelled_after_start', (SELECT COUNT(*) FROM cancelled WHERE ride_started_at IS NOT NULL),
    'fn_price_searches',        (
        SELECT COUNT(*) FROM price_searches
        WHERE created_at >= p_start AND created_at <= p_end
          AND (p_service_area_id IS NULL OR service_area_id = p_service_area_id)
    )
);
$$;

COMMENT ON FUNCTION public.admin_earnings_overview_agg(timestamptz, timestamptz, text) IS
    'Completed + cancelled ride aggregates plus ops-funnel counts for one /earnings/overview window. Completed-ride and cancelled-ride money/count keys exclude legacy-imported rides (migrations 341, 349). fn_requested/fn_reached_searching/fn_completed exclude legacy-imported CANCELLED rides only (new as of 349, closing a gap this migration itself introduces); legacy-imported COMPLETED rides still count in the funnel as they have since migration 227 (pre-existing, unchanged, out of scope). Cancel attribution via rides.cancelled_by (reason-string fallback for legacy NULLs).';

REVOKE EXECUTE ON FUNCTION public.admin_earnings_overview_agg(timestamptz, timestamptz, text) FROM anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_earnings_overview_agg(timestamptz, timestamptz, text) TO service_role;

NOTIFY pgrst, 'reload schema';
