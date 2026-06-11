-- Migration 143: at most one accepted offer per ride (double-dispatch guard).
-- =============================================================================
-- Defense-in-depth behind the atomic accept filter in routes/drivers.py
-- (status=searching AND driver_id IS NULL). Safe because a ride never returns
-- to 'searching' after an accept: the offer-timeout revert only fires
-- pre-accept (offer still 'pending'), and a post-accept driver cancel
-- terminates the ride ('cancelled') rather than re-dispatching.
--
-- Depends on migration 142's repair step having demoted any pre-existing
-- duplicate accepted offers; the build fails loudly if duplicates remain.
--
-- CONCURRENTLY keeps ride_offers (written on every dispatch event) unlocked
-- during the build. The migration runner detects CONCURRENTLY and executes
-- this file statement-by-statement in autocommit mode.
--
-- Interrupted-build safety: if a prior run was interrupted after Postgres
-- started building the index, Postgres marks it INVALID. IF NOT EXISTS would
-- skip the rebuild, leaving a non-enforcing placeholder. The DO block below
-- detects and drops any invalid leftover before the CREATE runs.
-- Plain DROP (not CONCURRENTLY) is safe for an invalid index — the planner
-- never uses it, so no read threads are blocked.
--
-- Rollback plan: DROP INDEX CONCURRENTLY ride_offers_one_accepted_per_ride;
-- =============================================================================

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_index pi
        JOIN pg_class pc ON pc.oid = pi.indexrelid
        WHERE pc.relname = 'ride_offers_one_accepted_per_ride'
          AND NOT pi.indisvalid
    ) THEN
        EXECUTE 'DROP INDEX ride_offers_one_accepted_per_ride';
    END IF;
END $$;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ride_offers_one_accepted_per_ride
    ON ride_offers (ride_id)
    WHERE status = 'accepted';
