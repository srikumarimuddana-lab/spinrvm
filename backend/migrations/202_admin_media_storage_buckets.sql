-- 202_admin_media_storage_buckets.sql
--
-- Provision the public Supabase Storage buckets used by admin-managed media.
-- Migration 83 added the DB columns and noted that these buckets had to be
-- created out-of-band; production drift left /admin/vehicle-types/{id}/upload-
-- illustration returning "Bucket not found". Keeping bucket setup here makes
-- the upload path reproducible across environments.
--
-- Rollback:
--   DELETE FROM storage.objects
--    WHERE bucket_id IN ('vehicle-illustrations', 'audio-assets');
--   DELETE FROM storage.buckets
--    WHERE id IN ('vehicle-illustrations', 'audio-assets');
--
-- Both buckets store non-PII public assets. Runtime writes use the backend
-- service-role key; public read is required for mobile Image/audio playback.

INSERT INTO storage.buckets (
    id,
    name,
    public,
    file_size_limit,
    allowed_mime_types
)
VALUES
    (
        'vehicle-illustrations',
        'vehicle-illustrations',
        true,
        512000,
        ARRAY['image/png', 'image/jpeg', 'image/webp']::text[]
    ),
    (
        'audio-assets',
        'audio-assets',
        true,
        512000,
        ARRAY['audio/mpeg', 'audio/wav', 'audio/x-wav']::text[]
    )
ON CONFLICT (id) DO UPDATE
SET
    name = EXCLUDED.name,
    public = EXCLUDED.public,
    file_size_limit = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;
