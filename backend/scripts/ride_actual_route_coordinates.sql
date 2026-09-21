-- Lat/lng behind a ride's "Actual Trip" distance.
--
-- READ-ONLY. Replace the ride id in each query.
--
-- IMPORTANT: coordinates in ride_routes.road_matched_segments are stored
-- [lat, lng] (see utils/route_reconstruction_projection.coordinate), which is
-- the OPPOSITE of the GeoJSON [lng, lat] order. Element 0 is latitude.
--
-- Two different things live here, and mixing them is the usual mistake:
--   * road_matched_segments = the FINALIZED route. Observed segments are GPS
--     snapped to roads; inferred segments are gap fill that no GPS witnessed.
--     This is what the map draws and what the km card is derived from.
--   * driver_location_history = the RAW breadcrumbs the device actually sent.
--     Evidence, not geometry. Q4.

-- ---------------------------------------------------------------------------
-- Q1. Headline: what the Actual Trip card is showing, and what it rests on.
-- ---------------------------------------------------------------------------
SELECT
    rd.id                                                         AS ride_id,
    rd.actual_distance_km                                         AS actual_trip_km,
    rd.distance_km                                                AS fare_distance_km,
    rd.ride_metrics -> 'phases' -> 'trip_in_progress' ->> 'distance_basis' AS distance_basis,
    r.route_quality ->> 'observed_distance_km'                    AS observed_km,
    r.route_quality ->> 'inferred_distance_km'                    AS inferred_km,
    r.route_quality ->> 'observed_distance_ratio'                 AS observed_ratio,
    r.route_quality ->> 'inferred_distance_ratio'                 AS inferred_ratio,
    r.route_quality ->> 'inferred_gap_count'                      AS inferred_gap_count,
    r.route_quality -> 'failed_gaps'                              AS failed_gaps,
    r.route_quality ->> 'finalization_reason'                     AS finalization_reason,
    r.processing_status,
    r.route_revision,
    r.finalized_at
FROM rides rd
LEFT JOIN ride_routes r ON r.ride_id = rd.id
WHERE rd.id = '0c24901f-7c9e-4de5-9792-19f954b713a5';

-- ---------------------------------------------------------------------------
-- Q2. Per-segment breakdown — where the km came from, and which are invented.
--     Sort by inferred_km to see what gap fill contributed.
-- ---------------------------------------------------------------------------
SELECT
    s.seg_ord                                                     AS segment_no,
    s.seg ->> 'geometry_kind'                                     AS kind,        -- observed | inferred
    s.seg ->> 'gap_reason'                                        AS gap_reason,  -- null for observed
    s.seg ->> 'provider'                                          AS provider,
    (s.seg ->> 'distance_km')::numeric                            AS segment_km,
    jsonb_array_length(s.seg -> 'coordinates')                    AS point_count,
    (s.seg -> 'coordinates' -> 0 ->> 0)::numeric                  AS start_lat,
    (s.seg -> 'coordinates' -> 0 ->> 1)::numeric                  AS start_lng,
    (s.seg -> 'coordinates' -> (jsonb_array_length(s.seg -> 'coordinates') - 1) ->> 0)::numeric AS end_lat,
    (s.seg -> 'coordinates' -> (jsonb_array_length(s.seg -> 'coordinates') - 1) ->> 1)::numeric AS end_lng
FROM ride_routes r
CROSS JOIN LATERAL jsonb_array_elements(
    CASE WHEN jsonb_typeof(r.road_matched_segments) = 'array' THEN r.road_matched_segments ELSE '[]'::jsonb END
) WITH ORDINALITY AS s(seg, seg_ord)
WHERE r.ride_id = '0c24901f-7c9e-4de5-9792-19f954b713a5'
  AND jsonb_typeof(s.seg -> 'coordinates') = 'array'
ORDER BY s.seg_ord;

-- ---------------------------------------------------------------------------
-- Q3. Every lat/lng of the finalized route, in travel order.
--     `map_link` pastes straight into Google Maps to check a suspect stretch.
-- ---------------------------------------------------------------------------
SELECT
    s.seg_ord                            AS segment_no,
    c.pt_ord                             AS point_no,
    s.seg ->> 'geometry_kind'            AS kind,
    s.seg ->> 'gap_reason'               AS gap_reason,
    (c.pt ->> 0)::numeric                AS lat,
    (c.pt ->> 1)::numeric                AS lng,
    (c.pt ->> 0) || ',' || (c.pt ->> 1)  AS map_link
FROM ride_routes r
CROSS JOIN LATERAL jsonb_array_elements(
    CASE WHEN jsonb_typeof(r.road_matched_segments) = 'array' THEN r.road_matched_segments ELSE '[]'::jsonb END
) WITH ORDINALITY AS s(seg, seg_ord)
CROSS JOIN LATERAL jsonb_array_elements(
    CASE WHEN jsonb_typeof(s.seg -> 'coordinates') = 'array' THEN s.seg -> 'coordinates' ELSE '[]'::jsonb END
) WITH ORDINALITY AS c(pt, pt_ord)
WHERE r.ride_id = '0c24901f-7c9e-4de5-9792-19f954b713a5'
ORDER BY s.seg_ord, c.pt_ord;

-- Q3b. Inferred points only — the stretch no GPS witnessed. On the reported
--      ride this is the detour: compare these against the road you drove.
SELECT
    s.seg_ord                            AS segment_no,
    s.seg ->> 'gap_reason'               AS gap_reason,
    s.seg ->> 'provider'                 AS provider,
    (s.seg ->> 'distance_km')::numeric   AS segment_km,
    (c.pt ->> 0)::numeric                AS lat,
    (c.pt ->> 1)::numeric                AS lng
FROM ride_routes r
CROSS JOIN LATERAL jsonb_array_elements(
    CASE WHEN jsonb_typeof(r.road_matched_segments) = 'array' THEN r.road_matched_segments ELSE '[]'::jsonb END
) WITH ORDINALITY AS s(seg, seg_ord)
CROSS JOIN LATERAL jsonb_array_elements(
    CASE WHEN jsonb_typeof(s.seg -> 'coordinates') = 'array' THEN s.seg -> 'coordinates' ELSE '[]'::jsonb END
) WITH ORDINALITY AS c(pt, pt_ord)
WHERE r.ride_id = '0c24901f-7c9e-4de5-9792-19f954b713a5'
  AND s.seg ->> 'geometry_kind' = 'inferred'
ORDER BY s.seg_ord, c.pt_ord;

-- ---------------------------------------------------------------------------
-- Q4. Raw GPS the device actually sent — the evidence under all of the above.
--     `gap_seconds` is the interval since the previous fix: that is where the
--     holes are, and any row over 60 s is what split the trace into segments.
-- ---------------------------------------------------------------------------
-- NB: "timestamp" is a reserved word in Postgres and must stay quoted.
SELECT
    h."timestamp",
    h.tracking_phase,
    h.lat,
    h.lng,
    round(EXTRACT(EPOCH FROM (h."timestamp" - lag(h."timestamp") OVER (ORDER BY h."timestamp")))::numeric, 1)
        AS gap_seconds
FROM driver_location_history h
WHERE h.ride_id = '0c24901f-7c9e-4de5-9792-19f954b713a5'
ORDER BY h."timestamp";

-- ---------------------------------------------------------------------------
-- Q5. Precision audit — how many decimal places are ACTUALLY stored.
--     Reads the raw JSON text, so nothing in this query can round anything.
--     Expect max 6. JSON drops trailing zeros, so 50.452 is 50.452000 stored
--     at 3 "decimals" — that is display, not precision loss. What would be a
--     real finding is a MAXIMUM of 4 across many points.
-- ---------------------------------------------------------------------------
SELECT
    s.seg ->> 'geometry_kind'                                  AS kind,
    s.seg ->> 'provider'                                       AS provider,
    count(*)                                                   AS points,
    max(length(split_part(c.pt ->> 0, '.', 2)))                AS max_lat_decimals,
    max(length(split_part(c.pt ->> 1, '.', 2)))                AS max_lng_decimals,
    round(avg(length(split_part(c.pt ->> 0, '.', 2))), 2)      AS avg_lat_decimals
FROM ride_routes r
CROSS JOIN LATERAL jsonb_array_elements(
    CASE WHEN jsonb_typeof(r.road_matched_segments) = 'array' THEN r.road_matched_segments ELSE '[]'::jsonb END
) WITH ORDINALITY AS s(seg, seg_ord)
CROSS JOIN LATERAL jsonb_array_elements(
    CASE WHEN jsonb_typeof(s.seg -> 'coordinates') = 'array' THEN s.seg -> 'coordinates' ELSE '[]'::jsonb END
) WITH ORDINALITY AS c(pt, pt_ord)
WHERE r.ride_id = '0c24901f-7c9e-4de5-9792-19f954b713a5'
GROUP BY 1, 2
ORDER BY 1, 2;

-- Q5b. The same for the raw device fixes, plus the accuracy the phone itself
--      reported. That accuracy — not our storage precision — is what decides
--      whether a fix can land on the wrong carriageway.
SELECT
    count(*)                                   AS fixes,
    min(h.accuracy)                            AS best_accuracy_m,
    round(avg(h.accuracy)::numeric, 1)         AS avg_accuracy_m,
    max(h.accuracy)                            AS worst_accuracy_m
FROM driver_location_history h
WHERE h.ride_id = '0c24901f-7c9e-4de5-9792-19f954b713a5'
  AND h.lat IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Q6. "I did not drive here" — classify a specific coordinate.
--
--     THIS IS THE ONE THAT MATTERS when a rider or driver points at a stretch
--     of the map. It answers which segment the point belongs to and, above
--     all, whether that segment is evidence or invention:
--
--       kind = 'inferred'  -> GAP FILL. No GPS witnessed it. The router was
--                             asked to connect two fixes and drew this. Look
--                             at gap_reason and segment_km.
--       kind = 'observed'  -> MAP MATCHING. Real fixes existed, but OSRM
--                             /match snapped them to this road. A wrong road
--                             here is a DIFFERENT defect from gap fill, and
--                             the gap-connector guards do not touch it --
--                             the levers are _osrm_radius (snap search
--                             radius, clamped 10-50 m) and _osrm_bearing.
--
--     Returns the 5 nearest route points to each target, so it answers even
--     when the coordinate was copied approximately.
-- ---------------------------------------------------------------------------
WITH target(label, lat, lng) AS (
    VALUES
        ('A', 50.417215, -104.645205),
        ('B', 50.417006, -104.644034)
),
route_point AS (
    SELECT
        s.seg_ord,
        c.pt_ord,
        s.seg ->> 'geometry_kind'          AS kind,
        s.seg ->> 'gap_reason'             AS gap_reason,
        s.seg ->> 'provider'               AS provider,
        (s.seg ->> 'distance_km')::numeric AS segment_km,
        (c.pt ->> 0)::double precision     AS lat,
        (c.pt ->> 1)::double precision     AS lng
    FROM ride_routes r
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(r.road_matched_segments) = 'array' THEN r.road_matched_segments ELSE '[]'::jsonb END
    ) WITH ORDINALITY AS s(seg, seg_ord)
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(s.seg -> 'coordinates') = 'array' THEN s.seg -> 'coordinates' ELSE '[]'::jsonb END
    ) WITH ORDINALITY AS c(pt, pt_ord)
    WHERE r.ride_id = '0c24901f-7c9e-4de5-9792-19f954b713a5'
)
SELECT
    t.label,
    p.kind,
    p.gap_reason,
    p.provider,
    p.segment_km,
    p.seg_ord    AS segment_no,
    p.pt_ord     AS point_no,
    p.lat,
    p.lng,
    round((2 * 6371000 * asin(sqrt(
        power(sin(radians(p.lat - t.lat) / 2), 2)
        + cos(radians(t.lat)) * cos(radians(p.lat)) * power(sin(radians(p.lng - t.lng) / 2), 2)
    )))::numeric, 1) AS metres_from_target
FROM target t
CROSS JOIN LATERAL (
    SELECT rp.*
    FROM route_point rp
    ORDER BY (rp.lat - t.lat) ^ 2 + ((rp.lng - t.lng) * cos(radians(t.lat))) ^ 2
    LIMIT 5
) p
ORDER BY t.label, metres_from_target;

-- Q6b. Was there ANY real GPS near that coordinate at all? If Q6 says
--      'inferred' and this returns nothing, the device genuinely recorded
--      nothing there and the stretch is pure gap fill.
SELECT
    t.label,
    h."timestamp",
    h.tracking_phase,
    h.lat,
    h.lng,
    h.accuracy,
    round((2 * 6371000 * asin(sqrt(
        power(sin(radians(h.lat - t.lat) / 2), 2)
        + cos(radians(t.lat)) * cos(radians(h.lat)) * power(sin(radians(h.lng - t.lng) / 2), 2)
    )))::numeric, 1) AS metres_from_target
FROM (VALUES ('A', 50.417215, -104.645205), ('B', 50.417006, -104.644034)) AS t(label, lat, lng)
JOIN driver_location_history h
  ON h.ride_id = '0c24901f-7c9e-4de5-9792-19f954b713a5'
 AND h.lat IS NOT NULL
 AND h.lat BETWEEN t.lat - 0.0025 AND t.lat + 0.0025      -- ~+/-275 m
 AND h.lng BETWEEN t.lng - 0.0040 AND t.lng + 0.0040      -- ~+/-280 m at 50N
ORDER BY t.label, metres_from_target;
