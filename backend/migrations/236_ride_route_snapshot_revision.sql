-- 234_ride_route_snapshot_revision.sql
--
-- Stores a private immutable snapshot-object path beside the exact v2 route
-- revision it depicts. Detail and receipt readers sign this path only for an
-- authorized request; v2 route images are never placed in a public bucket.
--
-- Rollback plan:
--   Deploy readers that ignore snapshot_object_path first. After snapshot
--   publishing is disabled and existing signed URLs have expired, drop only
--   snapshot_object_path. Keep snapshot_revision because migration 235 owns it.

ALTER TABLE public.ride_routes
    ADD COLUMN IF NOT EXISTS snapshot_url text,
    ADD COLUMN IF NOT EXISTS snapshot_object_path text;

COMMENT ON COLUMN public.ride_routes.snapshot_url IS
    'Legacy snapshot URL only. New v2 snapshots persist a private object path and are signed at read time.';
COMMENT ON COLUMN public.ride_routes.snapshot_object_path IS
    'Private ride-route-snapshots object path for snapshot_revision; never expose this field to clients.';

-- ``storage.objects`` has RLS enabled. No anon/authenticated policy is added:
-- only the backend service role can upload, sign, and remove trip snapshots.
INSERT INTO storage.buckets (id, name, public)
VALUES ('ride-route-snapshots', 'ride-route-snapshots', false)
ON CONFLICT (id) DO UPDATE
    SET public = false;

NOTIFY pgrst, 'reload schema';
