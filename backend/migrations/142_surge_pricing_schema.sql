-- 142_surge_pricing_schema.sql
-- Rollback plan (note: comments in this file must not contain semicolons —
--   CONCURRENTLY forces the runner's autocommit path, which naively splits
--   the file on every semicolon, including inside comments):
--   1. DROP INDEX CONCURRENTLY IF EXISTS idx_surge_pricing_area_created
--   2. DROP POLICY IF EXISTS surge_pricing_service_only ON surge_pricing
--   3. ALTER TABLE surge_pricing DISABLE ROW LEVEL SECURITY
--   Do NOT drop the table in production — it pre-exists this migration
--   (created outside the migration flow) and holds live surge history.
--
-- Context: surge_pricing is the largest table in production (52 MB,
-- 241k rows) with zero indexes — it was created ad hoc in the Supabase
-- dashboard and never brought under migration control. The surge engine
-- (utils/surge_engine.py) appends one history row per service area per
-- 2-minute tick. The only readers are the admin surge-history chart
-- (routes/admin/analytics.py, max lookback 168 h) and an existence
-- check (routes/admin/service_areas.py). Not user PII — internal
-- market metrics. The 90-day retention purge lives in migration 143
-- because a $$-quoted plpgsql body cannot share a file with the
-- autocommit semicolon splitting described above.
--
-- This migration:
--   1. CREATE TABLE IF NOT EXISTS — no-op in production, brings the
--      shape under version control for fresh environments.
--   2. Index (service_area_id, created_at DESC) matching the admin
--      history query pattern (fixes 482k sequential rows read).
--   3. RLS deny-all for clients (service role bypasses by design),
--      mirroring migration 76's push_retry_queue pattern.

CREATE TABLE IF NOT EXISTS surge_pricing (
    id              text             PRIMARY KEY,
    service_area_id text             NOT NULL,
    multiplier      double precision NOT NULL DEFAULT 1.0,
    demand_count    integer          NOT NULL DEFAULT 0,
    supply_count    integer          NOT NULL DEFAULT 0,
    ratio           double precision NOT NULL DEFAULT 0,
    source          text             NOT NULL DEFAULT 'auto',
    is_active       boolean          NOT NULL DEFAULT false,
    created_at      timestamptz      NOT NULL DEFAULT now(),
    updated_at      timestamptz      NOT NULL DEFAULT now()
);

-- Admin surge-history query shape: WHERE service_area_id = ? AND
-- created_at >= ? ORDER BY created_at DESC LIMIT 500.
-- CONCURRENTLY because the surge engine INSERTs every 2 min — a plain
-- CREATE INDEX would take a ShareLock and block those writes during the
-- build. The migration runner detects the keyword and runs in autocommit.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_surge_pricing_area_created
    ON surge_pricing (service_area_id, created_at DESC);

-- RLS: no client (authenticated or anon) should read or write this table.
-- The backend service role bypasses RLS by design — all access goes
-- through it.
ALTER TABLE surge_pricing ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS surge_pricing_service_only ON surge_pricing;

CREATE POLICY surge_pricing_service_only
    ON surge_pricing
    FOR ALL
    TO authenticated
    USING (false);
