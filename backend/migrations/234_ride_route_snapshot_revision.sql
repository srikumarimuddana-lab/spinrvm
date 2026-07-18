-- 234_ride_route_snapshot_revision.sql
--
-- Stores an immutable public snapshot URL beside the exact v2 route revision
-- it depicts. The detail API exposes it only when the revisions agree, so a
-- late GPS upload can never leave an older, misleading route image attached.
--
-- Rollback plan:
--   Deploy readers that ignore snapshot_url first. After snapshot publishing
--   is disabled and existing URLs have expired from receipts, drop only the
--   snapshot_url column. Keep snapshot_revision because migration 233 owns it.

ALTER TABLE public.ride_routes
    ADD COLUMN IF NOT EXISTS snapshot_url text;

COMMENT ON COLUMN public.ride_routes.snapshot_url IS
    'Public immutable PNG URL for snapshot_revision; route readers must require revision equality.';

NOTIFY pgrst, 'reload schema';
