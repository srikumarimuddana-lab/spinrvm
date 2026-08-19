-- 341_exclude_legacy_from_remaining_admin_money_aggregates.sql
--
-- migration-override-ok: intentional CREATE OR REPLACE of three existing
-- functions — public.admin_earnings_overview_agg (last defined in migration
-- 227, originally 163), public.admin_earnings_daily_series (163),
-- admin_dashboard_money (194) — to add the legacy-ride exclusion predicate,
-- exactly the same amendment pattern migrations 302/303 already used for
-- admin_ride_money_rollup/admin_payouts_overview_aggregates. Not an
-- accidental redefinition collision; see Purpose below for the full
-- rationale.
--
-- Purpose:
--   Found in this session's legacy-migration data-quality audit
--   (docs/audit/2026-08-19-full-mongodb-export-collection-inventory.md and
--   its follow-up review): migrations 302/303 excluded legacy-imported rides
--   from admin_ride_money_rollup and admin_payouts_overview_aggregates (the
--   A25/A26/P0-B fix), but three sibling money-aggregate functions were
--   never given the same predicate:
--
--     admin_earnings_overview_agg  (163, re-forked by 227) — GET
--       /admin/earnings/overview's gbv/platform/trips/riders/drivers/
--       surge/promo/tax numbers.
--     admin_earnings_daily_series  (163) — the same page's revenue chart.
--     admin_dashboard_money        (194) — GET /admin/analytics/dashboard,
--       the admin-dashboard HOMEPAGE's ride_volume/driver_earnings cards.
--
--   This is not a theoretical gap: booking_import_service.py preserves each
--   imported ride's true historical ride_completed_at/created_at (correct
--   for GPS/trip-record retention), and the Mongo export's vintage
--   (2026-07-26, cutover 2026-07-29) put all 186 imported rides' dates
--   inside a 30d/MTD window on /earnings/overview at the time of this
--   migration, and inside the homepage's 7d/24h windows for roughly the
--   first week after cutover. Every admin viewing "This Month" or the
--   homepage stat cards in that window saw GBV/revenue/trip-count silently
--   inflated by rides the OLD app already served and was already paid for
--   — the exact double-counting class EXCLUDE_LEGACY_RIDES exists to
--   prevent, on four call sites that were missed.
--
--   Same predicate as 302/303, for the same documented reason:
--   `legacy_import_metadata = '{}'::jsonb`, NOT `IS NULL` — the column is
--   `NOT NULL DEFAULT '{}'::jsonb` (migration 268), so `IS NULL` matches
--   zero rows (excludes everything, not just legacy rows).
--
--   admin_earnings_overview_agg's `cohort`/`cancelled` CTEs (cancellation
--   counts, ops-funnel) are UNCHANGED: booking_import_service.py only ever
--   imports `status='completed'` rows (P2's documented 78% cancelled/
--   failed-booking gap), so no legacy row can appear in a cancelled-ride
--   or funnel count today — only the `completed` CTE (gbv/platform/trips/
--   riders/drivers/surge/promo/tax) needed the predicate.
--
-- Same CREATE OR REPLACE targets as 227 (admin_earnings_overview_agg),
-- 163 (admin_earnings_daily_series), and 194 (admin_dashboard_money) — this
-- is the first amendment to daily_series/dashboard_money, and the second
-- (after 227's funnel rework) to overview_agg.
--
-- Money-function safety: unchanged — read-only, STABLE, SECURITY DEFINER,
-- pinned search_path, EXECUTE revoked from anon/authenticated.
--
-- Rollback:
--   Re-run 227's admin_earnings_overview_agg body, 163's
--   admin_earnings_daily_series body, and 194's admin_dashboard_money body
--   verbatim (restores the unfiltered versions). No new index/column.

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
    -- both the cancelled aggregates below and the funnel keys. Unchanged:
    -- no legacy row can be 'cancelled' (completed-only importer), so no
    -- exclusion is needed here — see this migration's header.
    SELECT status, cancelled_by, cancellation_reason, cancellation_fee_admin,
           ride_started_at, is_scheduled, scheduled_dispatched
    FROM rides
    WHERE created_at >= p_start AND created_at <= p_end
      AND (p_service_area_id IS NULL OR service_area_id::text = p_service_area_id)
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
    FROM cohort
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
    'Completed + cancelled ride aggregates plus ops-funnel counts for one /earnings/overview window. Completed-ride money/count keys exclude legacy-imported rides (migration 341); cancelled/funnel keys need no exclusion (completed-only importer). Cancel attribution via rides.cancelled_by (reason-string fallback for legacy NULLs).';

REVOKE EXECUTE ON FUNCTION public.admin_earnings_overview_agg(timestamptz, timestamptz, text) FROM anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_earnings_overview_agg(timestamptz, timestamptz, text) TO service_role;

CREATE OR REPLACE FUNCTION public.admin_earnings_daily_series(
    p_start           timestamptz,
    p_end             timestamptz,
    p_service_area_id text DEFAULT NULL
)
RETURNS TABLE (day date, gbv numeric, trips bigint, net_revenue numeric)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT (ride_completed_at AT TIME ZONE 'UTC')::date AS day,
           COALESCE(SUM(total_fare::text::numeric), 0)    AS gbv,
           COUNT(*)                                       AS trips,
           COALESCE(SUM(admin_earnings::text::numeric), 0) AS net_revenue
    FROM rides
    WHERE status = 'completed'
      AND ride_completed_at >= p_start AND ride_completed_at <= p_end
      AND (p_service_area_id IS NULL OR service_area_id::text = p_service_area_id)
      AND legacy_import_metadata = '{}'::jsonb
    GROUP BY 1
    ORDER BY 1;
$$;

COMMENT ON FUNCTION public.admin_earnings_daily_series(timestamptz, timestamptz, text) IS
    'Per-day gbv/trips/net_revenue for the /earnings/overview revenue chart, excluding legacy-imported rides (migration 341).';

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
          AND r.legacy_import_metadata = '{}'::jsonb
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
    'Main admin dashboard money sums (ride_volume, driver_earnings, spinr_pass_earnings, platform_revenue) for a [start,end) window + optional service area. ride_volume/driver_earnings exclude legacy-imported rides (migration 341). platform_revenue = Spinr Pass (0% ride commission). Served by GET /api/admin/analytics/dashboard.';

NOTIFY pgrst, 'reload schema';
