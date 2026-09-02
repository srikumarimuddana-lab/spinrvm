-- 398_drivers_nearby_location_geog.sql
--
-- PostGIS candidate lookup used when dispatch_geo_provider is `postgis`, or
-- as the failover from an unhealthy Redis/H3 index. Reads location_geog
-- (trigger-maintained from lat/lng since migration 170). Does NOT read the
-- stale `drivers.location` column that find_nearby_drivers uses.
--
-- Returns IDs + distance only. WAV / vehicle_type are optional RPC filters so
-- the 5000-row cap is applied AFTER those predicates, not before Python
-- eligibility. Service-area / presence still re-applied in Python.
-- Backend-only: SECURITY DEFINER with EXECUTE revoked from PUBLIC/anon/
-- authenticated.
--
-- COORDINATED DEPLOY: the matching path stays on the lat/lng box until
-- dispatch_geo_provider is flipped. Applying this file is a no-op for live
-- dispatch.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS drivers_nearby_location_geog(double precision, double precision, double precision, integer, boolean, text);
--   DROP FUNCTION IF EXISTS drivers_nearby_location_geog(double precision, double precision, double precision, integer);

DROP FUNCTION IF EXISTS drivers_nearby_location_geog(double precision, double precision, double precision, integer);

CREATE OR REPLACE FUNCTION drivers_nearby_location_geog(
    p_lat double precision,
    p_lng double precision,
    p_radius_m double precision,
    p_limit integer DEFAULT 500,
    p_is_wav boolean DEFAULT NULL,
    p_vehicle_type_id text DEFAULT NULL
)
RETURNS TABLE(driver_id text, distance_m double precision)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, extensions
AS $$
    SELECT
        d.id::text,
        ST_Distance(
            d.location_geog,
            ST_SetSRID(ST_MakePoint(p_lng, p_lat), 4326)::geography
        ) AS distance_m
    FROM drivers d
    WHERE d.is_online = true
      AND d.is_available = true
      AND d.is_verified = true
      AND d.status = 'active'
      AND d.deleted_at IS NULL
      AND d.location_geog IS NOT NULL
      AND (p_is_wav IS NULL OR d.is_wav = p_is_wav)
      AND (p_vehicle_type_id IS NULL OR d.vehicle_type_id = p_vehicle_type_id)
      AND ST_DWithin(
          d.location_geog,
          ST_SetSRID(ST_MakePoint(p_lng, p_lat), 4326)::geography,
          GREATEST(p_radius_m, 0)
      )
    ORDER BY ST_Distance(
        d.location_geog,
        ST_SetSRID(ST_MakePoint(p_lng, p_lat), 4326)::geography
    )
    LIMIT GREATEST(1, LEAST(COALESCE(p_limit, 500), 5000));
$$;

REVOKE EXECUTE ON FUNCTION drivers_nearby_location_geog(double precision, double precision, double precision, integer, boolean, text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION drivers_nearby_location_geog(double precision, double precision, double precision, integer, boolean, text) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION drivers_nearby_location_geog(double precision, double precision, double precision, integer, boolean, text) TO service_role;
