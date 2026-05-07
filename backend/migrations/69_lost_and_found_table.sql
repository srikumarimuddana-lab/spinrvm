-- 69_lost_and_found_table.sql
-- Creates the lost_and_found table for items left behind after a trip.
--
-- Rollback: DROP TABLE IF EXISTS lost_and_found CASCADE;
--
-- Forward-compatible: new table only; no changes to existing tables.
-- RLS: reporters see and insert their own items; status changes (found,
-- returned, unclaimed) are service-role-only (no UPDATE/DELETE policy for
-- authenticated/anon).

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

-- Indexes for the query patterns used in the admin dashboard and backend routes.
CREATE INDEX IF NOT EXISTS lost_and_found_reporter_id_idx ON lost_and_found (reporter_id);
CREATE INDEX IF NOT EXISTS lost_and_found_ride_id_idx     ON lost_and_found (ride_id);
CREATE INDEX IF NOT EXISTS lost_and_found_status_idx      ON lost_and_found (status);
CREATE INDEX IF NOT EXISTS lost_and_found_created_at_idx  ON lost_and_found (created_at DESC);

-- RLS: reporters can read and create their own reports.
-- No UPDATE/DELETE policy → those operations are denied to authenticated/anon
-- clients by default. Status transitions go through the service role only.
ALTER TABLE lost_and_found ENABLE ROW LEVEL SECURITY;

CREATE POLICY lost_and_found_reporter_select ON lost_and_found
    FOR SELECT USING (auth.uid()::text = reporter_id::text);

CREATE POLICY lost_and_found_insert ON lost_and_found
    FOR INSERT WITH CHECK (auth.uid()::text = reporter_id::text);

COMMENT ON TABLE lost_and_found IS
    'Items reported lost or found after a trip. '
    'RLS restricts non-admin reads/inserts to the reporter; '
    'status transitions (reported→found→returned/unclaimed) are service-role-only. '
    'Created in migration 69.';

NOTIFY pgrst, 'reload schema';
