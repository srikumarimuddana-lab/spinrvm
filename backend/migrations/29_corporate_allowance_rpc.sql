-- ============================================================
-- Migration 29: Corporate allowance apply-delta RPC
--
-- Atomic allowance movement: moves money from master wallet into / out of
-- a member's allowance (or resets usage). Locks master wallet + allowance
-- rows in deterministic order and writes paired ledger entries.
--
-- Called from backend/services/corporate_allowance_service.py.
--
-- SAFE TO RE-RUN (CREATE OR REPLACE).
-- ============================================================

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
