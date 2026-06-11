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
-- this file statement-by-statement in autocommit mode — keep this file free
-- of DO blocks and multi-statement constructs.
--
-- Rollback plan: DROP INDEX CONCURRENTLY ride_offers_one_accepted_per_ride;
-- =============================================================================

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ride_offers_one_accepted_per_ride
    ON ride_offers (ride_id)
    WHERE status = 'accepted';
