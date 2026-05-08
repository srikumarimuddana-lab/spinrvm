-- Rollback: DROP FUNCTION IF EXISTS match_and_claim_driver(uuid, float8, float8, float8, float8);
--
-- CREATE OR REPLACE is used so re-running this migration on a DB that already
-- has the function is safe and idempotent. The UPDATE that sets is_available=false
-- is part of the atomic claim; partial execution (function created but UPDATE
-- never committed) is rolled back automatically by Postgres within the same
-- plpgsql execution frame.

CREATE OR REPLACE FUNCTION match_and_claim_driver(
    p_vehicle_type_id   uuid,
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

    -- SELECT ... FOR UPDATE SKIP LOCKED ensures that concurrent dispatch calls
    -- on different replicas cannot claim the same driver row simultaneously.
    -- Only drivers with both is_online AND is_available=true are candidates
    -- (invariant: is_available => is_online; see CLAUDE.md driver flags section).
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

    -- No available driver found within radius — return empty set.
    IF v_driver.id IS NULL THEN
        RETURN;
    END IF;

    -- Atomically mark driver unavailable so no second replica can claim them.
    -- is_online is left untouched — the driver is still online, just no longer
    -- eligible for a new offer while this one is in-flight.
    UPDATE drivers
    SET    is_available = false
    WHERE  id = v_driver.id;

    RETURN NEXT v_driver;
END;
$$;
