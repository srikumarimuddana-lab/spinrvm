-- 350_analytics_regina_buckets_and_area_scope.sql
--
-- migration-override-ok: intentional CREATE OR REPLACE of two existing
-- functions — public.admin_analytics_overview (166, last amended 349) and
-- public.admin_cancellation_breakdown (165, last amended 349) — to change
-- their day/hour bucketing timezone and to add service-area scoping to the
-- overview. Same amendment pattern as 302/303/341/349. Not an accidental
-- redefinition collision; see Purpose below.
--
-- Purpose:
--   Two defects in the Operational Analytics page (/dashboard/analytics),
--   found in a read-through of the page rather than from a bug report —
--   both are silent, and both change what an operator concludes:
--
--   1) DAY/HOUR BUCKETS WERE UTC, THE BUSINESS IS America/Regina.
--      Saskatchewan is UTC-6 year-round (no DST). Both functions bucketed
--      on `AT TIME ZONE 'UTC'`, so:
--        * "Cancellations by Hour" was shifted 6 hours — a 02:00 local
--          spike rendered at 08:00. The axis is the primary input to
--          driver-staffing decisions.
--        * The daily chart's day boundary fell at 18:00 local the previous
--          day, splitting the evening peak (the busiest window for a
--          ride-share) across two bars.
--      This repo already treats UTC day boundaries as a bug elsewhere:
--      migration 347 added driver_daily_stats.day_tz and documents
--      day_tz='regina' as "a deliberate correction to the business day";
--      utils/auto_payout.py, utils/quest_tracker.py and utils/legacy_rides.py
--      all use ZoneInfo("America/Regina"). These two functions were the
--      outliers.
--
--   2) /analytics/overview COULD NOT BE SCOPED TO A SERVICE AREA.
--      admin_cancellation_breakdown and admin_driver_acceptance_rates both
--      already take p_service_area_id; admin_analytics_overview did not, so
--      the page's headline KPI cards (total rides, completion rate,
--      cancellation rate, revenue) always blended every service area
--      together. An operator running Saskatoon and Regina could not see
--      either one on its own, and the KPI targets in CLAUDE.md
--      (match rate, cancellation rate) are per-market judgements.
--
-- Both changes are read-path only. No table, column, index, or RLS policy
-- is touched, and no row is written or migrated.
--
-- Signature change / deploy safety:
--   admin_analytics_overview goes from (timestamptz) to
--   (timestamptz, text DEFAULT NULL). Because CREATE OR REPLACE cannot
--   change a signature, the 1-arg form is DROPped first. The new parameter
--   has a DEFAULT, so a backend still running the old code and calling with
--   only {"p_start": ...} resolves to the new function and behaves exactly
--   as before (p_service_area_id NULL => no area predicate). Safe to apply
--   ahead of the backend deploy, in either order.
--
-- Preserved from earlier amendments (do not drop these when next amending):
--   * migration 349's `legacy_import_metadata = '{}'::jsonb` exclusion on
--     both functions — legacy-imported historical bookings must stay out of
--     live operational KPIs.
--   * admin_cancellation_breakdown's existing p_service_area_id predicate.
--
-- Index: rides(service_area_id, created_at) already exists (migration 310),
-- which is exactly the new overview predicate — no new index needed.
-- Bucketing on an expression of created_at does not change the index used
-- for the range scan, since the WHERE clause still filters on bare
-- created_at.
--
--
-- Grants: Postgres grants EXECUTE to PUBLIC by default on CREATE FUNCTION, so
-- `REVOKE ... FROM anon, authenticated` alone is a NO-OP — both roles keep
-- EXECUTE through PUBLIC. Verified locally on Postgres 16: proacl showed
-- `=X/postgres` (the PUBLIC grant) and has_function_privilege('anon', ...)
-- returned true until PUBLIC was revoked. These are SECURITY DEFINER
-- functions that bypass RLS and return aggregate business data, so the
-- revoke must name PUBLIC. service_role is then granted back explicitly,
-- because it does NOT inherit EXECUTE any other way and the backend calls
-- these through it. Same pattern as migrations 50/296 (purge_pii_retention)
-- and 216 (encrypt/decrypt_driver_pii).
-- Rollback (restores migration 349's exact definitions):
--   DROP FUNCTION IF EXISTS public.admin_analytics_overview(timestamptz, text);
--   -- then re-run the two CREATE OR REPLACE blocks from
--   -- 349_exclude_legacy_cancelled_from_cancellation_analytics.sql
--   -- (lines 139-195 and 205-247) verbatim.

-- ── 1. Operational overview: Regina buckets + service-area scope ──────

DROP FUNCTION IF EXISTS public.admin_analytics_overview(timestamptz);

CREATE OR REPLACE FUNCTION public.admin_analytics_overview(
    p_start           timestamptz,
    p_service_area_id text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    WITH p AS (
        SELECT status, is_scheduled, total_fare, tip_amount,
               -- Business day / hour, not UTC. Saskatchewan is UTC-6
               -- year-round, so this is a fixed -6 shift with no DST seam.
               (created_at AT TIME ZONE 'America/Regina')::date                    AS d,
               EXTRACT(HOUR FROM (created_at AT TIME ZONE 'America/Regina'))::int  AS hr
        FROM rides
        WHERE created_at >= p_start
          AND legacy_import_metadata = '{}'::jsonb
          AND (p_service_area_id IS NULL OR service_area_id::text = p_service_area_id)
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

COMMENT ON FUNCTION public.admin_analytics_overview(timestamptz, text) IS
    'Status counts + completed revenue/tips + daily/hourly buckets for /analytics/overview. '
    'Buckets on America/Regina business days/hours (migration 350). '
    'Optional service-area scope (migration 350). '
    'Excludes legacy-imported rides (migration 349).';

REVOKE EXECUTE ON FUNCTION public.admin_analytics_overview(timestamptz, text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_analytics_overview(timestamptz, text) TO service_role;

-- ── 2. Cancellation breakdown: Regina hour buckets ────────────────────

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
            -- Regina hour, matching the overview's buckets so the two charts
            -- on the same page can no longer disagree by six hours.
            EXTRACT(
                HOUR FROM (COALESCE(cancelled_at, updated_at, created_at) AT TIME ZONE 'America/Regina')
            )::int AS hr
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

COMMENT ON FUNCTION public.admin_cancellation_breakdown(timestamptz, text) IS
    'Cancellation reason/party/hour breakdown for /analytics/cancellation-reasons. '
    'Hour buckets on America/Regina (migration 350). '
    'Excludes legacy-imported rides (migration 349).';

REVOKE EXECUTE ON FUNCTION public.admin_cancellation_breakdown(timestamptz, text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_cancellation_breakdown(timestamptz, text) TO service_role;
