-- 241_users_referral_code_partial_index.sql
-- Partial index backing the referral payout loop's candidate query
-- (users WHERE referral_code_used IS NOT NULL — utils/referral_payout._tick),
-- which replaced the previous scan-everything-and-filter-in-Python approach.
-- Partial: only referred users are indexed, so the index stays tiny relative
-- to the users table and non-referred signups never touch it.
--
-- Rollback plan:
-- DROP INDEX IF EXISTS idx_users_referral_code_used_not_null;
-- (Query still works without it — reverts to a sequential scan.)

CREATE INDEX IF NOT EXISTS idx_users_referral_code_used_not_null
    ON public.users (referral_code_used)
    WHERE referral_code_used IS NOT NULL;
