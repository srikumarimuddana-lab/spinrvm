-- Migration 411: cancellation breakdown — structured attribution + rider reasons
--
-- migration-override-ok: intentional CREATE OR REPLACE of an existing function
-- — public.admin_cancellation_breakdown (165, last amended 350) — to fix a
-- data-quality bug and add a genuine rider-cancellation-reason breakdown.
-- Same amendment pattern as 302/303/341/349/350.
--
-- Purpose
-- -------
-- Migration 38 added rides.cancelled_by / cancellation_type "so reports can
-- aggregate without parsing reason strings" (its own header). This function
-- was never updated to use them — it still classifies who cancelled by
-- fuzzy-matching the free-text cancellation_reason, e.g.
-- `lower(cancellation_reason) LIKE '%driver%'`.
--
-- That is a real, previously-unnoticed bug (found reading the rider-app
-- cancel flow, not from a bug report): CancelReasonSheet.tsx's rider-facing
-- preset "Driver is too far / long wait" contains the substring "driver", so
-- a RIDER cancelling for exactly this reason gets classified as a
-- driver_cancelled / party='driver' row. This is the single CLAUDE.md-named
-- diagnostic for the rider-cancellation-rate KPI ("Long wait or wrong ETA")
-- and it was being misattributed to the wrong party, hiding the signal the
-- KPI exists to catch.
--
-- Fix pattern is not new to this codebase: migration 351's
-- admin_marketplace_funnel already established "structured attribution
-- first, reason-string heuristic only as a fallback for pre-38 rows" for
-- exactly this classification. This migration ports that same, already-
-- reviewed pattern into admin_cancellation_breakdown, and does not invent a
-- new approach.
--
-- Changes
-- -------
--   1. `reason` and `party` (both already returned) now prefer
--      cancelled_by/cancellation_type over string-matching. Bucket NAMES are
--      unchanged (rider_cancelled/driver_cancelled/no_drivers_available/
--      search_timeout/scheduled_cancelled/unspecified/other for `reason`;
--      rider/driver/admin/system/unknown for `party`, matching
--      admin_marketplace_funnel's vocabulary) — only the classification
--      LOGIC changes, so no frontend contract break. One new `reason` value,
--      `admin_cancelled`, appears for the first time now that admin
--      force-cancels are correctly distinguished instead of falling into
--      `other`; the frontend's REASON_LABELS/REASON_COLORS maps handle an
--      unknown key gracefully already (`REASON_COLORS[r.reason] || "#9CA3AF"`),
--      so this is additive, not breaking.
--   2. New `rider_reasons` + `total_rider_cancellations` keys: the reason
--      breakdown riders actually pick in CancelReasonSheet.tsx (driver too
--      far/long wait, booked by mistake, found another ride, wrong pickup
--      location, changed plans, the scheduled-pre-dispatch cancel path, or
--      free text), scoped to rows this function's own `party` resolves to
--      'rider' — the thing "rider cancellation reason analytics" is
--      actually asking for, which the existing `reason`/`party` fields
--      cannot answer (they say WHO cancelled, never WHY a rider did).
--
-- Everything else (Regina-timezone hourly bucketing from 350, legacy-import
-- exclusion from 349, service-area scoping) is carried over unchanged.
--
-- Money-function safety: unchanged — read-only, STABLE, SECURITY DEFINER,
-- pinned search_path, EXECUTE revoked from PUBLIC/anon/authenticated.
--
-- Rollback:
--   Re-run migration 350's admin_cancellation_breakdown body verbatim
--   (lines 144-212 of 350_analytics_regina_buckets_and_area_scope.sql).
--   No schema change, no data written — purely a read-path revert.

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
            cancellation_reason,
            -- Structured attribution first (migration 38); reason-string
            -- heuristic only for pre-38 rows where cancelled_by is NULL.
            -- Bucket names unchanged from the prior (string-only) version.
            CASE
                WHEN cancellation_type = 'no_drivers_found' THEN 'no_drivers_available'
                WHEN cancelled_by = 'rider'  THEN 'rider_cancelled'
                WHEN cancelled_by = 'driver' THEN 'driver_cancelled'
                WHEN cancelled_by = 'admin'  THEN 'admin_cancelled'
                WHEN cancelled_by = 'system' THEN 'no_drivers_available'
                WHEN cancellation_reason IS NULL OR cancellation_reason = '' THEN 'unspecified'
                WHEN lower(cancellation_reason) LIKE '%no nearby drivers%'
                  OR lower(cancellation_reason) LIKE '%no driver%' THEN 'no_drivers_available'
                WHEN lower(cancellation_reason) LIKE '%timeout%'
                  OR lower(cancellation_reason) LIKE '%expired%' THEN 'search_timeout'
                WHEN lower(cancellation_reason) LIKE '%scheduled%' THEN 'scheduled_cancelled'
                WHEN lower(cancellation_reason) LIKE '%rider%' THEN 'rider_cancelled'
                WHEN lower(cancellation_reason) LIKE '%driver%' THEN 'driver_cancelled'
                ELSE 'other'
            END AS reason,
            -- Same structured-first pattern and vocabulary (rider/driver/
            -- admin/system/unknown) as admin_marketplace_funnel (351) — do
            -- not let this and that function's classification drift apart.
            CASE
                WHEN cancelled_by IN ('rider', 'driver', 'admin', 'system') THEN cancelled_by
                WHEN cancellation_type = 'no_drivers_found' THEN 'system'
                WHEN cancellation_reason IS NULL OR cancellation_reason = '' THEN 'unknown'
                WHEN lower(cancellation_reason) LIKE '%no nearby drivers%'
                  OR lower(cancellation_reason) LIKE '%no driver%' THEN 'unknown'
                WHEN lower(cancellation_reason) LIKE '%timeout%'
                  OR lower(cancellation_reason) LIKE '%expired%' THEN 'unknown'
                WHEN lower(cancellation_reason) LIKE '%scheduled%' THEN 'rider'
                WHEN lower(cancellation_reason) LIKE '%rider%' THEN 'rider'
                WHEN lower(cancellation_reason) LIKE '%driver%' THEN 'driver'
                ELSE 'unknown'
            END AS party,
            -- Regina hour, matching the overview's buckets (350).
            EXTRACT(
                HOUR FROM (COALESCE(cancelled_at, updated_at, created_at) AT TIME ZONE 'America/Regina')
            )::int AS hr
        FROM rides
        WHERE status = 'cancelled'
          AND created_at >= p_start
          AND legacy_import_metadata = '{}'::jsonb
          AND (p_service_area_id IS NULL OR service_area_id::text = p_service_area_id)
    ),
    -- What a rider actually picked in CancelReasonSheet.tsx, for rows this
    -- function's own `party` resolves to 'rider'. The app composes
    -- "<preset> — <note>" (or just "<preset>" with no note, or free text
    -- alone when the preset was "Other") — prefix-matched against the five
    -- non-free-text presets; the scheduled pre-dispatch path's literal
    -- "Cancelled by rider (scheduled)" (routes/rides/cancellation.py) gets
    -- its own bucket since it is a technical label, not a stated reason.
    rc AS (
        SELECT
            CASE
                WHEN cancellation_reason IS NULL OR cancellation_reason = '' THEN 'unspecified'
                WHEN cancellation_reason ILIKE 'Driver is too far / long wait%' THEN 'driver_too_far_long_wait'
                WHEN cancellation_reason ILIKE 'Booked by mistake%'             THEN 'booked_by_mistake'
                WHEN cancellation_reason ILIKE 'Found another ride%'            THEN 'found_another_ride'
                WHEN cancellation_reason ILIKE 'Pickup location is wrong%'      THEN 'pickup_location_wrong'
                WHEN cancellation_reason ILIKE 'Changed my plans%'              THEN 'changed_plans'
                WHEN cancellation_reason ILIKE 'Cancelled by rider (scheduled)%' THEN 'scheduled_pre_dispatch'
                ELSE 'other_free_text'
            END AS rider_reason
        FROM c
        WHERE party = 'rider'
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
        ),
        'total_rider_cancellations', (SELECT COUNT(*) FROM rc),
        'rider_reasons', (
            SELECT COALESCE(jsonb_agg(jsonb_build_object('reason', rider_reason, 'count', cnt) ORDER BY cnt DESC), '[]'::jsonb)
            FROM (SELECT rider_reason, COUNT(*) AS cnt FROM rc GROUP BY rider_reason) rr
        )
    );
$$;

COMMENT ON FUNCTION public.admin_cancellation_breakdown(timestamptz, text) IS
    'Cancellation reason/party/hour breakdown for /analytics/cancellation-reasons, '
    'plus a rider-specific reason breakdown (rider_reasons/total_rider_cancellations). '
    'Prefers structured cancelled_by/cancellation_type (38) over reason-string parsing '
    'for reason/party (411; same pattern as admin_marketplace_funnel, 351). '
    'Hour buckets on America/Regina (350). Excludes legacy-imported rides (349).';

REVOKE EXECUTE ON FUNCTION public.admin_cancellation_breakdown(timestamptz, text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_cancellation_breakdown(timestamptz, text) TO service_role;
