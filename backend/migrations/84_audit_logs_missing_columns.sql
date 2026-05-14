-- 84_audit_logs_missing_columns.sql
-- Migration 06 created audit_logs first; migration 08's CREATE TABLE IF NOT
-- EXISTS was a no-op, so actor_role, resource, resource_id, and ip_address
-- were never added. 12+ admin routes write these columns — every insert
-- hits PGRST204 and trips the circuit breaker, causing cascading 503s.
--
-- Rollback:
--   ALTER TABLE audit_logs DROP COLUMN IF EXISTS actor_role;
--   ALTER TABLE audit_logs DROP COLUMN IF EXISTS resource;
--   ALTER TABLE audit_logs DROP COLUMN IF EXISTS resource_id;
--   ALTER TABLE audit_logs DROP COLUMN IF EXISTS ip_address;

ALTER TABLE audit_logs
    ADD COLUMN IF NOT EXISTS actor_role  TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS resource    TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS resource_id TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS ip_address  TEXT DEFAULT NULL;
