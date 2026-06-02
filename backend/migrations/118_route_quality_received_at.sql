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
