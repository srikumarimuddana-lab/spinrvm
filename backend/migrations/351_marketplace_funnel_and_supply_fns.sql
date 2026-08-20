-- 351_marketplace_funnel_and_supply_fns.sql
--
-- Purpose:
--   Adds the two marketplace-health aggregates the Operational Analytics page
--   needs to answer business questions rather than only "which ride failed":
--
--     admin_marketplace_funnel(p_start, p_end, p_service_area_id)
--       requested -> matched -> accepted -> completed, with the drop-off at
--       each stage and the attributed cancellation split. Feeds the CLAUDE.md
--       KPI targets that had no surface at all before this: match rate
--       (>= 85%), rider cancellation rate (<= 8%), driver cancellation rate
--       (<= 3%).
--
--     admin_supply_utilization(p_start, p_end, p_service_area_id)
--       Online / en-route / on-trip seconds from the append-only
--       driver_insurance_periods ledger, and therefore driver utilization
--       (CLAUDE.md KPI target >= 55%). That ledger is the authoritative
--       record of where a driver's time went — it is the same data the
--       regulator sees — so utilization derived from it cannot disagree with
--       the insurance-period audit trail.
--
--   Both are read-only, STABLE, SECURITY DEFINER with a pinned search_path,
--   EXECUTE revoked from anon/authenticated — matching 165/166/350.
--
-- Both bucket on America/Regina business days (migration 350's precedent;
-- 347's day_tz='regina'). Both exclude legacy-imported rides
-- (legacy_import_metadata = '{}'::jsonb) per migration 349 — these are live
-- operational KPIs and historical pre-Spinr bookings must not skew them.
--
-- ── Definitions, and their honest limits ────────────────────────────────
--
--   matched  = assigned_at IS NOT NULL OR driver_id IS NOT NULL.
--              `rides` stores only the CURRENT status, so a stage is derived
--              from durable timestamps, never from status alone — a ride that
--              was matched and later cancelled still counts as matched.
--
--   accepted = a recorded accepted offer, OR ride_started_at IS NOT NULL,
--              OR status = 'completed'. The union is deliberate: ride_offers
--              is the direct signal, but any ride that started or completed
--              was definitionally accepted, so the extra arms stop a
--              dispatch path that does not write ride_offers (or a
--              backfilled row) from silently undercounting acceptance.
--
--   Cancellation attribution prefers the STRUCTURED columns cancelled_by /
--   cancellation_type (migration 38, added expressly "so the admin panel can
--   filter on them and reports can aggregate without parsing reason
--   strings") and falls back to the legacy reason-string heuristic only when
--   cancelled_by IS NULL — i.e. for rows written before migration 38. The
--   count of rows that needed the fallback is returned as
--   `cancels_unattributed_fallback`, so an operator can see how much of the
--   split rests on string matching rather than trusting it blindly.
--
--   Utilization is reported TWO ways because the term is ambiguous and the
--   difference is large:
--     utilization_pct         = P3 / (P1+P2+P3)  -- on-trip / online.
--                               This is CLAUDE.md's stated definition
--                               ("on-trip time / online time", target >= 55%).
--     engaged_pct             = (P2+P3) / (P1+P2+P3)  -- en-route + on-trip.
--                               Closer to what a driver considers "working",
--                               since Period 2 is unpaid-but-committed time.
--   Reporting only one would let the two readings be conflated.
--
--   Period rows are CLAMPED to the window: a period that started before
--   p_start or is still open contributes only its overlap with [p_start,
--   p_end]. Without the clamp, a driver online since last month would add a
--   month of "online time" to a 7-day window.
--
-- ── Index ───────────────────────────────────────────────────────────────
--   driver_insurance_periods had no index supporting a time-window scan
--   across all drivers — only (driver_id, started_at DESC) and the partial
--   open-period unique index. admin_supply_utilization's predicate is
--   exactly that new pattern, so the index ships in this migration per
--   backend/migrations/CLAUDE.md ("add the index in the same migration").
--   CONCURRENTLY so it cannot lock a table on the regulatory audit path;
--   run_migrations.py splits CONCURRENTLY statements out of the transaction.
--
--   rides(service_area_id, created_at) already exists (migration 310) and
--   covers the funnel's predicate. No new rides index needed.
--
-- Forward-compatible: two new functions and one new index. No table, column,
-- constraint, or existing function is altered. Nothing is written or migrated.
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
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_marketplace_funnel(timestamptz, timestamptz, text);
--   DROP FUNCTION IF EXISTS public.admin_supply_utilization(timestamptz, timestamptz, text);
--   DROP INDEX CONCURRENTLY IF EXISTS idx_dip_started_at;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dip_started_at
    ON driver_insurance_periods (started_at);

-- ── Marketplace funnel ──────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.admin_marketplace_funnel(
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
            status,
            (created_at AT TIME ZONE 'America/Regina')::date AS d,
            (assigned_at IS NOT NULL OR driver_id IS NOT NULL) AS is_matched,
            (
                ride_started_at IS NOT NULL
                OR status = 'completed'
                OR EXISTS (
                    SELECT 1 FROM ride_offers o
                    WHERE o.ride_id = rides.id AND o.status = 'accepted'
                )
            ) AS is_accepted,
            -- Structured attribution first (migration 38); reason-string
            -- heuristic only for pre-38 rows.
            CASE
                WHEN status <> 'cancelled' THEN NULL
                WHEN cancelled_by IN ('rider', 'driver', 'admin', 'system') THEN cancelled_by
                WHEN cancellation_type = 'no_drivers_found' THEN 'system'
                WHEN cancellation_reason IS NULL OR cancellation_reason = '' THEN 'unknown'
                WHEN lower(cancellation_reason) LIKE '%no nearby drivers%'
                  OR lower(cancellation_reason) LIKE '%no driver%' THEN 'system'
                WHEN lower(cancellation_reason) LIKE '%rider%' THEN 'rider'
                WHEN lower(cancellation_reason) LIKE '%driver%' THEN 'driver'
                ELSE 'unknown'
            END AS cancel_party,
            (status = 'cancelled' AND cancelled_by IS NULL) AS cancel_needed_fallback,
            (
                status = 'cancelled'
                AND (
                    cancellation_type = 'no_drivers_found'
                    OR (
                        cancellation_type IS NULL
                        AND (lower(COALESCE(cancellation_reason, '')) LIKE '%no nearby drivers%'
                          OR lower(COALESCE(cancellation_reason, '')) LIKE '%no driver%')
                    )
                )
            ) AS is_no_supply
        FROM rides
        WHERE created_at >= p_start
          AND created_at <= p_end
          AND legacy_import_metadata = '{}'::jsonb
          AND (p_service_area_id IS NULL OR service_area_id::text = p_service_area_id)
    )
    SELECT jsonb_build_object(
        'requested',  (SELECT COUNT(*) FROM r),
        'matched',    (SELECT COUNT(*) FROM r WHERE is_matched),
        'accepted',   (SELECT COUNT(*) FROM r WHERE is_accepted),
        'completed',  (SELECT COUNT(*) FROM r WHERE status = 'completed'),
        'cancelled',  (SELECT COUNT(*) FROM r WHERE status = 'cancelled'),
        'in_flight',  (SELECT COUNT(*) FROM r
                       WHERE status IN ('searching', 'driver_assigned', 'driver_accepted',
                                        'driver_arrived', 'in_progress', 'scheduled')),
        'no_supply',  (SELECT COUNT(*) FROM r WHERE is_no_supply),
        'cancels_by_party', (
            SELECT COALESCE(jsonb_object_agg(cancel_party, cnt), '{}'::jsonb)
            FROM (
                SELECT cancel_party, COUNT(*) AS cnt
                FROM r WHERE cancel_party IS NOT NULL GROUP BY cancel_party
            ) c
        ),
        'cancels_unattributed_fallback', (SELECT COUNT(*) FROM r WHERE cancel_needed_fallback),
        'daily', (
            SELECT COALESCE(jsonb_agg(obj ORDER BY obj->>'date'), '[]'::jsonb)
            FROM (
                SELECT jsonb_build_object(
                    'date',      d::text,
                    'requested', COUNT(*),
                    'matched',   COUNT(*) FILTER (WHERE is_matched),
                    'accepted',  COUNT(*) FILTER (WHERE is_accepted),
                    'completed', COUNT(*) FILTER (WHERE status = 'completed'),
                    'cancelled', COUNT(*) FILTER (WHERE status = 'cancelled'),
                    'no_supply', COUNT(*) FILTER (WHERE is_no_supply)
                ) AS obj
                FROM r WHERE d IS NOT NULL GROUP BY d
            ) x
        )
    );
$$;

COMMENT ON FUNCTION public.admin_marketplace_funnel(timestamptz, timestamptz, text) IS
    'Request->matched->accepted->completed funnel + attributed cancellation split for '
    '/analytics/marketplace-funnel. Regina business days (350). Excludes legacy imports (349). '
    'Prefers structured cancelled_by/cancellation_type (38) over reason-string parsing, and '
    'reports how many rows needed the legacy fallback.';

REVOKE EXECUTE ON FUNCTION public.admin_marketplace_funnel(timestamptz, timestamptz, text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_marketplace_funnel(timestamptz, timestamptz, text) TO service_role;

-- ── Supply & utilization ────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.admin_supply_utilization(
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
    WITH clamped AS (
        SELECT
            p.driver_id,
            p.period,
            GREATEST(p.started_at, p_start)                  AS s,
            LEAST(COALESCE(p.ended_at, p_end), p_end)        AS e
        FROM driver_insurance_periods p
        WHERE p.period > 0
          AND p.started_at <= p_end
          AND (p.ended_at IS NULL OR p.ended_at >= p_start)
          AND (
            p_service_area_id IS NULL
            OR p.driver_id IN (
                SELECT d.id FROM drivers d WHERE d.service_area_id::text = p_service_area_id
            )
          )
    ),
    seg AS (
        SELECT driver_id, period,
               EXTRACT(EPOCH FROM (e - s))                        AS secs,
               (s AT TIME ZONE 'America/Regina')::date            AS d
        FROM clamped
        WHERE e > s
    ),
    tot AS (
        SELECT
            COALESCE(SUM(secs) FILTER (WHERE period = 1), 0) AS p1,
            COALESCE(SUM(secs) FILTER (WHERE period = 2), 0) AS p2,
            COALESCE(SUM(secs) FILTER (WHERE period = 3), 0) AS p3
        FROM seg
    )
    SELECT jsonb_build_object(
        'idle_seconds',     (SELECT p1 FROM tot),
        'en_route_seconds', (SELECT p2 FROM tot),
        'on_trip_seconds',  (SELECT p3 FROM tot),
        'online_seconds',   (SELECT p1 + p2 + p3 FROM tot),
        -- CLAUDE.md's definition: on-trip / online. Target >= 55%.
        'utilization_pct', (
            SELECT CASE WHEN (p1 + p2 + p3) > 0
                        THEN ROUND((p3 / (p1 + p2 + p3) * 100)::numeric, 1)
                        ELSE 0 END
            FROM tot
        ),
        -- En-route counts as working from the driver's point of view.
        'engaged_pct', (
            SELECT CASE WHEN (p1 + p2 + p3) > 0
                        THEN ROUND(((p2 + p3) / (p1 + p2 + p3) * 100)::numeric, 1)
                        ELSE 0 END
            FROM tot
        ),
        'active_drivers', (SELECT COUNT(DISTINCT driver_id) FROM seg),
        'daily', (
            SELECT COALESCE(jsonb_agg(obj ORDER BY obj->>'date'), '[]'::jsonb)
            FROM (
                SELECT jsonb_build_object(
                    'date',             d::text,
                    'online_hours',     ROUND((SUM(secs) / 3600.0)::numeric, 2),
                    'on_trip_hours',    ROUND((SUM(secs) FILTER (WHERE period = 3) / 3600.0)::numeric, 2),
                    'active_drivers',   COUNT(DISTINCT driver_id),
                    'utilization_pct',  CASE WHEN SUM(secs) > 0
                        THEN ROUND((COALESCE(SUM(secs) FILTER (WHERE period = 3), 0) / SUM(secs) * 100)::numeric, 1)
                        ELSE 0 END
                ) AS obj
                FROM seg WHERE d IS NOT NULL GROUP BY d
            ) x
        )
    );
$$;

COMMENT ON FUNCTION public.admin_supply_utilization(timestamptz, timestamptz, text) IS
    'Online/en-route/on-trip seconds and driver utilization from driver_insurance_periods, for '
    '/analytics/supply-utilization. Periods clamped to the window. Regina business days (350). '
    'Reports utilization_pct (P3/online, CLAUDE.md KPI) and engaged_pct ((P2+P3)/online) separately.';

REVOKE EXECUTE ON FUNCTION public.admin_supply_utilization(timestamptz, timestamptz, text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_supply_utilization(timestamptz, timestamptz, text) TO service_role;
