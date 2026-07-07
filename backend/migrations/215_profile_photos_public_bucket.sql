-- 215_profile_photos_public_bucket.sql
--
-- Fix: rider/driver profile photos never render — not even after an app restart.
--
-- routes/users.py::upload_profile_image uploads to the `profile-photos` Storage
-- bucket and stores get_public_url(...) as users.profile_image. That URL only
-- resolves if the bucket is PUBLIC. The bucket was never provisioned in a
-- migration (same drift migration 202 fixed for admin media), so it was
-- private/absent: get_public_url returned a public-format URL that 403s, and the
-- apps rendered the avatar placeholder instead. (The base64 fallback isn't hit
-- because the upload itself succeeds against a private bucket — only the later
-- public read fails.) Making the bucket public is idempotent and retroactively
-- fixes already-uploaded photos, since their saved URLs start resolving.
--
-- Rollback (re-privatise; existing saved URLs would 403 again):
--   UPDATE storage.buckets SET public = false WHERE id = 'profile-photos';
--
-- Privacy: profile photos are shown to ride counterparties (riders see driver
-- photos and vice versa), so public read is intended. Object paths are
-- {user_id}/{uuid}.ext — the 128-bit UUID suffix is not practically guessable.
-- Runtime writes use the backend service-role key (bypasses RLS); the apps
-- never write to storage directly, and no anon/authenticated policy is added,
-- so API-level enumeration/.list() stays default-deny.
--
-- IMPORTANT — this bucket also holds DRIVER photos, including ones still
-- pending_review/rejected. Once the bucket is public, the raw object bytes are
-- fetchable by ANYONE WHO HAS THE URL, regardless of moderation state: the
-- "hidden from riders until approved" behaviour (rides.py::_rider_visible_photo,
-- admin/drivers.py::admin_review_driver_photo) is app-layer response filtering
-- (it controls whether the URL is DISCLOSED), NOT storage-level access control.
-- That matches the 202 precedent and is an accepted trade-off (URL secrecy), but
-- there is no storage.objects delete on profile_image replace / account
-- deletion, so a URL that leaked before deletion would now resolve — a
-- pre-existing PIPEDA right-to-delete gap this makes exercisable. Follow-up:
-- purge the storage object on profile_image change + account scrub.

INSERT INTO storage.buckets (
    id,
    name,
    public,
    file_size_limit,
    allowed_mime_types
)
VALUES (
    'profile-photos',
    'profile-photos',
    true,
    5242880,  -- 5 MB, matching the upload_profile_image size guard
    ARRAY['image/jpeg', 'image/png', 'image/webp', 'image/gif']::text[]
)
ON CONFLICT (id) DO UPDATE
SET
    name = EXCLUDED.name,
    public = EXCLUDED.public,
    file_size_limit = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;
