-- Migration 55: Driver dispatch partial composite index (B-P2-5)
--
-- The dispatch hot path (services/dispatch_service.py:192-202) filters on:
--   is_online    = TRUE
--   is_available = TRUE
--   is_verified  = TRUE
--   status       = 'active'
--   vehicle_type_id = <ride's vehicle type>
--
-- Without an index, every dispatch hits a sequential scan of the entire
-- drivers table. Even with a few thousand drivers, the predicate
-- evaluation cost adds milliseconds per dispatch — and dispatch latency
-- is a CLAUDE.md KPI (P95 < 2 s offer→accept). The four boolean+status
-- filters are very selective (typically <5% of drivers are
-- "ready-to-dispatch" at any moment), so a partial index keyed on
-- vehicle_type_id is dramatically smaller than a full multi-column
-- index, fits in cache, and turns the scan into an index-only lookup
-- of just the matching `vehicle_type_id` slice.
--
-- CONCURRENTLY: avoids the AccessExclusiveLock that blocks the dispatch
-- path itself during build. Migration runner must execute this outside
-- a transaction (matches the pattern in 34_rides_performance_indexes.sql,
-- 50_pii_retention_purge.sql, 53_rides_one_active_per_rider.sql).
--
-- Reversibility: DROP INDEX CONCURRENTLY IF EXISTS idx_drivers_dispatch_ready;
-- No data changes; safe to remove anytime if the planner regresses.
--
-- Forward compat: pure additive index. Old code paths keep working;
-- new dispatch reads automatically benefit once the index is built.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_drivers_dispatch_ready
    ON drivers (vehicle_type_id)
    WHERE is_online = TRUE
      AND is_available = TRUE
      AND is_verified = TRUE
      AND status = 'active';

COMMENT ON INDEX idx_drivers_dispatch_ready IS
  'B-P2-5: partial composite for the dispatch query in services/dispatch_service.py. Covers the "ready-to-dispatch" slice of drivers indexed by vehicle_type_id.';
