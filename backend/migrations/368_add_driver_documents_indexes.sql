-- 368_add_driver_documents_indexes.sql
-- Add missing indexes on driver_documents for query performance:
--   1. Composite index on (driver_id, status) — covers the hot path used by
--      document listing, go-online checks, and admin approval queues that
--      filter by driver + status.
--   2. FK index on driver_id — Postgres does not auto-index FK columns;
--      without this, any UPDATE/DELETE on the parent `drivers` row triggers
--      a sequential scan of driver_documents.
--   3. FK index on requirement_id — same rationale for the
--      document_requirements FK added in migration 02.
--
-- All indexes use CONCURRENTLY to avoid table locks on production traffic.
-- IF NOT EXISTS for idempotency.
--
-- Rollback (manual):
--   DROP INDEX IF EXISTS idx_driver_documents_driver_id_status;
--   DROP INDEX IF EXISTS idx_driver_documents_driver_id;
--   DROP INDEX IF EXISTS idx_driver_documents_requirement_id;

-- 1. Composite index for driver + status lookups
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_driver_documents_driver_id_status
    ON public.driver_documents (driver_id, status);

-- 2. FK index on driver_id (parent cascade / join performance)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_driver_documents_driver_id
    ON public.driver_documents (driver_id);

-- 3. FK index on requirement_id (document_requirements FK)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_driver_documents_requirement_id
    ON public.driver_documents (requirement_id);
