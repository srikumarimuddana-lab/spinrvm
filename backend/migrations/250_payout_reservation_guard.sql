-- Migration 250: payout reservation guard — prevent concurrent in-flight payouts
--
-- rollback: DROP INDEX IF EXISTS idx_payouts_one_inflight_per_driver;
--
-- Finding 4 (WS-7): the standard payout path reads payable_balance, checks
-- amount <= balance, then calls Stripe Transfer.create and *only then* inserts
-- the payouts row. Two concurrent requests both pass the balance check against
-- the same snapshot and both transfer — a TOCTOU double-withdraw.
--
-- Fix: the payout row is now inserted with status='reserved' BEFORE the Stripe
-- Transfer call. This partial unique index ensures at most one in-flight payout
-- per driver at any time. A second concurrent request's INSERT violates the
-- unique constraint and is rejected, eliminating the race window entirely.
--
-- The index covers all non-terminal statuses: reserved (pre-transfer claim),
-- pending (legacy standard-payout status), and transfer_completed (instant
-- payout: transfer done, payout step pending). Terminal statuses (completed,
-- failed, reversed, stranded) are excluded so they don't block future payouts.

CREATE UNIQUE INDEX IF NOT EXISTS idx_payouts_one_inflight_per_driver
    ON payouts (driver_id)
    WHERE status IN ('reserved', 'pending', 'transfer_completed');
