-- Repair planned_route_polyline point shape on legacy-imported rides.
--
-- Rollback (lossless inverse — converts [lat,lng] pairs back to {lat,lng} objects
-- for imported rides only):
--   UPDATE rides r SET planned_route_polyline = sub.reverted
--   FROM (
--     SELECT r2.id,
--            jsonb_agg(jsonb_build_object('lat', p->0, 'lng', p->1) ORDER BY ord) AS reverted
--     FROM rides r2
--     CROSS JOIN LATERAL jsonb_array_elements(r2.planned_route_polyline)
--          WITH ORDINALITY AS t(p, ord)
--     WHERE r2.legacy_import_metadata IS NOT NULL
--       AND jsonb_typeof(r2.planned_route_polyline) = 'array'
--       AND jsonb_array_length(r2.planned_route_polyline) > 0
--       AND jsonb_typeof(r2.planned_route_polyline -> 0) = 'array'
--     GROUP BY r2.id
--   ) sub WHERE r.id = sub.id;
--
-- Why: migration 100 defines planned_route_polyline as a decoded [[lat, lng], …]
-- array, and every consumer reads it that way — backend/schemas.py
-- (List[List[float]]), driver-app/store/driverStore.ts and rider-app/store/
-- rideStore.ts ([number, number][]), driver-app/lib/androidAuto/carRoute.ts,
-- admin-dashboard ride-detail-modal.tsx (indexes p[0]/p[1]), and the shared
-- validCoordinate() guard in shared/utils/routeSegments.ts, which requires each
-- point to be an ARRAY and rejects the whole segment otherwise.
--
-- backend/scripts/backfill_imported_ride_routes.py wrote OSRM geometry as
-- [{"lat": …, "lng": …}, …] instead. The rows are otherwise valid, so nothing
-- errored — the points silently failed validCoordinate(), the route segment was
-- dropped, and the driver/rider ride-detail maps rendered with no route line
-- (and, with no coordinates to fit, stayed at the wide default region).
--
-- This converts only object-shaped points, so it is idempotent and a no-op on
-- rows already in the correct shape. Points missing numeric lat/lng are dropped;
-- a row that would end up with fewer than 2 usable points is left untouched
-- rather than written back as an unusable stub.

UPDATE rides r
SET planned_route_polyline = sub.converted
FROM (
    SELECT
        r2.id,
        jsonb_agg(
            jsonb_build_array((p ->> 'lat')::numeric, (p ->> 'lng')::numeric)
            ORDER BY ord
        ) FILTER (
            WHERE jsonb_typeof(p -> 'lat') = 'number'
              AND jsonb_typeof(p -> 'lng') = 'number'
        ) AS converted
    FROM rides r2
    CROSS JOIN LATERAL jsonb_array_elements(r2.planned_route_polyline)
        WITH ORDINALITY AS t(p, ord)
    WHERE r2.planned_route_polyline IS NOT NULL
      AND jsonb_typeof(r2.planned_route_polyline) = 'array'
      AND jsonb_array_length(r2.planned_route_polyline) > 0
      AND jsonb_typeof(r2.planned_route_polyline -> 0) = 'object'
    GROUP BY r2.id
) sub
WHERE r.id = sub.id
  AND sub.converted IS NOT NULL
  AND jsonb_array_length(sub.converted) >= 2;
