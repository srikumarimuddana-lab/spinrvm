-- 199_wallet_txn_type_referral_bonus.sql
-- Follow-up to migration 198. The referral payout pays TWO sides:
--   * referrer → wallet_transactions type 'referral_reward' (added in 198), and
--   * referee  → wallet_transactions type 'referral_bonus'  (referral_payout.py:264).
-- 198 missed 'referral_bonus', so a rider REFEREE's reward still fails the
-- wallet_transactions_type_check constraint (Postgres 23514) even after 198 — and
-- because referrer + referee are credited in one try/except (referral_payout.py:
-- 259-271), a failure on EITHER side fails the whole claim and reverses both.
-- So without this, applying 198 alone fixes nothing for two-sided referrals.
--
-- This re-creates the constraint with the full migration-198 allowlist PLUS
-- 'referral_bonus'. Strict superset of 198 → every existing row stays valid,
-- ADD CONSTRAINT validates instantly. No CONCURRENTLY → atomic in the migrate.py
-- transaction (same precedent as 29 / 198). Safe to apply whether or not 198 has
-- run (DROP IF EXISTS + ADD the complete set).
--
-- After applying BOTH 198 and 199, re-queue the failed referral claims — see the
-- scoped UPDATE in 198's header comment (the next payout tick credits both sides).
--
-- Rollback (only if no 'referral_bonus' rows exist; reverts to the 198 set):
--   ALTER TABLE wallet_transactions DROP CONSTRAINT IF EXISTS wallet_transactions_type_check;
--   ALTER TABLE wallet_transactions ADD CONSTRAINT wallet_transactions_type_check
--       CHECK (type IN ('top_up','ride_payment','ride_refund','bonus','referral',
--           'referral_reward','cashout','fare_split_received','fare_split_sent',
--           'fare_split_refund','quest_reward','admin_credit','admin_debit'));

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
        'referral_bonus',
        'cashout',
        'fare_split_received',
        'fare_split_sent',
        'fare_split_refund',
        'quest_reward',
        'admin_credit',
        'admin_debit'
    ));
