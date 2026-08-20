-- 354_kyb_documents_bucket_private.sql
--
-- Provisions the corporate KYB (Know Your Business) document bucket's
-- metadata in source control. Closes ACTION_ITEMS.md's B12 gap: unlike
-- every other recently-added bucket (202, 236, 339, 340), `kyb-documents`
-- was created by hand in the Supabase dashboard per
-- backend/docs/STORAGE_BUCKETS.md ("create it once via the dashboard...
-- then the app writes to it at runtime") and its intended posture (private,
-- size limit, allowed MIME types) was never expressed anywhere in this repo
-- — so nobody could confirm from source control that the live bucket
-- actually matches what the application code assumes, and there was no
-- guard against someone flipping it public later.
--
-- This is metadata-only. It does not create the bucket for the first time
-- (it already exists and has been written to by the running application
-- since before this repo tracked storage buckets in migrations) and it
-- does not touch storage.objects, so no already-uploaded KYB document is
-- read, moved, or affected by this migration.
--
-- Values match what the application already enforces before a byte ever
-- reaches storage:
--   - allowed_mime_types: _ALLOWED_CONTENT_TYPES in
--     backend/routes/corporate_company_kyb.py and _ALLOWED_KYB_CONTENT in
--     backend/routes/corporate_accounts.py (both
--     {"application/pdf", "image/png", "image/jpeg"})
--   - file_size_limit: MAX_FILE_SIZE in backend/documents.py (10 MB),
--     the same cap migrations 339 and 340 used for their own buckets
--
-- Access control note: the backend's Supabase client is constructed with
-- SUPABASE_SERVICE_ROLE_KEY (backend/supabase_client.py), which bypasses
-- Postgres RLS entirely, for every KYB storage call
-- (backend/repositories/corporate_repo.py's kyb_object_exists() and
-- create_kyb_upload_url(), backend/routes/corporate_accounts.py's
-- admin_view_kyb_document()). The real access-control boundary for KYB
-- documents is therefore the FastAPI route guards (require_company_admin
-- for the company portal, get_current_admin for staff), not Storage-layer
-- RLS. Accordingly this migration deliberately grants NO policy to
-- authenticated/anon on this bucket — the same "no RLS grant" posture
-- migrations 339 and 340 already document for their own buckets. A client
-- must never be able to reach a KYB document except through the backend.
--
-- Rollback:
--   This bucket is NOT net-new — it may already hold real, live-uploaded
--   corporate KYB verification documents (incorporation records, business
--   licenses) uploaded by companies before this repo tracked the bucket at
--   all. Unlike migration 340's safety-evidence bucket (net-new, so a full
--   DROP/DELETE was safe), rolling back this migration must be config-only:
--
--     UPDATE storage.buckets
--     SET public = false  -- restore whatever the pre-migration value was
--                          -- if it differed; false is the only value this
--                          -- repo has ever asserted for this bucket
--     WHERE id = 'kyb-documents';
--     NOTIFY pgrst, 'reload schema';
--
--   Do NOT run DROP TABLE, DELETE FROM storage.objects WHERE bucket_id =
--   'kyb-documents', or DELETE FROM storage.buckets WHERE id =
--   'kyb-documents' as a rollback for this migration — any of those
--   destroys live companies' KYB verification documents, which this
--   migration never uploaded and has no authority to remove. If this
--   migration's file_size_limit/allowed_mime_types values ever need to be
--   reverted, only the UPDATE above is required; the bucket and its
--   objects must be left alone.
--
-- Forward-compatible: this file only asserts storage.buckets metadata for
-- an already-existing bucket id. No application code path changes, no
-- table is created or altered, so a backend still running the previous
-- build is unaffected.

INSERT INTO storage.buckets (
    id,
    name,
    public,
    file_size_limit,
    allowed_mime_types
)
VALUES (
    'kyb-documents',
    'kyb-documents',
    false,
    10485760,
    ARRAY['application/pdf', 'image/png', 'image/jpeg']::text[]
)
ON CONFLICT (id) DO UPDATE
SET
    name               = EXCLUDED.name,
    public             = EXCLUDED.public,
    file_size_limit    = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;

NOTIFY pgrst, 'reload schema';
