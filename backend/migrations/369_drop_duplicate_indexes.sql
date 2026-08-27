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
-- ACTION REQUIRED (DevOps-Omar):
--   1. Run the above queries on STAGING first
--   2. Replace the placeholder index names below with the actual duplicate index names
--   3. Confirm each index has 0 or near-zero idx_scan count
--   4. Confirm a covering index exists for each one being dropped
--   5. Run on staging, verify app behavior, then apply to production
--
-- ROLLBACK: Re-create the indexes if needed. Column definitions are documented
-- next to each DROP statement.

BEGIN;

-- ============================================================================
-- Duplicate Index #1
-- Table: <TABLE_NAME>
-- Columns: <COLUMN(S)>
-- Covered by: <NAME_OF_COVERING_INDEX>
-- idx_scan count (verify): ___
-- ============================================================================
DROP INDEX IF EXISTS <DUPLICATE_INDEX_NAME_1>;

-- ============================================================================
-- Duplicate Index #2
-- Table: <TABLE_NAME>
-- Columns: <COLUMN(S)>
-- Covered by: <NAME_OF_COVERING_INDEX>
-- idx_scan count (verify): ___
-- ============================================================================
DROP INDEX IF EXISTS <DUPLICATE_INDEX_NAME_2>;

-- ============================================================================
-- Duplicate Index #3
-- Table: <TABLE_NAME>
-- Columns: <COLUMN(S)>
-- Covered by: <NAME_OF_COVERING_INDEX>
-- idx_scan count (verify): ___
-- ============================================================================
DROP INDEX IF EXISTS <DUPLICATE_INDEX_NAME_3>;

COMMIT;
