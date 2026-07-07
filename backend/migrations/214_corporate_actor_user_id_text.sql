-- ============================================================
-- Migration 214: corporate_wallet_transactions.actor_user_id → TEXT
--                + widen p_actor_user_id (UUID → TEXT) on the two
--                  corporate money RPCs
--
-- migration-override-ok: this migration intentionally redefines
-- corporate_wallet_apply_delta (first defined in 28) and
-- corporate_allowance_apply_delta (first defined in 29) to widen the
-- p_actor_user_id parameter UUID → TEXT. Bodies are copied verbatim from the
-- current definition in migration 203 — the redefinition is the whole point of
-- the fix, not an accidental collision (same pattern 203 used to add SECURITY
-- DEFINER). See the DROP + CREATE OR REPLACE + re-grant sequence below.
--
-- Same bug class as migration 213 (kyb_reviewed_by). The admin manual
-- wallet-adjust flow (POST /api/admin/corporate-accounts/{id}/wallet/adjust →
-- corporate_wallet.py manual_adjust) records the PLATFORM admin as the actor:
-- current_admin["id"] is a string ('admin-001'), not a users.id UUID (admin
-- identity lives in the JWT, not the users table — CLAUDE.md "JWT trust model").
-- Migration 27 declared corporate_wallet_transactions.actor_user_id as UUID and
-- the RPCs took p_actor_user_id UUID, so an admin adjustment would fail with
-- Postgres 22P02 "invalid input syntax for type uuid: 'admin-001'" — exactly the
-- error 213 fixed for KYB review.
--
-- Fix: widen actor_user_id (and the RPC parameter) to TEXT. Consumer/company
-- flows that pass a real users.id keep working — a UUID is a valid TEXT value.
--
-- Existing actor_user_id rows hold UUID-shaped strings (ride debits, company
-- admin adjustments); actor_user_id::text casts them losslessly. No FK, index,
-- or CHECK references the column.
--
-- LOCK NOTE (apply in a low-traffic window): UUID → TEXT is NOT a
-- binary-coercible cast, so step 1 performs a FULL TABLE REWRITE of
-- corporate_wallet_transactions under an ACCESS EXCLUSIVE lock — corporate ride
-- debits, top-ups, refunds, and allowance grants/resets all INSERT into this
-- ledger via the two RPCs below and will block for the rewrite's duration.
-- Corporate B2B (migration 27) is a newer surface, so this ledger is expected
-- to be small and the rewrite sub-second; before a prod apply, confirm the row
-- count and that the rewrite fits the 30s migration-apply SLA. If the ledger has
-- grown large, switch to the online pattern (add actor_user_id_new TEXT, backfill
-- in batches, then rename/swap) instead of this in-place ALTER.
--
-- The RPC parameter type is part of the function signature, so CREATE OR REPLACE
-- would add a second overload rather than replace. DROP the old UUID-signature
-- functions first, then recreate with p_actor_user_id TEXT. Bodies are copied
-- VERBATIM from migration 203 (the current definition) — only the actor param
-- type changes; SECURITY DEFINER / SET search_path and the INSERT logic are
-- unchanged. Dropping a function drops its grants, so the migration-203 EXECUTE
-- lockdown (REVOKE PUBLIC/anon/authenticated, GRANT service_role) is re-applied
-- for the new signatures at the end.
--
-- SAFE TO RE-RUN: DROP ... IF EXISTS on the old signatures is a no-op after the
-- first apply; CREATE OR REPLACE on the new signatures is idempotent; the column
-- ALTER is a no-op once actor_user_id is already TEXT.
--
-- Rollback: (only if no non-UUID actor id was written)
--   -- restore the migration-203 UUID-signature definitions, then:
--   ALTER TABLE corporate_wallet_transactions
--       ALTER COLUMN actor_user_id TYPE UUID USING actor_user_id::uuid;
-- ============================================================

-- 1. Widen the ledger column.
ALTER TABLE corporate_wallet_transactions
    ALTER COLUMN actor_user_id TYPE TEXT USING actor_user_id::text;

-- 2. Drop the old UUID-signature RPCs (signatures verbatim from migration 203).
DROP FUNCTION IF EXISTS corporate_wallet_apply_delta(
    UUID, TEXT, TEXT, NUMERIC, UUID, UUID, TEXT, UUID, TEXT, NUMERIC);
DROP FUNCTION IF EXISTS corporate_allowance_apply_delta(
    UUID, UUID, UUID, TEXT, NUMERIC, UUID, TEXT, NUMERIC);

-- ============================================================
-- 3. corporate_wallet_apply_delta — body verbatim from migration 203,
--    p_actor_user_id UUID → TEXT
-- ============================================================
CREATE OR REPLACE FUNCTION corporate_wallet_apply_delta(
    p_wallet_id          UUID,
    p_scope              TEXT,
    p_type               TEXT,
    p_delta              NUMERIC(12,2),
    p_ride_id            UUID DEFAULT NULL,
    p_member_id          UUID DEFAULT NULL,
    p_stripe_pi          TEXT DEFAULT NULL,
    p_actor_user_id      TEXT DEFAULT NULL,
    p_notes              TEXT DEFAULT NULL,
    p_floor              NUMERIC(12,2) DEFAULT NULL
)
RETURNS TABLE(transaction_id UUID, balance_after NUMERIC(12,2))
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    v_current   NUMERIC(12,2);
    v_new       NUMERIC(12,2);
    v_txn_id    UUID;
BEGIN
    -- Idempotency short-circuit: if stripe_payment_intent_id already in ledger, no-op.
    IF p_stripe_pi IS NOT NULL THEN
        SELECT wt.id, wt.balance_after INTO v_txn_id, v_new
        FROM corporate_wallet_transactions wt
        WHERE wt.stripe_payment_intent_id = p_stripe_pi
        LIMIT 1;
        IF FOUND THEN
            transaction_id := v_txn_id;
            balance_after  := v_new;
            RETURN NEXT;
            RETURN;
        END IF;
    END IF;

    SELECT balance INTO v_current
    FROM corporate_wallets
    WHERE id = p_wallet_id
    FOR UPDATE;

    IF v_current IS NULL THEN
        RAISE EXCEPTION 'wallet not found: %', p_wallet_id;
    END IF;

    v_new := v_current + p_delta;

    -- Enforce soft-negative floor (master-scope only; member scope checked by caller).
    IF p_scope = 'master' AND p_floor IS NOT NULL AND v_new < p_floor THEN
        RAISE EXCEPTION 'wallet_below_floor: new=% floor=%', v_new, p_floor;
    END IF;

    UPDATE corporate_wallets
    SET balance = v_new, updated_at = now()
    WHERE id = p_wallet_id;

    INSERT INTO corporate_wallet_transactions
        (wallet_id, scope, type, amount, balance_after, ride_id, member_id,
         stripe_payment_intent_id, actor_user_id, notes)
    VALUES
        (p_wallet_id, p_scope, p_type, p_delta, v_new, p_ride_id, p_member_id,
         p_stripe_pi, p_actor_user_id, p_notes)
    RETURNING id INTO v_txn_id;

    transaction_id := v_txn_id;
    balance_after  := v_new;
    RETURN NEXT;
END
$$;

-- ============================================================
-- 4. corporate_allowance_apply_delta — body verbatim from migration 203,
--    p_actor_user_id UUID → TEXT
-- ============================================================
CREATE OR REPLACE FUNCTION corporate_allowance_apply_delta(
    p_wallet_id          UUID,
    p_allowance_id       UUID,
    p_member_id          UUID,
    p_type               TEXT,
    p_amount             NUMERIC(12,2),
    p_actor_user_id      TEXT DEFAULT NULL,
    p_notes              TEXT DEFAULT NULL,
    p_floor              NUMERIC(12,2) DEFAULT NULL
)
RETURNS TABLE(
    master_txn_id        UUID,
    member_txn_id        UUID,
    master_balance_after NUMERIC(12,2),
    allowance_used_after NUMERIC(12,2)
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    v_master_balance NUMERIC(12,2);
    v_master_new     NUMERIC(12,2);
    v_used           NUMERIC(12,2);
    v_used_new       NUMERIC(12,2);
    v_master_delta   NUMERIC(12,2);
    v_used_delta     NUMERIC(12,2);
    v_master_txn     UUID;
    v_member_txn     UUID;
BEGIN
    IF p_type NOT IN ('allowance_grant','allowance_reset','allowance_rollback') THEN
        RAISE EXCEPTION 'invalid allowance type: %', p_type;
    END IF;
    IF p_amount < 0 THEN
        RAISE EXCEPTION 'amount must be non-negative';
    END IF;

    -- Deterministic lock order (prevents deadlock): master wallet first, then allowance.
    SELECT balance INTO v_master_balance
    FROM corporate_wallets
    WHERE id = p_wallet_id
    FOR UPDATE;
    IF v_master_balance IS NULL THEN
        RAISE EXCEPTION 'wallet not found: %', p_wallet_id;
    END IF;

    SELECT used INTO v_used
    FROM corporate_member_allowances
    WHERE id = p_allowance_id
    FOR UPDATE;
    IF v_used IS NULL THEN
        RAISE EXCEPTION 'allowance not found: %', p_allowance_id;
    END IF;

    -- Map type → (master delta, used delta).
    -- grant:    master -p_amount, used -p_amount  (usage counter decreases → more available)
    -- reset:    master 0,         used -v_used    (zero out current usage, no master move)
    -- rollback: master +p_amount, used +p_amount  (undo a prior grant)
    IF p_type = 'allowance_grant' THEN
        v_master_delta := -p_amount;
        v_used_delta   := -p_amount;
    ELSIF p_type = 'allowance_reset' THEN
        v_master_delta := 0;
        v_used_delta   := -v_used;
    ELSE
        v_master_delta := p_amount;
        v_used_delta   := p_amount;
    END IF;

    v_master_new := v_master_balance + v_master_delta;
    v_used_new   := v_used + v_used_delta;

    -- Floor check on master only; allowance soft-negative is governed by master's floor.
    IF p_floor IS NOT NULL AND v_master_new < p_floor THEN
        RAISE EXCEPTION 'wallet_below_floor: new=% floor=%', v_master_new, p_floor;
    END IF;

    UPDATE corporate_wallets
    SET balance = v_master_new, updated_at = now()
    WHERE id = p_wallet_id;

    UPDATE corporate_member_allowances
    SET used = v_used_new, updated_at = now()
    WHERE id = p_allowance_id;

    INSERT INTO corporate_wallet_transactions
        (wallet_id, scope, type, amount, balance_after, member_id, actor_user_id, notes)
    VALUES
        (p_wallet_id, 'master', p_type, v_master_delta, v_master_new,
         p_member_id, p_actor_user_id, p_notes)
    RETURNING id INTO v_master_txn;

    INSERT INTO corporate_wallet_transactions
        (wallet_id, scope, type, amount, balance_after, member_id, actor_user_id, notes)
    VALUES
        (p_wallet_id, 'member:' || p_member_id::text, p_type, v_used_delta, v_used_new,
         p_member_id, p_actor_user_id, p_notes)
    RETURNING id INTO v_member_txn;

    master_txn_id        := v_master_txn;
    member_txn_id        := v_member_txn;
    master_balance_after := v_master_new;
    allowance_used_after := v_used_new;
    RETURN NEXT;
END
$$;

-- ============================================================
-- 5. Re-apply the migration-203 EXECUTE lockdown for the NEW signatures.
--    DROP dropped the old grants; without this any anon/authenticated client
--    could call these SECURITY DEFINER money functions directly.
-- ============================================================
REVOKE EXECUTE ON FUNCTION corporate_wallet_apply_delta(UUID, TEXT, TEXT, NUMERIC, UUID, UUID, TEXT, TEXT, TEXT, NUMERIC)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION corporate_wallet_apply_delta(UUID, TEXT, TEXT, NUMERIC, UUID, UUID, TEXT, TEXT, TEXT, NUMERIC)
    TO service_role;

REVOKE EXECUTE ON FUNCTION corporate_allowance_apply_delta(UUID, UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, NUMERIC)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION corporate_allowance_apply_delta(UUID, UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, NUMERIC)
    TO service_role;
