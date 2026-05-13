-- Rollback: run migration 77 again to restore the uuid signature.
--
-- The function was created in migration 77 with p_vehicle_type_id uuid, but
-- drivers.vehicle_type_id is TEXT. Every dispatch call hits:
--   "operator does not exist: text = uuid  (code 42883)"
-- and falls back to the slower Python path. This migration drops the old
-- overload and recreates the function with a text parameter so the WHERE
-- clause compiles without an explicit cast.

DROP FUNCTION IF EXISTS match_and_claim_driver(uuid, float8, float8, float8, float8);

CREATE OR REPLACE FUNCTION match_and_claim_driver(
    p_vehicle_type_id   text,
    p_pickup_lat        float8,
    p_pickup_lng        float8,
    p_radius_km         float8,
    p_min_rating        float8  DEFAULT 0.0
)
RETURNS SETOF drivers
LANGUAGE plpgsql
AS $$
DECLARE
    v_pickup  geography;
    v_driver  drivers;
BEGIN
    v_pickup := ST_SetSRID(ST_MakePoint(p_pickup_lng, p_pickup_lat), 4326)::geography;

    SELECT d.*
    INTO   v_driver
    FROM   drivers d
    WHERE  d.is_online         = true
      AND  d.is_available      = true
      AND  d.vehicle_type_id   = p_vehicle_type_id
      AND  d.is_suspended      = false
      AND  (p_min_rating = 0.0 OR d.rating >= p_min_rating)
      AND  ST_DWithin(
               ST_SetSRID(ST_MakePoint(d.current_lng, d.current_lat), 4326)::geography,
               v_pickup,
               p_radius_km * 1000
           )
    ORDER BY ST_Distance(
                 ST_SetSRID(ST_MakePoint(d.current_lng, d.current_lat), 4326)::geography,
                 v_pickup
             )
    LIMIT 1
    FOR UPDATE SKIP LOCKED;

    IF v_driver.id IS NULL THEN
        RETURN;
    END IF;

    UPDATE drivers
    SET    is_available = false
    WHERE  id = v_driver.id;

    RETURN NEXT v_driver;
END;
$$;
