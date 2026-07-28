-- 262_data_transfer_export_jobs.sql
-- Tracks admin-initiated Data Transfer export batches (Admin > Data Transfer
-- > Export) so the resulting ZIP in the private `data-transfer-exports`
-- Storage bucket can be purged after its signed-link TTL, and so the Jobs
-- tab can show export history. Distinct from `data_export_objects`
-- (migration 200), which tracks single-user PIPEDA self-export ZIPs — this
-- table tracks admin-to-admin, multi-entity, full-fidelity bundles instead.
--
-- Rollback: NOT a plain git revert — the export route and purge loop both
-- reference this table once wired in Phase 1.2-1.3. Sequence:
--   1. Remove the insert_one("data_transfer_export_jobs", ...) call in
--      routes/admin/data_transfer_export.py and the purge sweep added to
--      utils/data_export_purge.py.
--   2. Deploy that build to all replicas.
--   3. Then: DROP TABLE IF EXISTS data_transfer_export_jobs;

BEGIN;

CREATE TABLE IF NOT EXISTS data_transfer_export_jobs (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- public.users.id is TEXT (see migration 29) — admins are users too.
    requested_by_admin_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    entity_type           TEXT NOT NULL,       -- 'driver' | 'rider' | 'mixed'
    entity_ids            JSONB NOT NULL,      -- array of users.id included in this batch
    doc_type_filter       JSONB,               -- array of driver_documents.document_type, or null = all
    format                TEXT NOT NULL DEFAULT 'zip',  -- 'zip' | 'csv' | 'excel' | 'json'
    status                TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'completed' | 'failed'
    storage_path          TEXT,
    error_message         TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at          TIMESTAMPTZ,
    expires_at            TIMESTAMPTZ,
    deleted_at            TIMESTAMPTZ
);

-- Purge-loop query pattern mirrors idx_data_export_objects_purge (migration
-- 200), tightened with `expires_at IS NOT NULL` because unlike 200's
-- expires_at (NOT NULL), this table's expires_at is nullable (a job that
-- failed before upload never gets an expiry) and must not match the purge
-- sweep.
CREATE INDEX IF NOT EXISTS idx_data_transfer_export_jobs_purge
    ON data_transfer_export_jobs (expires_at)
    WHERE deleted_at IS NULL AND expires_at IS NOT NULL;

-- Jobs tab query pattern: most recent batches first, by requester.
CREATE INDEX IF NOT EXISTS idx_data_transfer_export_jobs_admin_created
    ON data_transfer_export_jobs (requested_by_admin_id, created_at DESC);

COMMENT ON TABLE data_transfer_export_jobs IS
    'Tracks admin-initiated Data Transfer export batches (full-fidelity, '
    'multi-entity, portal-to-portal) so utils/data_export_purge.py can sweep '
    'expired ZIPs from the data-transfer-exports Storage bucket and the Jobs '
    'tab can render history. Backend/service-role only.';

-- This table references user data and is backend/service-role-only — no
-- client (anon/authenticated) query exists or should ever be added, since
-- entity_ids/doc_type_filter describe which users' data left the system.
-- Explicit per-action service_role policies (not FOR ALL) per the RLS
-- convention in backend/migrations/CLAUDE.md; anon/authenticated get no
-- policy at all, so they're denied by RLS's default-deny.
ALTER TABLE data_transfer_export_jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY data_transfer_export_jobs_service_select
    ON data_transfer_export_jobs FOR SELECT TO service_role USING (true);
CREATE POLICY data_transfer_export_jobs_service_insert
    ON data_transfer_export_jobs FOR INSERT TO service_role WITH CHECK (true);
CREATE POLICY data_transfer_export_jobs_service_update
    ON data_transfer_export_jobs FOR UPDATE TO service_role USING (true) WITH CHECK (true);
CREATE POLICY data_transfer_export_jobs_service_delete
    ON data_transfer_export_jobs FOR DELETE TO service_role USING (true);

COMMIT;
