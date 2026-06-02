-- 118_route_quality_received_at.sql
-- Preserve device capture timestamps separately from server receipt time and
-- persist route quality / route-geometry save status for billing and dispute review.

ALTER TABLE driver_location_history
    ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ DEFAULT NOW();

COMMENT ON COLUMN driver_location_history.timestamp IS
    'Device GPS capture timestamp when supplied by the client; otherwise server receipt time for legacy clients.';

COMMENT ON COLUMN driver_location_history.received_at IS
    'Server receipt timestamp for the GPS breadcrumb. Used to compare client capture time vs ingestion time.';

CREATE INDEX IF NOT EXISTS idx_dlh_received_at
    ON driver_location_history(received_at);

-- Defensive bootstrap: production databases that missed migration 117 (or ran
-- this migration manually from the SQL editor) otherwise fail with
-- `relation "ride_routes" does not exist` before the new quality columns can be
-- added. Keep this table definition aligned with 117_ride_routes.sql; migration
-- 117 remains the authoritative migration for RLS/retention-function changes.
CREATE TABLE IF NOT EXISTS ride_routes (
    ride_id          text PRIMARY KEY REFERENCES rides(id) ON DELETE CASCADE,
    phase_distances  jsonb NOT NULL DEFAULT '{}'::jsonb,
    phase_durations  jsonb NOT NULL DEFAULT '{}'::jsonb,
    phase_polylines  jsonb NOT NULL DEFAULT '{}'::jsonb,
    road_polyline    jsonb NOT NULL DEFAULT '[]'::jsonb,
    gps_points_count integer NOT NULL DEFAULT 0,
    computed_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE ride_routes IS
    'Per-ride route detail (per-phase distances/durations/polylines + OSRM road-matched line), 1:1 with rides. Write-once at completion, read-on-demand by the admin map modal. Geometry cleared at 3y by purge_pii_retention; row cascades on the 7y rides delete.';

CREATE INDEX IF NOT EXISTS idx_ride_routes_computed_at ON ride_routes (computed_at);
ALTER TABLE ride_routes ENABLE ROW LEVEL SECURITY;

ALTER TABLE ride_routes
    ADD COLUMN IF NOT EXISTS route_quality JSONB NOT NULL DEFAULT '{}'::JSONB,
    ADD COLUMN IF NOT EXISTS save_status TEXT NOT NULL DEFAULT 'saved',
    ADD COLUMN IF NOT EXISTS save_error TEXT;

COMMENT ON COLUMN ride_routes.route_quality IS
    'Route quality/confidence metadata captured at settlement: point counts, rejected segment ratio, max GPS gap, distance provider, and road-snap outcome.';

COMMENT ON COLUMN ride_routes.save_status IS
    'Best-effort route geometry persistence status for admin/dispute review.';

COMMENT ON COLUMN ride_routes.save_error IS
    'Last route geometry persistence error, truncated by the backend, when save_status is failed.';

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS route_quality JSONB NOT NULL DEFAULT '{}'::JSONB,
    ADD COLUMN IF NOT EXISTS route_geometry_status TEXT NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS route_geometry_error TEXT;

COMMENT ON COLUMN rides.route_quality IS
    'Settlement route quality/confidence summary duplicated from ride_routes for billing/dispute list views.';

COMMENT ON COLUMN rides.route_geometry_status IS
    'Whether the heavy route geometry side-table was saved at completion: pending/saved/failed.';

COMMENT ON COLUMN rides.route_geometry_error IS
    'Last route geometry side-table save error, if saving failed after retries.';

-- Existing databases can already have ride_routes from migration 117. Backfill
-- the lightweight rides status so admin list views do not report completed
-- historical rides with saved geometry as still pending.
UPDATE rides r
SET route_geometry_status = 'saved',
    route_geometry_error = NULL
FROM ride_routes rr
WHERE rr.ride_id = r.id
  AND COALESCE(r.route_geometry_status, 'pending') = 'pending';
