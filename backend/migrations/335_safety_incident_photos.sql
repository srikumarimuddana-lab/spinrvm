-- 335_safety_incident_photos.sql
--
-- Storage for evidence photos attached to a safety incident.
--
-- Why this exists: driver-app/app/report-safety.tsx has been POSTing photos to
-- /api/v1/safety/report/{id}/photo since it shipped, but that endpoint was
-- never implemented — routes/safety.py only ever exposed POST /report. The
-- client wrapped each upload in `catch {}` commented "Photo upload failure is
-- non-fatal", so every safety evidence photo a driver has ever attached was
-- silently discarded. This migration + the matching endpoint close that.
--
-- Photos are a separate table rather than columns on safety_incidents because
-- a report carries up to 4 of them (the client's own selection limit) and the
-- incident row is read on every admin queue page — keeping blobs' metadata out
-- of that hot row avoids widening it for the common case.
--
-- Rollback:
--   DROP TABLE IF EXISTS public.safety_incident_photos CASCADE;
--   DELETE FROM storage.objects WHERE bucket_id = 'safety-evidence';
--   DELETE FROM storage.buckets WHERE id = 'safety-evidence';
--   NOTIFY pgrst, 'reload schema';
--
--   NOTE: dropping the bucket destroys evidence attached to open incidents.
--   If any incident is still under review, drop the table only and leave the
--   bucket in place.
--
-- Forward-compatible: new table + new bucket only; safety_incidents is
-- untouched, so a backend still running the previous build is unaffected.

CREATE TABLE IF NOT EXISTS public.safety_incident_photos (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- safety_incidents.id is TEXT (see migration 94), so this matches it.
    -- CASCADE: an incident that is hard-deleted takes its evidence with it.
    incident_id         TEXT        NOT NULL
                                        REFERENCES public.safety_incidents(id) ON DELETE CASCADE,

    -- Object key in the private safety-evidence bucket. The signed URL is
    -- minted per read and never stored — it expires within the hour.
    storage_key         TEXT        NOT NULL,
    content_type        TEXT,
    size_bytes          INTEGER,

    -- Who attached it. Normally the reporter; kept explicitly so an
    -- admin-attached photo is distinguishable during review.
    uploaded_by_user_id TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Sole read pattern: "every photo for this incident, in upload order",
-- issued by the admin incident-detail view.
CREATE INDEX IF NOT EXISTS idx_safety_incident_photos_incident
    ON public.safety_incident_photos (incident_id, created_at);

ALTER TABLE public.safety_incident_photos ENABLE ROW LEVEL SECURITY;

-- Service role (backend) reads and writes everything. Mirrors the policy
-- migration 94 put on safety_incidents itself.
DROP POLICY IF EXISTS "Service role bypass safety_incident_photos"
    ON public.safety_incident_photos;
CREATE POLICY "Service role bypass safety_incident_photos"
    ON public.safety_incident_photos FOR ALL TO service_role
    USING (true)
    WITH CHECK (true);

-- Deliberately NO policy for authenticated/anon. Evidence is reachable only
-- through the backend, which checks reporter identity on write and admin
-- module grant on read. Safety evidence can name or depict a third party, so
-- a client must never be able to enumerate this table directly.

COMMENT ON TABLE public.safety_incident_photos IS
    'Evidence photos for safety_incidents. Objects live in the private '
    'safety-evidence bucket; rows store the key only, never a signed URL.';

-- Private bucket. 10 MB matches documents.py MAX_FILE_SIZE, the cap the
-- shared read_upload_capped() helper enforces on the way in. No HEIC/HEIF:
-- _resolve_upload_type() rejects those with an actionable 400 before they
-- reach storage, so listing them would misstate what the API accepts.
INSERT INTO storage.buckets (
    id,
    name,
    public,
    file_size_limit,
    allowed_mime_types
)
VALUES (
    'safety-evidence',
    'safety-evidence',
    false,
    10485760,
    ARRAY['image/jpeg', 'image/png', 'image/webp']::text[]
)
ON CONFLICT (id) DO UPDATE
SET
    name               = EXCLUDED.name,
    public             = EXCLUDED.public,
    file_size_limit    = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;

NOTIFY pgrst, 'reload schema';
