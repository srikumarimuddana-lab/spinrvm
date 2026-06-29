-- 200_data_export_objects.sql
-- Tracks data-export ZIP objects uploaded to the private `data-exports` Storage
-- bucket, so a background loop can delete them once the 7-day signed-link TTL
-- expires. Without this, every driver's full personal-data ZIP accumulates in
-- Storage indefinitely (PIPEDA data-minimization violation + breach blast area).
--
-- Coordinated rollback (NOT a plain git revert — two code paths reference this
-- table, so dropping it without first removing them throws 42P01 on every DSAR
-- export and every purge tick):
--   1. Disable data_export_purge_loop in core/lifespan.py and the
--      insert_one("data_export_objects", ...) block in routes/drivers.py
--      (_upload_export_zip).
--   2. Deploy that build to all replicas.
--   3. Then: DROP TABLE IF EXISTS data_export_objects;

BEGIN;

CREATE TABLE IF NOT EXISTS data_export_objects (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- public.users.id is TEXT (uuid-formatted strings stored as TEXT — see
    -- migration 29), so this FK must be TEXT or Postgres rejects it with 42804.
    -- ON DELETE SET NULL (not CASCADE): if the user row is ever hard-deleted the
    -- tracking row must survive so the purge loop can still delete the Storage
    -- object at its TTL (the loop keys on storage_path + expires_at, not
    -- user_id). Cascading the row away would orphan the ZIP in Storage forever.
    user_id       TEXT REFERENCES users(id) ON DELETE SET NULL,
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

COMMENT ON TABLE data_export_objects IS
    'Tracks DSAR data-export ZIPs in the private data-exports Storage bucket so '
    'utils/data_export_purge.py can delete them after the 7-day signed-link TTL '
    '(PIPEDA data minimization). Backend/service-role only.';

-- This table references user data, so it carries RLS. Only the backend service
-- role ever reads or writes it (the purge loop and the upload path) — there is
-- no client-facing query — so RLS is enabled with NO public policies: the anon
-- and authenticated keys get nothing, while the service role bypasses RLS by
-- design. (Per CLAUDE.md: the frontend anon key must never touch user data.)
ALTER TABLE data_export_objects ENABLE ROW LEVEL SECURITY;

COMMIT;
