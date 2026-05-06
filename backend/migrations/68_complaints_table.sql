-- 68_complaints_table.sql
-- Creates the complaints table for reporting ride-related issues
-- (harassment, safety, quality, vehicle, navigation, payment, other).
--
-- Rollback: DROP TABLE IF EXISTS complaints CASCADE;
--
-- Forward-compatible: new table only; no changes to existing tables.
-- RLS: reporters see and insert their own complaints; status changes are
-- service-role-only (no UPDATE/DELETE policy for authenticated/anon).

CREATE TABLE IF NOT EXISTS complaints (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ride_id         UUID        REFERENCES rides(id) ON DELETE SET NULL,
    reporter_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reported_id     UUID        REFERENCES users(id) ON DELETE SET NULL,
    complaint_type  TEXT        NOT NULL CHECK (complaint_type IN (
                                    'harassment', 'safety', 'quality', 'vehicle',
                                    'navigation', 'payment', 'other'
                                )),
    description     TEXT,
    status          TEXT        NOT NULL DEFAULT 'open'
                                    CHECK (status IN ('open', 'under_review', 'resolved', 'dismissed')),
    resolution_note TEXT,
    resolved_by     UUID        REFERENCES users(id) ON DELETE SET NULL,
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for the query patterns used in the admin dashboard and backend routes.
CREATE INDEX IF NOT EXISTS complaints_reporter_id_idx  ON complaints (reporter_id);
CREATE INDEX IF NOT EXISTS complaints_reported_id_idx  ON complaints (reported_id);
CREATE INDEX IF NOT EXISTS complaints_ride_id_idx      ON complaints (ride_id);
CREATE INDEX IF NOT EXISTS complaints_status_idx       ON complaints (status);
CREATE INDEX IF NOT EXISTS complaints_created_at_idx   ON complaints (created_at DESC);

-- RLS: reporters can read and create their own complaints.
-- No UPDATE/DELETE policy → those operations are denied to authenticated/anon
-- clients by default. Status changes go through the service role only.
ALTER TABLE complaints ENABLE ROW LEVEL SECURITY;

CREATE POLICY complaints_reporter_select ON complaints
    FOR SELECT USING (auth.uid()::text = reporter_id::text);

CREATE POLICY complaints_insert ON complaints
    FOR INSERT WITH CHECK (auth.uid()::text = reporter_id::text);

COMMENT ON TABLE complaints IS
    'Ride-related complaints submitted by riders or drivers. '
    'RLS restricts non-admin reads/inserts to the reporter; '
    'status transitions (open→under_review→resolved/dismissed) are service-role-only. '
    'Created in migration 68.';

NOTIFY pgrst, 'reload schema';
