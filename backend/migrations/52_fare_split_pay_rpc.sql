-- Migration 52: Atomic fare split payment RPC
--
-- Atomically deducts from a rider's wallet and marks the fare-split
-- participant as "paid" in a single transaction. Without this, a crash
-- between the wallet debit and the status update would leave the wallet
-- balance reduced but the participant still showing "accepted".

CREATE OR REPLACE FUNCTION fare_split_pay_share(
    p_wallet_id      UUID,
    p_participant_id UUID,
    p_amount         NUMERIC
)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    v_balance NUMERIC;
BEGIN
    -- Lock the wallet row to prevent concurrent debits
    SELECT balance INTO v_balance
    FROM wallets
    WHERE id = p_wallet_id
    FOR UPDATE;

    IF v_balance IS NULL THEN
        RAISE EXCEPTION 'wallet_not_found';
    END IF;

    IF v_balance < p_amount THEN
        RAISE EXCEPTION 'insufficient_funds';
    END IF;

    UPDATE wallets
    SET balance    = balance - p_amount,
        updated_at = NOW()
    WHERE id = p_wallet_id;

    UPDATE fare_split_participants
    SET status  = 'paid',
        paid_at = NOW()
    WHERE id = p_participant_id;

    RETURN v_balance - p_amount;
END;
$$;
