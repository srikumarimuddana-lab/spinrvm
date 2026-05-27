-- 69a_lost_and_found_repair_schema.sql
--
-- Repairs the lost_and_found table on environments where the legacy schema
-- from sql/04_rides_admin_overhaul.sql existed before migration 69 was written.
-- On those environments migration 69's CREATE TABLE IF NOT EXISTS was a no-op,
-- leaving the table with the legacy column set (rider_id, created_by NOT NULL,
-- no contact_method / item_category / service_area_id, no status CHECK).
--
-- This file has no CONCURRENTLY statements, so the runner executes the whole
-- file in a single transaction.  DO blocks with internal semicolons work fine
-- in that path.  The CONCURRENTLY indexes are in migration 69b.
--
-- Rollback:
--   ALTER TABLE lost_and_found RENAME COLUMN reporter_id TO rider_id;
--   ALTER TABLE lost_and_found ALTER COLUMN created_by SET NOT NULL;
--   ALTER TABLE lost_and_found DROP COLUMN IF EXISTS contact_method;
--   ALTER TABLE lost_and_found DROP COLUMN IF EXISTS item_category;
--   ALTER TABLE lost_and_found DROP COLUMN IF EXISTS driver_note;
--   ALTER TABLE lost_and_found DROP COLUMN IF EXISTS service_area_id;
--   ALTER TABLE lost_and_found DROP CONSTRAINT IF EXISTS lost_and_found_status_check;
--   (Re-add the old 4-value check if desired.)

CREATE TABLE IF NOT EXISTS lost_and_found (
    id               TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::text,
    ride_id          TEXT        REFERENCES rides(id) ON DELETE SET NULL,
    reporter_id      TEXT        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    driver_id        TEXT,
    item_description TEXT        NOT NULL,
    item_category    TEXT        CHECK (item_category IN ('electronics', 'clothing', 'bag', 'document', 'keys', 'other')),
    status           TEXT        NOT NULL DEFAULT 'reported',
    contact_method   TEXT,
    admin_notes      TEXT,
    driver_note      TEXT,
    notified_at      TIMESTAMPTZ,
    resolved_at      TIMESTAMPTZ,
    created_by       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'lost_and_found' AND column_name = 'rider_id'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'lost_and_found' AND column_name = 'reporter_id'
    ) THEN
        ALTER TABLE lost_and_found RENAME COLUMN rider_id TO reporter_id;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'lost_and_found' AND column_name = 'created_by'
        AND is_nullable = 'NO'
    ) THEN
        ALTER TABLE lost_and_found ALTER COLUMN created_by DROP NOT NULL;
    END IF;
END $$;

ALTER TABLE lost_and_found
    ADD COLUMN IF NOT EXISTS reporter_id     TEXT,
    ADD COLUMN IF NOT EXISTS contact_method  TEXT,
    ADD COLUMN IF NOT EXISTS item_category   TEXT,
    ADD COLUMN IF NOT EXISTS driver_note     TEXT,
    ADD COLUMN IF NOT EXISTS service_area_id TEXT REFERENCES service_areas(id) ON DELETE SET NULL;

ALTER TABLE lost_and_found DROP CONSTRAINT IF EXISTS lost_and_found_status_check;

ALTER TABLE lost_and_found
    ADD CONSTRAINT lost_and_found_status_check
        CHECK (status IN ('reported', 'driver_notified', 'found', 'returned', 'unclaimed', 'resolved', 'unresolved'));

ALTER TABLE lost_and_found ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lost_and_found_reporter_select ON lost_and_found;

CREATE POLICY lost_and_found_reporter_select ON lost_and_found
    FOR SELECT USING (auth.uid()::text = reporter_id::text);

DROP POLICY IF EXISTS lost_and_found_insert ON lost_and_found;

CREATE POLICY lost_and_found_insert ON lost_and_found
    FOR INSERT WITH CHECK (auth.uid()::text = reporter_id::text);

CREATE INDEX IF NOT EXISTS lost_and_found_reporter_id_idx ON lost_and_found (reporter_id);
CREATE INDEX IF NOT EXISTS lost_and_found_ride_id_idx     ON lost_and_found (ride_id);
CREATE INDEX IF NOT EXISTS lost_and_found_status_idx      ON lost_and_found (status);
CREATE INDEX IF NOT EXISTS lost_and_found_created_at_idx  ON lost_and_found (created_at DESC);

NOTIFY pgrst, 'reload schema';
