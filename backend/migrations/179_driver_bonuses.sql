-- Migration 179: driver_bonuses — payable bonus ledger for drivers
--
-- Quest rewards and DRIVER referral rewards are payable EARNINGS for a driver,
-- not a rider-style spendable wallet balance. Previously they were credited to
-- the `wallets` table (wallet_increment_balance), which the driver app has no
-- UI for and which is excluded from the driver's payable_balance / Stripe
-- payout — so the money was recorded but invisible and unpayable.
--
-- This append-only ledger lets those bonuses fold into get_driver_balance's
-- payable_balance (= ride earnings + bonuses - payouts), so they are paid out
-- by the existing platform->connect stripe.Transfer and surface in earnings /
-- payout history, exactly like ride earnings.
--
-- Append-only: rows are never updated or deleted. A clawback would be a new
-- negative-amount row (amount CHECK allows it via a separate reversal kind).
--
-- Rollback plan:
--   DROP TABLE IF EXISTS driver_bonuses;
--   Disable the quest/referral driver-credit paths (feature flags) BEFORE
--   dropping, or new claims will error on the missing table.

CREATE TABLE IF NOT EXISTS driver_bonuses (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    driver_id   UUID NOT NULL,            -- drivers.id (backend queries)
    user_id     UUID NOT NULL,            -- drivers.user_id (RLS owner)
    amount      NUMERIC(10, 2) NOT NULL,  -- payable amount in CAD (Decimal, never float)
    kind        TEXT NOT NULL CHECK (kind IN ('quest', 'referral', 'adjustment')),
    source_id   UUID,                     -- quest_progress.id / referral_payouts.id (idempotency)
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Payable-balance + history reads are by driver_id, newest first.
CREATE INDEX IF NOT EXISTS idx_driver_bonuses_driver ON driver_bonuses (driver_id);
CREATE INDEX IF NOT EXISTS idx_driver_bonuses_driver_created ON driver_bonuses (driver_id, created_at DESC);
-- RLS predicate column — index so any auth-key / PostgREST read (or a future
-- user_id-filtered query) evaluates the policy without a full table scan.
CREATE INDEX IF NOT EXISTS idx_driver_bonuses_user ON driver_bonuses (user_id);

-- Idempotency: at most one bonus row per (kind, source). Prevents a retried
-- claim/payout from double-crediting. Partial — source_id may be NULL for
-- manual adjustments, which are not deduped.
CREATE UNIQUE INDEX IF NOT EXISTS uq_driver_bonuses_kind_source
    ON driver_bonuses (kind, source_id) WHERE source_id IS NOT NULL;

-- RLS: a driver reads only their own bonuses. The backend service role writes
-- (bypasses RLS by design); the anon/auth key never writes this ledger.
ALTER TABLE driver_bonuses ENABLE ROW LEVEL SECURITY;

CREATE POLICY driver_bonuses_select_own ON driver_bonuses
    FOR SELECT USING (auth.uid() = user_id);
-- No INSERT/UPDATE/DELETE policies: append-only, service-role-written only.
