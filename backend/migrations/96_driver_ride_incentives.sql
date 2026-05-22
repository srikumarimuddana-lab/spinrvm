-- 96_driver_ride_incentives.sql
-- Per-ride driver incentives configurable by admins per service area
-- and vehicle type. Displayed on the ride offer screen to attract
-- driver acceptance.
--
-- Rollback:
--   DROP TABLE IF EXISTS ride_incentive_claims;
--   DROP TABLE IF EXISTS ride_incentives;

CREATE TABLE IF NOT EXISTS ride_incentives (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service_area_id UUID,                       -- NULL = all areas
    vehicle_type_id UUID,                       -- NULL = all vehicle types
    name TEXT NOT NULL,                          -- e.g. "Peak Hour Bonus"
    description TEXT,                            -- shown to driver on offer
    incentive_type TEXT NOT NULL CHECK (incentive_type IN (
        'per_ride',           -- flat bonus on every ride
        'peak_hours',         -- bonus during configured hours
        'time_limited',       -- limited-time campaign
        'min_distance',       -- bonus for rides over X km
        'area_boost'          -- boost for rides in a specific area
    )),
    bonus_amount NUMERIC(12,2) NOT NULL,        -- CAD bonus per ride
    bonus_type TEXT NOT NULL DEFAULT 'flat' CHECK (bonus_type IN ('flat', 'percentage')),
    conditions JSONB DEFAULT '{}',              -- e.g. {"peak_hours": [7,9,16,19], "min_distance_km": 5}
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    start_date TIMESTAMPTZ,                     -- NULL = immediately active
    end_date TIMESTAMPTZ,                       -- NULL = no end date
    priority INTEGER NOT NULL DEFAULT 0,        -- display ordering (higher = shown first)
    max_budget NUMERIC(14,2),                   -- optional total budget cap
    budget_used NUMERIC(14,2) NOT NULL DEFAULT 0,
    created_by UUID,                            -- admin user_id
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Claims log: append-only record of each incentive payout per ride
CREATE TABLE IF NOT EXISTS ride_incentive_claims (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ride_id UUID NOT NULL,
    driver_id UUID NOT NULL,
    incentive_id UUID NOT NULL REFERENCES ride_incentives(id),
    bonus_amount NUMERIC(12,2) NOT NULL,
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_ride_incentives_active
    ON ride_incentives(is_active, service_area_id)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_ride_incentives_vehicle
    ON ride_incentives(vehicle_type_id)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_ride_incentive_claims_ride
    ON ride_incentive_claims(ride_id);

CREATE INDEX IF NOT EXISTS idx_ride_incentive_claims_driver
    ON ride_incentive_claims(driver_id);

CREATE INDEX IF NOT EXISTS idx_ride_incentive_claims_incentive
    ON ride_incentive_claims(incentive_id);

-- RLS
ALTER TABLE ride_incentives ENABLE ROW LEVEL SECURITY;
ALTER TABLE ride_incentive_claims ENABLE ROW LEVEL SECURITY;
