-- Flag finalized rides whose inferred gap connectors look like router detours.
--
-- READ-ONLY. Corrects nothing, writes nothing. Run it, review the rows, then
-- decide per ride — a finalized distance is insurance-audit and receipt
-- evidence, so it is never rewritten in bulk (CLAUDE.md: a `git revert` is not
-- a rollback for data already applied).
--
-- WHY
-- When GPS drops mid-trip the finalizer asks OSRM/Google to route between the
-- last fix before the hole and the first fix after it. The router answers with
-- the fastest legal path, which on a divided road can mean driving to the next
-- turnaround and back. That fabricated distance was then published as the
-- ride's Actual Trip km. Ride 0c24901f: a ~389 m hole bridged with a 2.33 km
-- connector, publishing 8.96 km against a 6.99 km booking.
--
-- WHAT THIS DETECTS
-- The shape signature, using only what is already persisted: a ROUTED
-- (non-haversine) inferred connector whose own road distance is far longer
-- than the straight line between its own two endpoints. The live fix also
-- applies a time-based test (implied speed over the gap's elapsed seconds),
-- which cannot be reproduced here because per-connector timing is not stored.
-- So this query is a superset filter by shape, not a replay of the new guard.
--
-- TUNING
-- :ratio_threshold  - detour multiple over the crow-flies gap. 5.0 is the
--                     ceiling the routing layer itself enforces; lower it to
--                     ~2.5 to surface milder detours.
-- :min_excess_km    - ignore connectors whose absolute overshoot is trivial.
--                     0.3 km keeps short around-the-block routing out of the
--                     report.
--
-- Postgres 9.4+ (jsonb). Safe to run against production.

WITH segment AS (
    SELECT
        r.ride_id,
        r.finalized_at,
        r.route_revision,
        seg
    FROM ride_routes r
    -- Guard inside the lateral: a non-array value would make
    -- jsonb_array_elements raise before any WHERE clause could filter it out.
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(r.road_matched_segments) = 'array' THEN r.road_matched_segments
            ELSE '[]'::jsonb
        END
    ) AS seg
    WHERE r.processing_status = 'complete'
),
connector AS (
    SELECT
        s.ride_id,
        s.finalized_at,
        s.route_revision,
        s.seg ->> 'gap_reason'             AS gap_reason,
        s.seg ->> 'provider'               AS provider,
        (s.seg ->> 'distance_km')::numeric AS connector_km,
        s.seg -> 'coordinates'             AS coordinates
    FROM segment s
    WHERE s.seg ->> 'geometry_kind' = 'inferred'
      -- Straight-line connectors are already excluded from measured distance,
      -- so they cannot inflate a published number. Only routed ones can.
      AND s.seg ->> 'provider' IS DISTINCT FROM 'haversine_interpolated'
      AND jsonb_typeof(s.seg -> 'coordinates') = 'array'
      AND (s.seg ->> 'distance_km') IS NOT NULL
),
endpoint AS (
    SELECT
        c.ride_id,
        c.finalized_at,
        c.route_revision,
        c.gap_reason,
        c.provider,
        c.connector_km,
        (c.coordinates -> 0 ->> 0)::numeric AS from_lat,
        (c.coordinates -> 0 ->> 1)::numeric AS from_lng,
        (c.coordinates -> (jsonb_array_length(c.coordinates) - 1) ->> 0)::numeric AS to_lat,
        (c.coordinates -> (jsonb_array_length(c.coordinates) - 1) ->> 1)::numeric AS to_lng
    FROM connector c
    WHERE jsonb_array_length(c.coordinates) >= 2
),
measured AS (
    SELECT
        e.*,
        -- Haversine, km. Mirrors utils/route_reconstruction_projection.distance_m.
        (2 * 6371 * asin(sqrt(
            power(sin(radians(e.to_lat - e.from_lat) / 2), 2)
            + cos(radians(e.from_lat)) * cos(radians(e.to_lat))
              * power(sin(radians(e.to_lng - e.from_lng) / 2), 2)
        )))::numeric AS direct_km
    FROM endpoint e
    WHERE e.from_lat IS NOT NULL AND e.from_lng IS NOT NULL
      AND e.to_lat IS NOT NULL AND e.to_lng IS NOT NULL
)
SELECT
    m.ride_id,
    m.finalized_at,
    m.route_revision,
    m.gap_reason,
    m.provider,
    round(m.connector_km, 3)                 AS connector_km,
    round(m.direct_km, 3)                    AS straight_line_km,
    round(m.connector_km - m.direct_km, 3)   AS phantom_km,
    round(m.connector_km / m.direct_km, 2)   AS detour_ratio
FROM measured m
WHERE m.direct_km > 0
  AND m.connector_km > m.direct_km * 5.0        -- :ratio_threshold
  AND m.connector_km - m.direct_km > 0.3        -- :min_excess_km
ORDER BY (m.connector_km - m.direct_km) DESC
LIMIT 500;
