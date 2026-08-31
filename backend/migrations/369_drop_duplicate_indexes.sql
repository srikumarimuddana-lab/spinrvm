-- Migration 369: Drop duplicate/redundant indexes
-- Part of: DB Query Optimization Audit (PR #4579, Issue #4584)
-- Priority: P0 #2
-- Author: SpinR DB Optimization Sprint
-- Date: 2026-08-26
--
-- DESCRIPTION:
--   This migration removes 3 duplicate/redundant indexes identified during
--   the DB query optimization audit. Duplicate indexes waste disk space,
--   slow down writes (every INSERT/UPDATE/DELETE must maintain each index),
--   and provide zero query benefit since the covering index already exists.
--
-- IMPORTANT: Before running, verify these indexes are truly redundant using:
--   SELECT indexrelname, idx_scan FROM pg_stat_user_indexes
--   WHERE schemaname = 'public' ORDER BY idx_scan;
--
-- Also verify duplicates with this query (finds indexes on same table+columns):
--   SELECT
--     a.indexrelid::regclass AS duplicate_index,
--     b.indexrelid::regclass AS covering_index,
--     a.indrelid::regclass AS table_name,
--     pg_size_pretty(pg_relation_size(a.indexrelid)) AS wasted_space
--   FROM pg_index a
--   JOIN pg_index b ON a.indrelid = b.indrelid
--     AND a.indexrelid != b.indexrelid
--     AND a.indkey::text = b.indkey::text
--   WHERE a.indrelid::regclass::text NOT LIKE 'pg_%';
--
-- RESOLVED 2026-08-27: the placeholders below were never filled in, which left
-- literal `<DUPLICATE_INDEX_NAME_1>` text as executable SQL. That is a syntax
-- error, and because backend/scripts/run_migrations.py applies files in order
-- and hard-stops on the first failure, it blocked EVERY pending migration
-- behind it (production `schema_migrations` is applied through 362, so 363-369
-- had never run and nothing had drifted).
--
-- The three indexes are named below, verified against pg_indexes/advisors:
-- each is byte-identical to a surviving twin, so no access path is lost.
-- They are already absent from production (dropped out-of-band before this
-- migration ever ran) and no migration creates them, so these statements are
-- idempotent no-ops there. They are kept, rather than emptied, because any
-- environment still carrying the legacy indexes must still have them removed.
--
-- Deliberately NOT included: the other duplicate pairs that a fresh sweep
-- turned up (admin_staff, corporate_accounts, corporate_wallets, drivers,
-- loyalty_*, promo_applications, promotions, users, wallets). Almost all of
-- those pair a plain index with a UNIQUE CONSTRAINT's index, which DROP INDEX
-- cannot remove (it needs ALTER TABLE ... DROP CONSTRAINT) — a separate,
-- riskier change that needs its own review.
--
-- Do NOT add idx_rides_completed_driver here: migrations 302/303/370 depend on it.
--
-- ROLLBACK: Re-create the indexes if needed. Column definitions are documented
-- next to each DROP statement.

BEGIN;

-- ============================================================================
-- Duplicate Index #1
-- Table: surge_pricing
-- Columns: (service_area_id, created_at DESC)
-- Covered by: idx_surge_pricing_area_created
-- idx_scan count (verify): index absent in production as of 2026-08-27
-- ============================================================================
DROP INDEX IF EXISTS idx_surge_pricing_area;

-- ============================================================================
-- Duplicate Index #2
-- Table: driver_location_history
-- Columns: (driver_id, timestamp)
-- Covered by: idx_driver_location_history_driver_id_timestamp
-- idx_scan count (verify): index absent in production as of 2026-08-27
-- ============================================================================
DROP INDEX IF EXISTS idx_dlh_driver;

-- ============================================================================
-- Duplicate Index #3
-- Table: driver_location_history
-- Columns: (ride_id, timestamp)
-- Covered by: idx_driver_location_history_ride_id_timestamp
-- idx_scan count (verify): index absent in production as of 2026-08-27
-- ============================================================================
DROP INDEX IF EXISTS idx_dlh_ride;

COMMIT;
