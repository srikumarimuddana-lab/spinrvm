-- Rollback: DROP FUNCTION IF EXISTS get_service_area_for_point(float8, float8);
--
-- This function already exists verbatim in backend/sql/01_postgis_schema.sql
-- (lines 84-91) but was never landed in the migrations runner, so production
-- may be missing it or may have the older version without the is_active filter
-- and without the STABLE volatility marker.
-- CREATE OR REPLACE is safe to re-run against an existing definition.

CREATE OR REPLACE FUNCTION get_service_area_for_point(lat double precision, lng double precision)
RETURNS SETOF service_areas
LANGUAGE sql
STABLE
AS $$
  SELECT * FROM service_areas
  WHERE is_active = true
    AND ST_Intersects(area, ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography)
  LIMIT 1;
$$;
