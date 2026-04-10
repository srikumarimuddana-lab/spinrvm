-- ============================================================
-- Migration: Rides Admin Dashboard Overhaul
-- Date: 2026-04-07
-- Description: Adds tables and columns for rides admin dashboard
--   - flags (rider/driver behavioral flags, 3 = auto-ban)
--   - complaints (ride-level complaints)
--   - lost_and_found (lost item reports with driver notification)
--   - driver_location_history (formal schema for GPS breadcrumbs)
--   - service_area_id on rides (links ride to pickup service area)
--   - status on users and drivers (active/suspended/banned)
-- ============================================================

-- ============================================================
-- ALTER EXISTING TABLES
-- ============================================================

-- Add service_area_id to rides (links ride to the service area of its pickup point)
ALTER TABLE rides ADD COLUMN IF NOT EXISTS service_area_id TEXT;
CREATE INDEX IF NOT EXISTS idx_rides_service_area ON rides(service_area_id);

-- Add status column to users (active/suspended/banned)
ALTER TABLE users ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';

-- Add status column to drivers (active/suspended/banned)
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';

-- ============================================================
-- FLAGS (rider/driver behavioral flags – 3 flags = auto-ban)
-- ============================================================
CREATE TABLE IF NOT EXISTS flags (
    id              TEXT PRIMARY KEY,
    target_type     TEXT NOT NULL,                          -- 'rider' or 'driver'
    target_id       TEXT NOT NULL,                          -- user_id for riders, driver_id for drivers
    ride_id         TEXT REFERENCES rides(id),
    reason          TEXT NOT NULL,                          -- 'vomited_in_car', 'misbehaved', 'no_show', 'damage', 'fraud', 'other'
    description     TEXT,
    flagged_by      TEXT NOT NULL,                          -- admin user id
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_flags_target ON flags(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_flags_ride ON flags(ride_id);
ALTER TABLE flags ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- COMPLAINTS (ride-level complaints against rider/driver)
-- ============================================================
CREATE TABLE IF NOT EXISTS complaints (
    id              TEXT PRIMARY KEY,
    ride_id         TEXT NOT NULL REFERENCES rides(id),
    against_type    TEXT NOT NULL,                          -- 'rider' or 'driver'
    against_id      TEXT NOT NULL,                          -- user_id or driver_id
    category        TEXT NOT NULL,                          -- 'safety', 'behavior', 'fraud', 'damage', 'other'
    description     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open',           -- open/investigating/resolved/dismissed
    resolution      TEXT,
    created_by      TEXT NOT NULL,                          -- admin user id
    resolved_by     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_complaints_ride ON complaints(ride_id);
CREATE INDEX IF NOT EXISTS idx_complaints_against ON complaints(against_type, against_id);
ALTER TABLE complaints ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- LOST AND FOUND (report lost items, notify driver)
-- ============================================================
CREATE TABLE IF NOT EXISTS lost_and_found (
    id              TEXT PRIMARY KEY,
    ride_id         TEXT NOT NULL REFERENCES rides(id),
    rider_id        TEXT NOT NULL,
    driver_id       TEXT NOT NULL,
    item_description TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'reported',       -- reported/driver_notified/resolved/unresolved
    admin_notes     TEXT,
    notified_at     TIMESTAMPTZ,
    resolved_at     TIMESTAMPTZ,
    created_by      TEXT NOT NULL,                          -- admin user id
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lost_found_ride ON lost_and_found(ride_id);
ALTER TABLE lost_and_found ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- DRIVER LOCATION HISTORY (formal schema declaration)
-- Already used by websocket.py but schema was undeclared
-- ============================================================
CREATE TABLE IF NOT EXISTS driver_location_history (
    id              TEXT PRIMARY KEY,
    driver_id       TEXT NOT NULL,
    ride_id         TEXT,
    lat             FLOAT NOT NULL,
    lng             FLOAT NOT NULL,
    speed           FLOAT,
    heading         FLOAT,
    tracking_phase  TEXT DEFAULT 'online_idle',             -- online_idle/navigating_to_pickup/arrived_at_pickup/trip_in_progress
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dlh_ride ON driver_location_history(ride_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_dlh_driver ON driver_location_history(driver_id, timestamp);
