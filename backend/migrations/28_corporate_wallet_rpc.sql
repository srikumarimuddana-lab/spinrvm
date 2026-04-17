-- Atomic wallet movement with row-lock + ledger insert.
-- Called from backend/services/corporate_wallet_service.py.
CREATE OR REPLACE FUNCTION corporate_wallet_apply_delta(
    p_wallet_id          UUID,
    p_scope              TEXT,
    p_type               TEXT,
    p_delta              NUMERIC(12,2),
    p_ride_id            UUID DEFAULT NULL,
    p_member_id          UUID DEFAULT NULL,
    p_stripe_pi          TEXT DEFAULT NULL,
    p_actor_user_id      UUID DEFAULT NULL,
    p_notes              TEXT DEFAULT NULL,
    p_floor              NUMERIC(12,2) DEFAULT NULL
)
RETURNS TABLE(transaction_id UUID, balance_after NUMERIC(12,2))
LANGUAGE plpgsql
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
