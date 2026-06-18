-- Migration 171: referral_payouts — idempotent ledger of paid referral rewards
--
-- Both the rider and driver referral programs reward a referrer once their
-- referee reaches a ride threshold (driver: 10 rides → $10; rider: 1 ride →
-- $5 each side). The payout background loop needs an idempotency anchor so a
-- referral is rewarded EXACTLY once even though the loop runs on every replica
-- and retries. This table is that anchor: one row per referee, claimed by an
-- INSERT whose UNIQUE(referee_user_id) makes a concurrent/duplicate claim fail
-- (the loop treats the unique-violation as "already claimed, skip").
--
-- Money columns are numeric (never float). This is a financial ledger: the FKs
-- are ON DELETE SET NULL (NOT cascade) so the payout record survives a user
-- deletion/anonymisation for reconciliation and tax purposes, while the PII
-- link is severed per PIPEDA.
--
-- Rollback (coordinated — do in this order):
--   1. Set REFERRAL_PAYOUTS_ENABLED=false and redeploy so the payout loop stops
--      touching the table (the loop is wired in core/lifespan.py).
--   2. DROP TABLE IF EXISTS referral_payouts;
--   NOTE: wallet credits already issued are NOT reversed by dropping the table —
--   any referrer/referee already paid keeps the credit (manual cleanup if needed).

CREATE TABLE IF NOT EXISTS referral_payouts (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- One payout per referred user — this UNIQUE is the idempotency claim.
    -- Nullable + SET NULL so the financial row is retained (PII severed) if the
    -- user is later deleted; multiple NULLs are allowed by the UNIQUE index.
    -- users.id is TEXT in this schema, so the FK columns must be TEXT too.
    referee_user_id   text UNIQUE REFERENCES users(id) ON DELETE SET NULL,
    referrer_user_id  text REFERENCES users(id) ON DELETE SET NULL,
    kind              text NOT NULL CHECK (kind IN ('rider', 'driver')),
    referrer_reward   numeric(10, 2) NOT NULL DEFAULT 0,
    referee_reward    numeric(10, 2) NOT NULL DEFAULT 0,
    -- 'processing' is the in-flight claim (set on the winning INSERT so a second
    -- replica can't double-pay); finalised to 'paid' or 'failed'.
    status            text NOT NULL DEFAULT 'processing' CHECK (status IN ('processing', 'paid', 'failed')),
    paid_at           timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- Loop lists a referrer's payouts and the user-facing screen may show them.
CREATE INDEX IF NOT EXISTS idx_referral_payouts_referrer
    ON referral_payouts (referrer_user_id);

-- Supports the stale-'processing' reclaim sweep and future status-filtered reads.
CREATE INDEX IF NOT EXISTS idx_referral_payouts_status
    ON referral_payouts (status);

-- RLS: backend (service role) bypasses RLS and performs ALL writes. Clients may
-- read only their own rows; client writes are explicitly denied (never FOR ALL).
ALTER TABLE referral_payouts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS referral_payouts_select_own ON referral_payouts;
CREATE POLICY referral_payouts_select_own ON referral_payouts
    FOR SELECT
    USING (auth.uid()::text = referrer_user_id OR auth.uid()::text = referee_user_id);

-- Explicit client-write denials (service role bypasses these by design).
DROP POLICY IF EXISTS referral_payouts_no_insert ON referral_payouts;
CREATE POLICY referral_payouts_no_insert ON referral_payouts
    FOR INSERT WITH CHECK (false);

DROP POLICY IF EXISTS referral_payouts_no_update ON referral_payouts;
CREATE POLICY referral_payouts_no_update ON referral_payouts
    FOR UPDATE USING (false);

DROP POLICY IF EXISTS referral_payouts_no_delete ON referral_payouts;
CREATE POLICY referral_payouts_no_delete ON referral_payouts
    FOR DELETE USING (false);
