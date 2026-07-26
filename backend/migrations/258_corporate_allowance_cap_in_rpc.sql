-- Rollback: re-apply migration 248's body (the version without the ride_debit
-- ceiling guard). No schema change — this only replaces the function body.
--
-- Closes the corporate per-member allowance over-spend race. settle_corporate
-- computed allowance_debit = min(remaining, total) from a NON-locking read of
-- corporate_member_allowances.used, then called ride_debit. The RPC locked the
-- row but only guarded the master-wallet floor — it never checked used against
-- the allowance ceiling. Two rides for the same member settling concurrently
-- both read the same stale `used`, both decide the debit fits, and both apply
-- it: used ends up above `amount`, silently bypassing the per-employee cap.
--
-- Fix: enforce the ceiling INSIDE the locked RPC for ride_debit. The row is
-- already held FOR UPDATE, so the check is atomic against concurrent settles.
-- On breach it RAISEs 'allowance_cap_exceeded'; settle_corporate catches that
-- and routes the whole fare to the company master wallet (its existing fallback
-- path), so total dollars billed stay correct and only the per-member ceiling
-- is enforced. Unlimited allowances (amount IS NULL) are never capped.
--
-- CREATE OR REPLACE keeps the same signature and RETURNS TABLE — callers are
-- unaffected. Only the two changed spots vs 248 are the SELECT (also reads
-- amount) and the new guard after v_used_new is computed.

CREATE OR REPLACE FUNCTION corporate_allowance_apply_delta(
    p_wallet_id          UUID,
    p_allowance_id       UUID,
    p_member_id          UUID,
    p_type               TEXT,
    p_amount             NUMERIC(12,2),
    p_actor_user_id      UUID DEFAULT NULL,
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
SET search_path = public, pg_temp
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
