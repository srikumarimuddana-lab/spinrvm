-- 120_ensure_emergency_contacts_and_gps_column.sql
--
-- Rollback:
--   DROP TABLE IF EXISTS emergency_contacts;
--   ALTER TABLE rides DROP COLUMN IF EXISTS gps_anonymized_at;
--
-- Problem 1: emergency_contacts table missing on Railway (PGRST205).
--   The table is defined in 08_complete_schema.sql, but due to string-sort
--   ordering (100_* sorts before 50_*, which sorts before 08_* on some runners)
--   combined with a fresh DB, the table may never have been created.
--   This migration creates it idempotently and applies RLS.
--
-- Problem 2: purge_pii_retention() crashes with
--   'column "gps_anonymized_at" does not exist'.
--   The column is added by 50_pii_retention_purge.sql, but migration 117
--   re-creates purge_pii_retention() referencing that column. On a fresh DB
--   with string-sorted migration application, 117_* runs before 50_*, so the
--   function compiles fine but fails at runtime because the column isn't there yet.
--   This migration adds the column idempotently to make the function safe to call
--   regardless of application order.

-- ── 1. emergency_contacts ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS emergency_contacts (
    id           TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id      TEXT        NOT NULL,
    name         TEXT        NOT NULL,
    phone        TEXT        NOT NULL,
    relationship TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_emergency_contacts_user_id ON emergency_contacts (user_id);

ALTER TABLE emergency_contacts ENABLE ROW LEVEL SECURITY;

-- Riders may only see and manage their own contacts.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'emergency_contacts'
          AND policyname = 'emergency_contacts_owner_select'
    ) THEN
        CREATE POLICY emergency_contacts_owner_select
            ON emergency_contacts FOR SELECT
            USING (auth.uid()::text = user_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'emergency_contacts'
          AND policyname = 'emergency_contacts_owner_insert'
    ) THEN
        CREATE POLICY emergency_contacts_owner_insert
            ON emergency_contacts FOR INSERT
            WITH CHECK (auth.uid()::text = user_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'emergency_contacts'
          AND policyname = 'emergency_contacts_owner_delete'
    ) THEN
        CREATE POLICY emergency_contacts_owner_delete
            ON emergency_contacts FOR DELETE
            USING (auth.uid()::text = user_id);
    END IF;
END
$$;

-- ── 2. rides.gps_anonymized_at ────────────────────────────────────────────────
ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS gps_anonymized_at TIMESTAMPTZ;

COMMENT ON COLUMN rides.gps_anonymized_at IS
    'Set by purge_pii_retention() once pickup/dropoff GPS + polylines are scrubbed (3y). NULL means full GPS still present. Append-only — never reset.';

CREATE INDEX IF NOT EXISTS idx_rides_gps_anonymized_at
    ON rides (created_at)
    WHERE gps_anonymized_at IS NULL;

-- Tell PostgREST to reload its schema cache so the new table is immediately
-- visible without a full restart.
NOTIFY pgrst, 'reload schema';
