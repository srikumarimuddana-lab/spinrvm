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
