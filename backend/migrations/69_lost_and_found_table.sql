-- 69_lost_and_found_table.sql
-- Creates the lost_and_found table for items left behind after a trip.
--
-- Rollback: DROP TABLE IF EXISTS lost_and_found CASCADE;
--
-- Forward-compatible: new table only; no changes to existing tables.
-- RLS: reporters see and insert their own items; status changes are
-- service-role-only (no UPDATE/DELETE policy for authenticated/anon).
--
-- Note: on environments where the legacy table from sql/04_rides_admin_overhaul.sql
-- already exists, the CREATE TABLE below is a no-op and reporter_id may be present
-- as rider_id instead. The reporter_id-dependent index and policies are therefore
-- wrapped in conditional DO blocks; migration 69a adds the missing columns and
-- recreates those objects unconditionally once the column exists.

CREATE TABLE IF NOT EXISTS lost_and_found (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ride_id          UUID        REFERENCES rides(id) ON DELETE SET NULL,
    reporter_id      UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_description TEXT        NOT NULL,
    item_category    TEXT        CHECK (item_category IN (
                                     'electronics', 'clothing', 'bag', 'document',
                                     'keys', 'other'
                                 )),
    status           TEXT        NOT NULL DEFAULT 'reported'
                                     CHECK (status IN ('reported', 'found', 'returned', 'unclaimed')),
    contact_method   TEXT,
    driver_note      TEXT,
    resolved_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS lost_and_found_ride_id_idx     ON lost_and_found (ride_id);
CREATE INDEX IF NOT EXISTS lost_and_found_status_idx      ON lost_and_found (status);
CREATE INDEX IF NOT EXISTS lost_and_found_created_at_idx  ON lost_and_found (created_at DESC);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'lost_and_found' AND column_name = 'reporter_id'
    ) THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS lost_and_found_reporter_id_idx ON lost_and_found (reporter_id)';
    END IF;
END $$;

ALTER TABLE lost_and_found ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'lost_and_found' AND column_name = 'reporter_id'
    ) THEN
        EXECUTE 'DROP POLICY IF EXISTS lost_and_found_reporter_select ON lost_and_found';
        EXECUTE $p$CREATE POLICY lost_and_found_reporter_select ON lost_and_found
            FOR SELECT USING (auth.uid()::text = reporter_id::text)$p$;
        EXECUTE 'DROP POLICY IF EXISTS lost_and_found_insert ON lost_and_found';
        EXECUTE $p$CREATE POLICY lost_and_found_insert ON lost_and_found
            FOR INSERT WITH CHECK (auth.uid()::text = reporter_id::text)$p$;
    END IF;
END $$;

COMMENT ON TABLE lost_and_found IS
    'Items reported lost or found after a trip. '
    'RLS restricts non-admin reads/inserts to the reporter; '
    'status transitions are service-role-only. '
    'Created in migration 69; columns added in migration 69a.';

NOTIFY pgrst, 'reload schema';
