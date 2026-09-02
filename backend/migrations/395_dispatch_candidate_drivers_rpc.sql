-- 395_dispatch_candidate_drivers_rpc.sql
--
-- WS-C / audit C4: let dispatch fetch candidates through the PostGIS GiST index
-- instead of a lat/lng bounding box + a Python distance loop.
--
-- Today routes/rides/matching.py issues:
--     get_rows("drivers", {is_online, is_available, is_verified, status,
--                          vehicle_type_id, $and: dispatch_geo_bounds(...)},
--              limit=500)
-- and then filter_and_rank_drivers() walks the result computing haversine
-- distance in Python. The box is served by idx_drivers_online_available_recency
-- (migration 138), so the geo predicates are a filter *within* an online-fleet
-- walk rather than an indexed lookup — and the LIMIT 500 truncates arbitrarily
-- rather than by distance, so on a dense night the nearest driver can sit in
-- row 501 and dispatch reports "no drivers".
--
-- drivers.location_geog (migration 170) is already trigger-maintained from
-- lat/lng and already carries the partial GiST index
-- idx_drivers_location_geog_available (WHERE is_online AND is_available).
-- matching.py's own comment says the next step is "an RPC on that column rather
-- than a second btree that every location heartbeat would have to maintain".
-- This is that RPC.
--
-- SEMANTICS — deliberately a SUPERSET, matching the box it replaces:
--   dispatch_geo_bounds() pads by 10% + 1 km and documents that the box is a
--   superset of the circle, with filter_and_rank_drivers() as "the exact
--   haversine gate". This function keeps that contract exactly: the CALLER
--   passes an already-padded p_radius_m, so ST_DWithin returns a superset and
--   the Python ranking stays the single source of truth for which drivers are
--   actually in range. Nothing about ranking, presence filtering, the
--   subscription gate or the service-area guard moves into SQL.
--
--   ORDER BY location_geog <-> point makes the LIMIT distance-ordered rather
--   than arbitrary, which is the part that fixes the row-501 truncation bug.
--   <-> on geography is a KNN operator the GiST index can serve directly.
--
-- SECURITY: SECURITY DEFINER + pinned search_path, and EXECUTE revoked from
-- PUBLIC/anon/authenticated then granted only to service_role — same posture as
-- drivers_available_in_polygon in migration 170. This returns driver locations,
-- so a default PUBLIC EXECUTE on a SECURITY DEFINER function would be a PIPEDA
-- leak. The projection is deliberately identical to the columns matching.py
-- already requests: no encrypted PII (address, licence, vehicle details) is
-- exposed, per the P1 note on that query.
--
-- ROLLBACK (safe at any time — nothing reads this until the
-- DISPATCH_SPATIAL_CANDIDATES env flag is switched on, and the flag falls back
-- to the existing box query):
--   DROP FUNCTION IF EXISTS dispatch_candidate_drivers(
--       double precision, double precision, double precision, text[], boolean,
--       text[], boolean, integer);
--
-- EXPECTED PLAN (verify on staging before enabling the flag — the whole point
-- is the index scan; a Seq Scan here means the partial index's WHERE clause no
-- longer matches the predicates below):
--   EXPLAIN ANALYZE SELECT * FROM dispatch_candidate_drivers(
--       52.1332, -106.6700, 14300, ARRAY['<vehicle_type_id>'], false, NULL, true, 500);
--   -> Index Scan using idx_drivers_location_geog_available on drivers
--      Order By: (location_geog <-> '...'::geography)

CREATE OR REPLACE FUNCTION dispatch_candidate_drivers(
    p_lat                   double precision,
    p_lng                   double precision,
    p_radius_m              double precision,
    p_vehicle_type_ids      text[],
    p_requires_wav          boolean DEFAULT false,
    p_area_ids              text[] DEFAULT NULL,
    p_allow_unassigned_area boolean DEFAULT true,
    p_limit                 integer DEFAULT 500
)
RETURNS TABLE(
    id                text,
    user_id           text,
    lat               double precision,
    lng               double precision,
    rating            double precision,
    is_wav            boolean,
    acceptance_rate   double precision,
    destination_mode  boolean,
    destination_lat   double precision,
    destination_lng   double precision,
    vehicle_type_id   text,
    service_area_id   text,
    distance_m        double precision
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, extensions AS $$
    -- The pickup point is written out inline at each use rather than built once
    -- in a CTE. A CTE would read better, but `<->` can only be answered by the
    -- GiST index when one side is effectively a constant for the scan; joining
    -- against a CTE row risks the planner materialising it and falling back to a
    -- sort over every online driver, which would silently cost exactly the
    -- speed-up this function exists for. Function parameters ARE constant per
    -- call, so the inline form keeps the KNN index usable.
    SELECT
        d.id,
        d.user_id,
        d.lat,
        d.lng,
        d.rating,
        d.is_wav,
        d.acceptance_rate,
        d.destination_mode,
        d.destination_lat,
        d.destination_lng,
        d.vehicle_type_id,
        d.service_area_id,
        ST_Distance(d.location_geog, ST_SetSRID(ST_MakePoint(p_lng, p_lat), 4326)::geography) AS distance_m
    FROM drivers d
    WHERE d.is_online = true
      AND d.is_available = true
      AND d.is_verified = true
      AND d.status = 'active'
      AND d.vehicle_type_id = ANY(p_vehicle_type_ids)
      AND d.location_geog IS NOT NULL
      -- WAV is an "only if required" narrowing, exactly like the dict filter:
      -- a non-WAV ride must still be offered to a WAV driver.
      AND (NOT p_requires_wav OR d.is_wav = true)
      -- Service-area guard. Mirrors build_driver_area_filter(), including the
      -- reason it emits an $or: SQL IN never matches NULL, so an unassigned
      -- driver would be silently dropped by a bare = ANY().
      AND (
            p_area_ids IS NULL
            OR d.service_area_id = ANY(p_area_ids)
            OR (p_allow_unassigned_area AND d.service_area_id IS NULL)
          )
      AND ST_DWithin(
          d.location_geog,
          ST_SetSRID(ST_MakePoint(p_lng, p_lat), 4326)::geography,
          p_radius_m
      )
    ORDER BY d.location_geog <-> ST_SetSRID(ST_MakePoint(p_lng, p_lat), 4326)::geography
    LIMIT p_limit;
$$;

REVOKE EXECUTE ON FUNCTION dispatch_candidate_drivers(
    double precision, double precision, double precision, text[], boolean,
    text[], boolean, integer) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION dispatch_candidate_drivers(
    double precision, double precision, double precision, text[], boolean,
    text[], boolean, integer) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION dispatch_candidate_drivers(
    double precision, double precision, double precision, text[], boolean,
    text[], boolean, integer) TO service_role;
