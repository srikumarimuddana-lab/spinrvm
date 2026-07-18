-- 240_ride_route_snapshot_object_ledger.sql
--
-- Preserve every private v2 route-image object path until its physical Storage
-- deletion is confirmed. ``ride_routes.snapshot_object_path`` is a current
-- projection and is intentionally cleared when late GPS evidence reopens a
-- route; this append-only ledger prevents older revision objects from being
-- orphaned outside the three-year GPS retention purge.
--
-- Rollback plan:
--   Stop private snapshot uploads and the retention worker before dropping the
--   table. Do not remove the table while any private route object exists: it is
--   the durable deletion inventory for those objects.

CREATE TABLE IF NOT EXISTS public.ride_route_snapshot_objects (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ride_id uuid REFERENCES public.rides(id) ON DELETE SET NULL,
    storage_bucket text NOT NULL,
    object_path text NOT NULL,
    route_revision integer NOT NULL,
    route_finalized_at timestamptz NOT NULL,
    retention_due_at timestamptz NOT NULL,
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ride_route_snapshot_objects_revision_nonnegative CHECK (route_revision >= 0) NOT VALID,
    CONSTRAINT ride_route_snapshot_objects_unique_path UNIQUE (storage_bucket, object_path)
);

CREATE INDEX IF NOT EXISTS idx_route_snapshot_objects_retention
    ON public.ride_route_snapshot_objects(retention_due_at)
    WHERE deleted_at IS NULL;

ALTER TABLE public.ride_route_snapshot_objects ENABLE ROW LEVEL SECURITY;

-- There are deliberately no authenticated/anon policies. The service-role
-- backend owns this deletion inventory and must not expose route object paths
-- through the browser Data API.

-- Backfill v2 snapshots created before the ledger shipped. The calculated
-- deadline matches the retention function's exact PostgreSQL 3-year interval.
INSERT INTO public.ride_route_snapshot_objects (
    ride_id,
    storage_bucket,
    object_path,
    route_revision,
    route_finalized_at,
    retention_due_at
)
SELECT
    ride_id,
    'ride-route-snapshots',
    snapshot_object_path,
    route_revision,
    COALESCE(finalized_at, computed_at, now()),
    COALESCE(finalized_at, computed_at, now()) + INTERVAL '3 years'
FROM public.ride_routes
WHERE snapshot_object_path IS NOT NULL
ON CONFLICT (storage_bucket, object_path) DO NOTHING;

NOTIFY pgrst, 'reload schema';
