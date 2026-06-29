-- 200_data_export_objects.sql
-- Tracks data-export ZIP objects uploaded to the private `data-exports` Storage
-- bucket, so a background loop can delete them once the 7-day signed-link TTL
-- expires. Without this, every driver's full personal-data ZIP accumulates in
-- Storage indefinitely (PIPEDA data-minimization violation + breach blast area).
--
-- Rollback:
--   DROP TABLE IF EXISTS data_export_objects;

CREATE TABLE IF NOT EXISTS data_export_objects (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    storage_path  TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ
);

-- Purge-loop query pattern: undeleted rows past expiry. Partial index keeps it
-- tiny (only ever indexes the rows still awaiting deletion).
CREATE INDEX IF NOT EXISTS idx_data_export_objects_purge
    ON data_export_objects (expires_at)
    WHERE deleted_at IS NULL;

-- This table references user data, so it carries RLS. Only the backend service
-- role ever reads or writes it (the purge loop and the upload path) — there is
-- no client-facing query — so RLS is enabled with NO public policies: the anon
-- and authenticated keys get nothing, while the service role bypasses RLS by
-- design. (Per CLAUDE.md: the frontend anon key must never touch user data.)
ALTER TABLE data_export_objects ENABLE ROW LEVEL SECURITY;
