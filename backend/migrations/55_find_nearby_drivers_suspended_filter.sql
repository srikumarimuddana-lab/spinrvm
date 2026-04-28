-- Migration 55: add status != 'suspended' filter to find_nearby_drivers (DV-1 / P0-5).
-- =============================================================================
-- Rollback plan: CREATE OR REPLACE with the old WHERE clause (drop the
--   AND status != 'suspended' line). No data rows are affected; the function
--   is replaced in-place. Old backend can talk to new DB without issue.
--
-- Problem: the find_nearby_drivers RPC only filters on is_online and
--   is_available. A suspended driver whose row still has is_online=true and
--   is_available=true (e.g. suspension happened after the last go-online call
--   but Redis presence was not fully cleared) would appear in dispatch
--   candidates. Defense-in-depth: the SQL function is the last gate, so the
--   filter must be authoritative.
--
-- Fix: add AND status != 'suspended' to the WHERE clause. This is additive —
--   no existing callers break, and active/pending drivers are unaffected.

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
STABLE
AS $$
  SELECT
    id,
    name,
    vehicle_type_id,
    ST_Y(location::geometry)  AS lat,
    ST_X(location::geometry)  AS lng,
    ST_Distance(
      location,
      ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography
    )                          AS distance_meters
  FROM drivers
  WHERE
    ST_DWithin(
      location,
      ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography,
      radius_meters
    )
    AND is_online     = true
    AND is_available  = true
    AND status       != 'suspended'
  ORDER BY
    location <-> ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography;
$$;
