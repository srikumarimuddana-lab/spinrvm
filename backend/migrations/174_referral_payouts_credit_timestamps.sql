-- Migration 174: per-side credit timestamps on referral_payouts
--
-- A referral reward credits two wallets in sequence — the referrer, then (rider
-- referrals only) the referee. If the referrer credit lands but the referee
-- credit then fails, the payout loop marks the claim 'failed' and stops (no
-- retry, to avoid double-crediting the referrer). Today a 'failed' (or stale
-- 'processing') row carries no record of WHICH side was actually paid, so
-- reconciliation can't tell "referrer paid, referee owed" apart from "neither
-- paid" without a manual wallet-ledger cross-check — and guessing wrong
-- double-pays someone.
--
-- These two timestamps make the split-credit state explicit:
--   referrer_credited_at set, referee_credited_at NULL → pay only the referee
--   both NULL                                          → neither side paid
--   both set                                           → fully paid (status='paid')
-- The payout loop writes each immediately after its credit succeeds, so the row
-- reflects reality even if a later step crashes.
--
-- Additive + nullable → forward-compatible with in-flight traffic; no backfill
-- (existing 'paid' rows predate the split-credit tracking and are reconciled by
-- their status alone).
--
-- Rollback:
--   ALTER TABLE referral_payouts
--     DROP COLUMN IF EXISTS referrer_credited_at,
--     DROP COLUMN IF EXISTS referee_credited_at;

ALTER TABLE referral_payouts
    ADD COLUMN IF NOT EXISTS referrer_credited_at timestamptz;
ALTER TABLE referral_payouts
    ADD COLUMN IF NOT EXISTS referee_credited_at  timestamptz;
