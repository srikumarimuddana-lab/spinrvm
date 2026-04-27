-- Migration: 52_loyalty_dedup_constraint.sql
-- Purpose : Add UNIQUE index on loyalty_transactions(user_id, reference_id)
--           WHERE type = 'ride_earned' so the DB enforces exactly-once point
--           awards even under concurrent earn_points_for_ride calls.  Fixes P1-1.
-- Rollback: DROP INDEX IF EXISTS ux_loyalty_txn_user_ride;
-- Forward-compatible: partial index; existing data must not already contain
--           duplicates — backfill script deduplicates before this runs.

CREATE UNIQUE INDEX IF NOT EXISTS ux_loyalty_txn_user_ride
    ON loyalty_transactions(user_id, reference_id)
    WHERE type = 'ride_earned';
