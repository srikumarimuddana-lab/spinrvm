-- 339_lost_and_found_message_images.sql
--
-- Lets Lost & Found chat participants attach a photo of the item. Drivers
-- reported being unable to send a picture of what they found, which is the
-- single most useful piece of evidence for a rider identifying their property.
--
-- Two parts:
--   1. lost_and_found_messages gains image_key / image_mime.
--   2. A PRIVATE 'lost-and-found' storage bucket to hold the objects.
--
-- We store the storage KEY, never a URL: signed URLs expire (1 h), so
-- persisting one would hand out dead links a day later. routes/lost_and_found.py
-- mints a fresh signed URL per read instead.
--
-- The bucket is private (public = false) because these photos are user content
-- attached to an identifiable ride — a public bucket would make every item
-- photo world-readable by URL. PIPEDA: images live in the same Canadian-region
-- Supabase project as the rest of the data; purge is covered by the case's own
-- retention (see "What was NOT verified" in the change log — a dedicated
-- storage purge step is not yet wired).
--
-- Rollback:
--   ALTER TABLE lost_and_found_messages DROP COLUMN IF EXISTS image_key;
--   ALTER TABLE lost_and_found_messages DROP COLUMN IF EXISTS image_mime;
--   DELETE FROM storage.objects WHERE bucket_id = 'lost-and-found';
--   DELETE FROM storage.buckets WHERE id = 'lost-and-found';
--   NOTIFY pgrst, 'reload schema';
--
-- Forward-compatible: both columns are nullable additions, so a backend
-- instance still running the previous build keeps inserting text-only messages
-- without error while this is rolling out.

-- 1. Attachment columns -----------------------------------------------------
ALTER TABLE lost_and_found_messages
    ADD COLUMN IF NOT EXISTS image_key  TEXT,
    ADD COLUMN IF NOT EXISTS image_mime TEXT;

COMMENT ON COLUMN lost_and_found_messages.image_key IS
    'Supabase Storage object key in the private lost-and-found bucket. '
    'NULL for text-only messages. A signed URL is generated per read; '
    'never store the signed URL here — it expires.';

-- An image-only message carries message = '' (the column is NOT NULL). Guard
-- against a row that is neither: no text AND no image would render as an empty
-- bubble with nothing in it.
ALTER TABLE lost_and_found_messages
    DROP CONSTRAINT IF EXISTS lfm_text_or_image_present;

ALTER TABLE lost_and_found_messages
    ADD CONSTRAINT lfm_text_or_image_present
        CHECK (length(trim(message)) > 0 OR image_key IS NOT NULL);

-- 2. Private storage bucket -------------------------------------------------
-- 10 MB matches documents.py's MAX_FILE_SIZE, which is the cap the shared
-- read_upload_capped() helper enforces on the way in.
INSERT INTO storage.buckets (
    id,
    name,
    public,
    file_size_limit,
    allowed_mime_types
)
VALUES (
    'lost-and-found',
    'lost-and-found',
    false,
    10485760,
    -- Deliberately no HEIC/HEIF: _resolve_upload_type() in documents.py
    -- rejects those with an actionable 400 before they ever reach storage,
    -- so listing them here would only misstate what the API accepts.
    ARRAY['image/jpeg', 'image/png', 'image/webp']::text[]
)
ON CONFLICT (id) DO UPDATE
SET
    name               = EXCLUDED.name,
    public             = EXCLUDED.public,
    file_size_limit    = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;

-- No storage RLS policies are added: reads and writes go through the backend
-- service-role key only (it signs URLs for participants after the same
-- _require_participant() check that gates the rest of the case). The mobile
-- anon key must never touch this bucket directly.

NOTIFY pgrst, 'reload schema';
