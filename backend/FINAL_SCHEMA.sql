-- Supabase/Postgres schema for Spinr (Modernized & PostGIS)

-- Enable Extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "postgis";

-- Users
CREATE TABLE IF NOT EXISTS users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  phone text UNIQUE,
  first_name text,
  last_name text,
  email text,
  city text,
  role text DEFAULT 'rider',
  profile_complete boolean DEFAULT false,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Vehicle types
CREATE TABLE IF NOT EXISTS vehicle_types (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text,
  description text,
  icon text,
  capacity integer,
  is_active boolean DEFAULT true,
  created_at timestamptz DEFAULT now()
);

-- Drivers
CREATE TABLE IF NOT EXISTS drivers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text,
  phone text UNIQUE,
  photo_url text,
  vehicle_type_id uuid REFERENCES vehicle_types(id),
  vehicle_make text,
  vehicle_model text,
  vehicle_color text,
  license_plate text,
  rating numeric DEFAULT 5.0,
  total_rides integer DEFAULT 0,
  -- PostGIS location column
  location geography(POINT, 4326),
  is_online boolean DEFAULT false,
  is_available boolean DEFAULT true,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Create index for geospatial queries
CREATE INDEX IF NOT EXISTS drivers_location_idx ON drivers USING GIST (location);

-- Service areas
CREATE TABLE IF NOT EXISTS service_areas (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text,
  city text,
  -- Polygon stored as geography
  area geography(POLYGON, 4326),
  is_active boolean DEFAULT true,
  created_at timestamptz DEFAULT now()
);

-- Fare configs
CREATE TABLE IF NOT EXISTS fare_configs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  service_area_id uuid REFERENCES service_areas(id),
  vehicle_type_id uuid REFERENCES vehicle_types(id),
  base_fare numeric,
  per_km_rate numeric,
  per_minute_rate numeric,
  minimum_fare numeric,
  booking_fee numeric,
  is_active boolean DEFAULT true,
  created_at timestamptz DEFAULT now()
);

-- Rides
CREATE TABLE IF NOT EXISTS rides (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  rider_id uuid REFERENCES users(id),
  driver_id uuid REFERENCES drivers(id),
  vehicle_type_id uuid REFERENCES vehicle_types(id),
  pickup_address text,
  pickup_location geography(POINT, 4326),
  dropoff_address text,
  dropoff_location geography(POINT, 4326),
  distance_km numeric,
  duration_minutes integer,
  base_fare numeric,
  distance_fare numeric,
  time_fare numeric,
  booking_fee numeric,
  total_fare numeric,
  tip_amount numeric DEFAULT 0,
  payment_method text DEFAULT 'card',
  payment_intent_id text,
  payment_status text DEFAULT 'pending',
  status text DEFAULT 'searching', -- searching, driver_assigned, driver_arrived, in_progress, completed, cancelled
  pickup_otp text,

  -- Timeline
  ride_requested_at timestamptz DEFAULT now(),
  driver_notified_at timestamptz,
  driver_accepted_at timestamptz,
  driver_arrived_at timestamptz,
  ride_started_at timestamptz,
  ride_completed_at timestamptz,
  cancelled_at timestamptz,

  -- Earnings
  driver_earnings numeric DEFAULT 0,
  admin_earnings numeric DEFAULT 0,
  cancellation_fee_driver numeric DEFAULT 0,
  cancellation_fee_admin numeric DEFAULT 0,

  -- Rating
  rider_rating integer,
  rider_comment text,

  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Saved addresses
CREATE TABLE IF NOT EXISTS saved_addresses (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid REFERENCES users(id),
  name text,
  address text,
  location geography(POINT, 4326),
  icon text DEFAULT 'location',
  created_at timestamptz DEFAULT now()
);

-- Settings (Singleton)
CREATE TABLE IF NOT EXISTS settings (
  id text PRIMARY KEY,
  data jsonb,
  updated_at timestamptz DEFAULT now()
);

-- OTP records (Consider replacing with Supabase Auth or Firebase)
CREATE TABLE IF NOT EXISTS otp_records (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  phone text,
  code text,
  expires_at timestamptz,
  verified boolean DEFAULT false,
  created_at timestamptz DEFAULT now()
);

-- RPC Functions for PostGIS

-- Find nearby drivers
CREATE OR REPLACE FUNCTION find_nearby_drivers(
  lat double precision,
  lng double precision,
  radius_meters double precision
)
RETURNS TABLE (
  id uuid,
  name text,
  vehicle_type_id uuid,
  lat double precision,
  lng double precision,
  distance_meters double precision
)
LANGUAGE sql
AS $$
  SELECT
    id,
    name,
    vehicle_type_id,
    ST_Y(location::geometry) as lat,
    ST_X(location::geometry) as lng,
    ST_Distance(location, ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography) as distance_meters
  FROM drivers
  WHERE
    ST_DWithin(location, ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography, radius_meters)
    AND is_online = true
    AND is_available = true
  ORDER BY
    location <-> ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography;
$$;

-- Update driver location
CREATE OR REPLACE FUNCTION update_driver_location(
  driver_id uuid,
  lat double precision,
  lng double precision
)
RETURNS void
LANGUAGE sql
AS $$
  UPDATE drivers
  SET
    location = ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography,
    updated_at = now()
  WHERE id = driver_id;
$$;

-- RPC: Get service area for point
CREATE OR REPLACE FUNCTION get_service_area_for_point(lat double precision, lng double precision)
RETURNS SETOF service_areas
LANGUAGE sql
AS $$
  SELECT * FROM service_areas
  WHERE ST_Intersects(area, ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography)
  LIMIT 1;
$$;
-- Migration: Enable PostGIS and add geospatial columns

-- Enable PostGIS
CREATE EXTENSION IF NOT EXISTS postgis;

-- Add location column to drivers if not exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='location') THEN
        ALTER TABLE drivers ADD COLUMN location geography(POINT, 4326);
        CREATE INDEX drivers_location_idx ON drivers USING GIST (location);
    END IF;
END $$;

-- Add location column to service_areas if not exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='service_areas' AND column_name='area') THEN
        ALTER TABLE service_areas ADD COLUMN area geography(POLYGON, 4326);
    END IF;
END $$;

-- Add location columns to rides if not exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rides' AND column_name='pickup_location') THEN
        ALTER TABLE rides ADD COLUMN pickup_location geography(POINT, 4326);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rides' AND column_name='dropoff_location') THEN
        ALTER TABLE rides ADD COLUMN dropoff_location geography(POINT, 4326);
    END IF;
END $$;

-- RPC: Find nearby drivers
CREATE OR REPLACE FUNCTION find_nearby_drivers(
  lat double precision,
  lng double precision,
  radius_meters double precision
)
RETURNS TABLE (
  id uuid,
  name text,
  vehicle_type_id uuid,
  lat double precision,
  lng double precision,
  distance_meters double precision
)
LANGUAGE sql
AS $$
  SELECT
    id,
    name,
    vehicle_type_id,
    ST_Y(location::geometry) as lat,
    ST_X(location::geometry) as lng,
    ST_Distance(location, ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography) as distance_meters
  FROM drivers
  WHERE
    ST_DWithin(location, ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography, radius_meters)
    AND is_online = true
    AND is_available = true
  ORDER BY
    location <-> ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography;
$$;

-- RPC: Update driver location
CREATE OR REPLACE FUNCTION update_driver_location(
  driver_id uuid,
  lat double precision,
  lng double precision
)
RETURNS void
LANGUAGE sql
AS $$
  UPDATE drivers
  SET
    location = ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography,
    -- We can update legacy lat/lng columns if they exist, but for now we focus on location
    updated_at = now()
  WHERE id = driver_id;
$$;

-- RPC: Get service area for point
CREATE OR REPLACE FUNCTION get_service_area_for_point(lat double precision, lng double precision)
RETURNS SETOF service_areas
LANGUAGE sql
AS $$
  SELECT * FROM service_areas
  WHERE ST_Intersects(area, ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography)
  LIMIT 1;
$$;

-- Fix: Ensure 'updated_at' column exists on all tables
-- This script is safe to run multiple times.

DO $$
BEGIN
    -- Drivers
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='updated_at') THEN
        ALTER TABLE drivers ADD COLUMN updated_at timestamptz DEFAULT now();
    END IF;

    -- Users
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='updated_at') THEN
        ALTER TABLE users ADD COLUMN updated_at timestamptz DEFAULT now();
    END IF;

    -- Rides
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rides' AND column_name='updated_at') THEN
        ALTER TABLE rides ADD COLUMN updated_at timestamptz DEFAULT now();
    END IF;

    -- Settings
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='settings' AND column_name='updated_at') THEN
        ALTER TABLE settings ADD COLUMN updated_at timestamptz DEFAULT now();
    END IF;
END $$;
-- Add user_id to drivers table
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='user_id') THEN
        ALTER TABLE drivers ADD COLUMN user_id uuid REFERENCES users(id);
        CREATE INDEX IF NOT EXISTS drivers_user_id_idx ON drivers(user_id);
    END IF;
END $$;
