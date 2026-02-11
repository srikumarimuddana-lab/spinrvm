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
