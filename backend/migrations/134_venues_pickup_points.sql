-- 134_venues_pickup_points.sql
-- Curated pickup points for large venues (malls, airport, stadiums). When a
-- rider's pickup pin falls inside a venue's detection radius, the rider app
-- offers a short list of named, driver-reachable meeting points ("North
-- entrance", "Door C") instead of a pin a car can't reach. This is the durable
-- solution that complements the per-ride snap_to_road fallback.
--
-- Single table; the named points live in a JSONB array on the venue row
-- ([{name, lat, lng}, ...]) so the admin edits a venue + its points together.
-- Operational config, NOT user PII: riders read via the backend (service role),
-- so RLS is locked down (no anon/authenticated policies).
--
-- Rollback:
--   DROP TABLE IF EXISTS venues;

CREATE TABLE IF NOT EXISTS venues (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text NOT NULL,
    center_lat      double precision NOT NULL,
    center_lng      double precision NOT NULL,
    radius_m        integer NOT NULL DEFAULT 150,   -- detection radius around center
    pickup_points   jsonb NOT NULL DEFAULT '[]'::jsonb,  -- [{name, lat, lng}]
    service_area_id text,
    is_active       boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Detection scan reads active venues only.
CREATE INDEX IF NOT EXISTS idx_venues_active ON venues (is_active);

COMMENT ON TABLE venues IS
    'Curated pickup venues (malls/airport). pickup_points JSONB = [{name,lat,lng}]. Read by rider app via backend /maps/pickup-points; admin-managed.';

-- Service role (backend) bypasses RLS by design; no anon/authenticated policies
-- means the frontend keys cannot read/write venues directly.
ALTER TABLE venues ENABLE ROW LEVEL SECURITY;
