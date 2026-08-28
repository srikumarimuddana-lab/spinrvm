-- 371_route_gap_latest_captures_fn.sql
--
-- Purpose:
--   Replace the per-ride N+1 in utils/route_gap_monitor.py's
--   _latest_capture_time(). That helper issued 1-2 serial
--   driver_location_history queries PER RIDE, for up to
--   MAX_ACTIVE_RIDES_PER_TICK (500) rides, every 15 seconds, on every replica
--   — up to ~1000 round-trips per tick to answer one question per ride.
--
--   This returns the newest accepted capture time for every requested ride in
--   a single call.
--
-- Why an RPC and not a batched `ride_id IN (...)` query:
--   The question is "newest row PER ride", which PostgREST cannot express.
--   A batched query ordered by captured_at DESC with a row cap would let the
--   busiest rides fill the cap and push a quiet ride's newest row past it,
--   making that ride look like it had no captures at all. That is exactly the
--   blindness the helper's own docstring records as a real incident (ride
--   SPR-PE7TTB: an 11-minute mid-trip outage went undetected). DISTINCT ON
--   answers it exactly, with no cap and no per-ride round-trip.
--
-- Semantics mirror the previous Python exactly:
--   - captured_at IS NOT NULL is filtered FIRST. Postgres sorts NULLs first
--     under ORDER BY ... DESC, so a single legacy WS-path breadcrumb with no
--     captured_at would otherwise win the DISTINCT ON and blind the monitor
--     for the whole ride.
--   - Falls back to the legacy `timestamp` column only for rides that have no
--     non-NULL captured_at at all (rides whose points are all v1-shaped) —
--     the same precedence the two sequential queries encoded.
--   - A ride with neither is simply absent from the result; the caller reads
--     that as None, exactly as the old "no rows" path did.
--
-- Types:
--   driver_location_history.ride_id is UUID, so the parameter is uuid[] and the
--   result key is cast ride_id::text (jsonb_object_agg requires a text key).
--   The caller passes Python strings; PostgREST coerces them on the way in.
--
-- Indexes:
--   Covered by idx_driver_location_history_ride_id_timestamp and the
--   captured_at index from the breadcrumb work. Both are (ride_id, <time>),
--   which is the exact DISTINCT ON key order, so this is an index scan per
--   ride group rather than a sort.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.route_gap_latest_captures(uuid[]);
--   ...and revert utils/route_gap_monitor.py to the per-ride helper. No
--   column, index or row is created or altered by this migration.

CREATE OR REPLACE FUNCTION public.route_gap_latest_captures(
    p_ride_ids uuid[]
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    WITH captured AS (
        SELECT DISTINCT ON (ride_id) ride_id, captured_at AS at
        FROM driver_location_history
        WHERE ride_id = ANY (p_ride_ids)
          AND captured_at IS NOT NULL
        ORDER BY ride_id, captured_at DESC
    ),
    legacy AS (
        SELECT DISTINCT ON (ride_id) ride_id, timestamp AS at
        FROM driver_location_history
        WHERE ride_id = ANY (p_ride_ids)
          AND timestamp IS NOT NULL
          AND ride_id NOT IN (SELECT ride_id FROM captured)
        ORDER BY ride_id, timestamp DESC
    ),
    merged AS (
        SELECT ride_id, at FROM captured
        UNION ALL
        SELECT ride_id, at FROM legacy
    )
    SELECT COALESCE(
        (SELECT jsonb_object_agg(ride_id::text, to_char(at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'))
         FROM merged),
        '{}'::jsonb
    );
$$;

COMMENT ON FUNCTION public.route_gap_latest_captures(uuid[]) IS
    'Newest non-NULL capture time per ride for the given ride ids, preferring captured_at and '
    'falling back to the legacy timestamp column. Returns {ride_id: iso8601_utc}; rides with '
    'neither are absent. Replaces a 1-2-query-per-ride N+1 in the 15s route-gap monitor '
    '(audit P2 #19). Rides absent from the result mean "no captures", same as the old no-rows path.';

-- Called from the backend (service role) only. The service role bypasses this
-- REVOKE by design; it exists so a leaked anon/authenticated key cannot mine
-- ride-level location timing.
REVOKE EXECUTE ON FUNCTION public.route_gap_latest_captures(uuid[]) FROM anon, authenticated;
