-- Rollback: DROP FUNCTION IF EXISTS corporate_allowance_apply_delta(UUID,
-- UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, NUMERIC); then re-apply migration
-- 258's CREATE OR REPLACE FUNCTION body verbatim (p_actor_user_id back to
-- UUID DEFAULT NULL) plus its implicit EXECUTE grants. No schema/table
-- change — this only replaces the function signature + body.
--
-- Bug: migration 214 widened corporate_wallet_transactions.actor_user_id and
-- the corporate_allowance_apply_delta RPC's p_actor_user_id parameter from
-- UUID to TEXT, because the admin manual wallet-adjust flow records a
-- PLATFORM ADMIN id (e.g. "admin-001", a non-UUID string) as the actor --
-- passing that into a UUID parameter raises Postgres 22P02
-- (invalid_text_representation), the same failure class as the historical
-- kyb_reviewed_by bug.
--
-- Migrations 248 (corporate_allowance_ride_debit) and 258 (allowance-cap
-- guard) both re-declared corporate_allowance_apply_delta via CREATE OR
-- REPLACE FUNCTION with p_actor_user_id back at UUID DEFAULT NULL, silently
-- undoing 214's fix for this specific RPC (the corporate_wallet_transactions
-- table column itself was never re-narrowed -- only this function's
-- parameter type regressed). Any admin-actor call into ride_debit/
-- ride_debit_reversal/allowance_grant/allowance_reset via this RPC would
-- 22P02 on a non-UUID actor id.
--
-- Fix: re-apply 258's full function body verbatim, with only the
-- p_actor_user_id parameter type changed back to TEXT. CREATE OR REPLACE
-- keeps the same RETURNS TABLE shape and callers are unaffected.
--
-- CREATE OR REPLACE FUNCTION only replaces an existing function when its
-- argument-TYPE signature matches exactly -- a changed parameter type
-- creates a new overload alongside the old one rather than replacing it.
-- DROP the old UUID-signature overload first (as migration 214 did for
-- this exact scenario), or the buggy UUID overload stays live and
-- PostgREST RPC calls risk resolving to it (or an ambiguous-overload
-- PGRST203) instead of the fixed TEXT one.
DROP FUNCTION IF EXISTS corporate_allowance_apply_delta(
    UUID, UUID, UUID, TEXT, NUMERIC, UUID, TEXT, NUMERIC);

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
    v_cap            NUMERIC(12,2);
    v_master_delta   NUMERIC(12,2);
    v_used_delta     NUMERIC(12,2);
    v_master_txn     UUID;
    v_member_txn     UUID;
BEGIN
    IF p_type NOT IN ('allowance_grant','allowance_reset','allowance_rollback','ride_debit','ride_debit_reversal') THEN
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

    -- Read the usage counter AND the ceiling under the same row lock so the
    -- cap check below is atomic against a concurrent settle of the same member.
    SELECT used, amount INTO v_used, v_cap
    FROM corporate_member_allowances
    WHERE id = p_allowance_id
    FOR UPDATE;
    IF v_used IS NULL THEN
        RAISE EXCEPTION 'allowance not found: %', p_allowance_id;
    END IF;

    IF p_type = 'allowance_grant' THEN
        v_master_delta := -p_amount;
        v_used_delta   := -p_amount;
    ELSIF p_type = 'allowance_reset' THEN
        v_master_delta := 0;
        v_used_delta   := -v_used;
    ELSIF p_type = 'ride_debit' THEN
        v_master_delta := -p_amount;
        v_used_delta   := p_amount;
    ELSIF p_type = 'ride_debit_reversal' THEN
        v_master_delta := p_amount;
        v_used_delta   := -p_amount;
    ELSE
        v_master_delta := p_amount;
        v_used_delta   := p_amount;
    END IF;

    v_master_new := v_master_balance + v_master_delta;
    v_used_new   := v_used + v_used_delta;

    -- Per-member ceiling — ride_debit only, and only for capped (non-unlimited)
    -- allowances. This is the atomic gate that the non-locking application-side
    -- min(remaining, total) split could not provide. On breach the caller routes
    -- the fare to the master wallet instead.
    IF p_type = 'ride_debit' AND v_cap IS NOT NULL AND v_used_new > v_cap THEN
        RAISE EXCEPTION 'allowance_cap_exceeded: used_new=% cap=%', v_used_new, v_cap;
    END IF;

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

-- Re-apply the migration-214/203 EXECUTE lockdown for the new signature.
-- The DROP above dropped the old (UUID p_actor_user_id) signature's grants;
-- without this, the new TEXT-signature function defaults to EXECUTE granted
-- to PUBLIC, exposing this SECURITY DEFINER wallet/allowance-mutating
-- function to anon/authenticated clients via PostgREST.
REVOKE EXECUTE ON FUNCTION corporate_allowance_apply_delta(UUID, UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, NUMERIC)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION corporate_allowance_apply_delta(UUID, UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, NUMERIC)
    TO service_role;
