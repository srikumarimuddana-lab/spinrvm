-- 138_drivers_estimate_recency_index.sql
-- /rides/estimate fetches the candidate driver page with:
--   WHERE is_online = TRUE AND is_available = TRUE
--   ORDER BY went_online_at DESC LIMIT 200
-- (routes/rides.py: estimate_ride — recently-online drivers fill the page
-- first so heartbeat-expired ghosts fall toward the tail.)
--
-- The existing partial indexes cover the dispatch filters
-- (idx_drivers_online_available_type keyed by vehicle_type_id,
-- idx_drivers_dispatch_ready) but neither matches this ORDER BY, so the
-- planner sorts every ready driver on each estimate call — a path with a
-- <300ms P95 budget that runs on every rider price check. A partial index
-- ordered by went_online_at lets Postgres walk in order and stop at the
-- LIMIT with no sort and no heap filter.
--
-- CONCURRENTLY: runner detects it and applies in autocommit mode, so the
-- build never blocks live estimate/dispatch traffic.
-- Forward-compatible: additive index only, safe against in-flight traffic.
-- Rollback:
--   DROP INDEX CONCURRENTLY IF EXISTS idx_drivers_online_available_recency;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_drivers_online_available_recency
    ON drivers (went_online_at DESC)
    WHERE is_online = TRUE AND is_available = TRUE;

COMMENT ON INDEX idx_drivers_online_available_recency IS
  'Estimate-path candidate page: ready drivers ordered by went_online_at DESC (routes/rides.py estimate_ride).';
