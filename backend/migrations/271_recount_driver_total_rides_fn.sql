-- 271_recount_driver_total_rides_fn.sql
--
-- Purpose:
--   The legacy booking importer resets drivers.total_rides to its invariant
--   (COUNT of completed rides, per migration 74) after inserting historical
--   rides. The Python implementation did this with two round-trips per driver
--   — a count SELECT plus an UPDATE — so a 64-driver import cost 128
--   sequential Supabase calls, 20-40s against a remote project.
--
--   That was tolerable for the CLI, which has no request deadline. It is not
--   tolerable for the admin bulk-import endpoint: a gateway timeout part-way
--   through the loop leaves rides and payouts written but total_rides updated
--   for only some drivers, and the operator has no signal which. This
--   function does the whole recount as ONE set-based statement, so it either
--   fully applies or fully rolls back.
--
--   Also removes the N+1 read pattern CLAUDE.md calls out as an SLA
--   anti-pattern (batch via .in_() / set-based SQL instead of looping).
--
-- Invariant preserved exactly: total_rides = COUNT(rides WHERE
--   driver_id = drivers.id AND status = 'completed'). Recomputing rather than
--   incrementing is what keeps a re-run idempotent — do not change this to a
--   delta update.
--
-- Drivers passed in with zero completed rides are set to 0, not skipped, so
-- the function is also a repair tool for drift.
--
-- Money-function safety: this writes a COUNTER, not money — but it is
-- SECURITY DEFINER (the importer runs under the service role and the column
-- is not writable by anon/authenticated), with a pinned search_path and
-- EXECUTE revoked from anon/authenticated, matching the convention in
-- migrations 159/161/162.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.recount_driver_total_rides(text[]);
--   (The Python fallback loop in booking_import_service.recount_drivers
--   continues to work with the function absent — no code rollback needed.)

CREATE OR REPLACE FUNCTION public.recount_driver_total_rides(
    p_driver_ids text[]
)
RETURNS integer
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    WITH counts AS (
        SELECT
            d.id AS driver_id,
            (
                SELECT COUNT(*)
                FROM rides r
                WHERE r.driver_id = d.id
                  AND r.status = 'completed'
            ) AS total
        FROM drivers d
        WHERE d.id = ANY(p_driver_ids)
    ), updated AS (
        UPDATE drivers d
           SET total_rides = c.total
          FROM counts c
         WHERE d.id = c.driver_id
           AND d.total_rides IS DISTINCT FROM c.total
        RETURNING 1
    )
    SELECT COALESCE((SELECT COUNT(*) FROM updated), 0)::integer;
$$;

REVOKE ALL ON FUNCTION public.recount_driver_total_rides(text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.recount_driver_total_rides(text[]) FROM anon, authenticated;

COMMENT ON FUNCTION public.recount_driver_total_rides(text[]) IS
    'Set-based reset of drivers.total_rides to COUNT(completed rides) for the given driver IDs. Used by the legacy booking importer; replaces a per-driver read+write loop so an import cannot leave counters half-updated. Returns the number of rows actually changed.';
