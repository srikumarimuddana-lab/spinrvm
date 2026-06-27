-- 198_wallet_txn_types_referral_fare_split.sql
-- Two wallet_transactions.type values are written by code but were never added
-- to the CHECK constraint (last set in migration 29), so the inserts fail with
-- Postgres 23514:
--   * 'referral_reward'  — rider referral payout (utils/referral_payout.py:372).
--     ACTIVELY failing in production: rewards rejected, the claim is marked
--     'failed' for manual reconciliation, and the balance increment is reversed
--     (utils/referral_payout.py:379-392) so no money is left unrecorded.
--   * 'fare_split_refund' — fare-split refund (routes/fare_split.py:452). Latent;
--     fails whenever a fare split is refunded.
--
-- This re-creates wallet_transactions_type_check with the full allowlist plus
-- both values. The new set is a SUPERSET of the existing one, so every existing
-- row stays valid and ADD CONSTRAINT validates instantly. No CONCURRENTLY, so it
-- runs inside the migrate.py transaction — the DROP+ADD is atomic (no window in
-- which the table has no type check).
--
-- After applying: re-queue the referral_payouts rows that hit THIS bug (status
-- 'failed' with no net credit — the increment was reversed). FIRST grep the logs
-- for "ledger write AND its reversal failed" and EXCLUDE any wallet/claim it
-- names (those left an unrecorded credit and would double-credit on re-process).
-- Then, scoped to the incident window (the referral-payout feature is recent, so
-- a short look-back covers it — widen only if needed):
--   UPDATE referral_payouts SET status = 'pending'
--    WHERE status = 'failed'
--      AND created_at >= NOW() - INTERVAL '7 days';
-- (Do NOT blanket-reset all 'failed' rows.) The next payout tick re-credits them
-- now that 'referral_reward' is allowed.
--
-- Rollback (only safe if no 'referral_reward'/'fare_split_refund' rows exist):
--   ALTER TABLE wallet_transactions DROP CONSTRAINT IF EXISTS wallet_transactions_type_check;
--   ALTER TABLE wallet_transactions ADD CONSTRAINT wallet_transactions_type_check
--       CHECK (type IN ('top_up','ride_payment','ride_refund','bonus','referral',
--           'cashout','fare_split_received','fare_split_sent','quest_reward',
--           'admin_credit','admin_debit'));

ALTER TABLE wallet_transactions DROP CONSTRAINT IF EXISTS wallet_transactions_type_check;

ALTER TABLE wallet_transactions
    ADD CONSTRAINT wallet_transactions_type_check
    CHECK (type IN (
        'top_up',
        'ride_payment',
        'ride_refund',
        'bonus',
        'referral',
        'referral_reward',
        'cashout',
        'fare_split_received',
        'fare_split_sent',
        'fare_split_refund',
        'quest_reward',
        'admin_credit',
        'admin_debit'
    ));
