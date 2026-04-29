-- 57_audit_logs_schema_standardization.sql
-- A-P3-12: Standardize audit_logs schema so every admin action written in
-- P1/P2 renders uniformly in the admin dashboard audit-logs page.
--
-- Rollback plan (columns only, no data loss):
--   ALTER TABLE audit_logs
--       DROP COLUMN IF EXISTS entity_type,
--       DROP COLUMN IF EXISTS entity_id,
--       DROP COLUMN IF EXISTS old_value,
--       DROP COLUMN IF EXISTS new_value,
--       DROP COLUMN IF EXISTS reason,
--       DROP COLUMN IF EXISTS amount;
--   DROP INDEX IF EXISTS idx_audit_logs_entity;
--   DROP INDEX IF EXISTS idx_audit_logs_actor_created;
--
-- Forward-compatible: all new columns are nullable; existing rows (which use
-- the legacy resource/resource_id/details columns) remain valid and readable.

-- Add standardized columns alongside legacy ones (no DROP here — append-only).
ALTER TABLE audit_logs
    ADD COLUMN IF NOT EXISTS entity_type TEXT        DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS entity_id   TEXT        DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS old_value   JSONB       DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS new_value   JSONB       DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS reason      TEXT        DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS amount      NUMERIC(12,2) DEFAULT NULL;

-- Backfill entity_type/entity_id from legacy resource/resource_id columns
-- for rows that pre-date this migration (e.g. ride_declined entries).
UPDATE audit_logs
SET
    entity_type = resource,
    entity_id   = resource_id
WHERE entity_type IS NULL
  AND resource    IS NOT NULL;

-- Composite indexes for the two query patterns on the audit-logs dashboard:
--   1. "Show all events for this entity"
CREATE INDEX IF NOT EXISTS idx_audit_logs_entity
    ON audit_logs (entity_type, entity_id, created_at DESC)
    WHERE entity_type IS NOT NULL;

--   2. "Show all actions by this actor, newest first"
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_created
    ON audit_logs (actor_id, created_at DESC)
    WHERE actor_id IS NOT NULL;

-- Tamper-evidence trigger (B-P2-4 companion): prevent UPDATE/DELETE on rows.
-- Uses a DO block so the migration is idempotent if the trigger already exists.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'audit_logs_no_mutate'
          AND tgrelid = 'audit_logs'::regclass
    ) THEN
        CREATE OR REPLACE FUNCTION _audit_logs_immutable()
        RETURNS trigger LANGUAGE plpgsql AS
        $fn$
        BEGIN
            RAISE EXCEPTION 'audit_logs rows are append-only and cannot be modified or deleted';
        END;
        $fn$;

        CREATE TRIGGER audit_logs_no_mutate
            BEFORE UPDATE OR DELETE ON audit_logs
            FOR EACH ROW EXECUTE FUNCTION _audit_logs_immutable();
    END IF;
END;
$$;

COMMENT ON TABLE audit_logs IS
    'Append-only audit trail for all admin actions. Rows are tamper-evident '
    '(UPDATE/DELETE blocked by trigger). Schema standardized in migration 57.';
