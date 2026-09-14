-- 420_dispatch_latency_by_zone.sql
--
-- Purpose:
--   admin_dispatch_latency_by_zone(p_start, p_end, p_service_area_id) — P50/P95
--   offer->accept latency (ms), overall and broken out per service area.
--
--   CLAUDE.md's Performance SLA table names "Dispatch offer -> driver phone
--   notification < 2s (P95)" as a KPI, and the metric IS emitted today
--   (spinr_dispatch_offer_to_accept_duration_ms, backend/routes/drivers/
--   ride_flow.py) -- but only into Prometheus/Grafana (metrics-agent/), never
--   into the admin dashboard, and never broken out by service area. An
--   operator watching the heatmap page's existing "Live Demand Pressure"
--   panel (which is already per-zone: demand, idle supply, surge tier) has
--   no way to see whether a *specific* zone is actually dispatching fast —
--   this migration adds that using data already durably stored in Postgres
--   (ride_offers.offered_at/responded_at), not a new metrics pipeline.
--
--   Source of truth: ride_offers rows with status = 'accepted' and a
--   recorded responded_at. duration_ms = responded_at - offered_at, exactly
--   the same delta the in-process Prometheus histogram observes at
--   backend/routes/drivers/ride_flow.py's "spinr_dispatch_offer_to_accept_
--   duration_ms" call site (accept_ride, using winner_rows[0].offered_at vs
--   now()) -- this migration reconstructs the same measurement from durable
--   storage instead of scraping Prometheus, so the admin dashboard doesn't
--   need a new Prometheus-query integration.
--
--   Same conventions as 351 (admin_marketplace_funnel/admin_supply_utilization):
--   read-only, STABLE, SECURITY DEFINER, pinned search_path, EXECUTE revoked
--   from PUBLIC/anon/authenticated (Postgres grants EXECUTE to PUBLIC by
--   default on CREATE FUNCTION, so revoking PUBLIC explicitly is required,
--   not a no-op -- see 351's own header for the verified reasoning), granted
--   to service_role only. Buckets on America/Regina business days (350).
--   Excludes legacy-imported rides (legacy_import_metadata = '{}'::jsonb,
--   349) -- a backfilled historical ride never had a real dispatch offer
--   cycle and must not skew a live dispatch-latency KPI.
--
--   Rows with a NULL offered_at or responded_at are excluded (defensive --
--   ride_offers.offered_at is NOT NULL DEFAULT now() at the column level, so
--   this should never happen, but a duration computed from a NULL would
--   otherwise silently vanish from percentile_cont rather than being visibly
--   excluded).
--
-- Index:
--   ride_offers had no index supporting a time-range scan of accepted rows
--   by responded_at (existing indexes: ride_id, (driver_id, status), a
--   partial index on ride_id where status='pending', and a partial unique
--   index on ride_id where status='accepted' for the one-accepted-per-ride
--   invariant -- none support this new predicate). Per backend/migrations/
--   CLAUDE.md ("add the index in the same migration"), a partial index on
--   (responded_at) WHERE status = 'accepted' is added, CONCURRENTLY so it
--   cannot lock ride_offers on the live dispatch-acceptance write path.
--   run_migrations.py splits CONCURRENTLY statements out of the transaction.
--
-- Forward-compatible: one new function and one new index. No table, column,
-- constraint, or existing function is altered. Nothing is written or migrated.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_dispatch_latency_by_zone(timestamptz, timestamptz, text);
--   DROP INDEX CONCURRENTLY IF EXISTS idx_ride_offers_accepted_responded;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ride_offers_accepted_responded
    ON ride_offers (responded_at)
    WHERE status = 'accepted';

CREATE OR REPLACE FUNCTION public.admin_dispatch_latency_by_zone(
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
    WITH accepted AS (
        SELECT
            r.service_area_id,
            EXTRACT(EPOCH FROM (o.responded_at - o.offered_at)) * 1000.0 AS duration_ms
        FROM ride_offers o
        JOIN rides r ON r.id = o.ride_id
        WHERE o.status = 'accepted'
          AND o.offered_at IS NOT NULL
          AND o.responded_at IS NOT NULL
          AND o.responded_at >= p_start
          AND o.responded_at <= p_end
          AND r.legacy_import_metadata = '{}'::jsonb
          AND (p_service_area_id IS NULL OR r.service_area_id::text = p_service_area_id)
    )
    SELECT jsonb_build_object(
        'sample_count', (SELECT COUNT(*) FROM accepted),
        'p50_ms', (SELECT ROUND(percentile_cont(0.5)  WITHIN GROUP (ORDER BY duration_ms)::numeric, 0) FROM accepted),
        'p95_ms', (SELECT ROUND(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)::numeric, 0) FROM accepted),
        'by_zone', (
            SELECT COALESCE(jsonb_agg(obj ORDER BY obj->>'service_area_id'), '[]'::jsonb)
            FROM (
                SELECT jsonb_build_object(
                    'service_area_id', COALESCE(service_area_id, 'unknown'),
                    'sample_count',    COUNT(*),
                    'p50_ms',          ROUND(percentile_cont(0.5)  WITHIN GROUP (ORDER BY duration_ms)::numeric, 0),
                    'p95_ms',          ROUND(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)::numeric, 0)
                ) AS obj
                FROM accepted
                GROUP BY service_area_id
            ) x
        )
    );
$$;

COMMENT ON FUNCTION public.admin_dispatch_latency_by_zone(timestamptz, timestamptz, text) IS
    'P50/P95 dispatch offer->accept latency (ms), overall and per service area, from '
    'ride_offers.offered_at/responded_at -- the durable-storage equivalent of the '
    'spinr_dispatch_offer_to_accept_duration_ms Prometheus histogram. Regina business days '
    '(350). Excludes legacy imports (349). For /analytics/dispatch-latency.';

REVOKE EXECUTE ON FUNCTION public.admin_dispatch_latency_by_zone(timestamptz, timestamptz, text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_dispatch_latency_by_zone(timestamptz, timestamptz, text) TO service_role;
