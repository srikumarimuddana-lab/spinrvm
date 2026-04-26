-- Atomic wallet RPCs to eliminate TOCTOU race conditions (P0-4, P0-5, P0-6)
-- All three functions use row-level locking so concurrent calls serialize correctly.

-- P0-4: Atomic top-up — avoids read-compute-write race on wallet balance.
CREATE OR REPLACE FUNCTION wallet_increment_balance(
    p_wallet_id UUID,
    p_amount    NUMERIC
)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    v_new_balance NUMERIC;
BEGIN
    UPDATE wallets
       SET balance    = balance + p_amount,
           updated_at = NOW()
     WHERE id = p_wallet_id
    RETURNING balance INTO v_new_balance;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'wallet not found: %', p_wallet_id;
    END IF;

    RETURN v_new_balance;
END;
$$;

-- P0-5: Atomic pay — locks wallet row, checks balance, debits wallet and marks
-- ride paid in a single transaction so neither half can succeed without the other.
CREATE OR REPLACE FUNCTION wallet_pay_for_ride(
    p_wallet_id UUID,
    p_ride_id   UUID,
    p_amount    NUMERIC
)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    v_balance     NUMERIC;
    v_new_balance NUMERIC;
BEGIN
    SELECT balance INTO v_balance
      FROM wallets
     WHERE id = p_wallet_id
       FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'wallet not found: %', p_wallet_id;
    END IF;

    IF v_balance < p_amount THEN
        RAISE EXCEPTION 'insufficient_funds';
    END IF;

    UPDATE wallets
       SET balance    = balance - p_amount,
           updated_at = NOW()
     WHERE id = p_wallet_id
    RETURNING balance INTO v_new_balance;

    UPDATE rides
       SET payment_status = 'paid',
           updated_at     = NOW()
     WHERE id = p_ride_id;

    RETURN v_new_balance;
END;
$$;

-- P0-6: Atomic transfer — locks both wallets in ascending UUID order to prevent
-- deadlocks, checks sender balance, debits and credits in one transaction.
CREATE OR REPLACE FUNCTION wallet_transfer(
    p_sender_id    UUID,
    p_recipient_id UUID,
    p_amount       NUMERIC,
    OUT sender_balance    NUMERIC,
    OUT recipient_balance NUMERIC
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_first_id  UUID;
    v_second_id UUID;
BEGIN
    -- Lock in ascending UUID order to prevent deadlocks between concurrent transfers.
    IF p_sender_id < p_recipient_id THEN
        v_first_id  := p_sender_id;
        v_second_id := p_recipient_id;
    ELSE
        v_first_id  := p_recipient_id;
        v_second_id := p_sender_id;
    END IF;

    PERFORM balance FROM wallets WHERE id = v_first_id  FOR UPDATE;
    PERFORM balance FROM wallets WHERE id = v_second_id FOR UPDATE;

    SELECT balance INTO sender_balance
      FROM wallets WHERE id = p_sender_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'sender wallet not found: %', p_sender_id;
    END IF;

    IF sender_balance < p_amount THEN
        RAISE EXCEPTION 'insufficient_funds';
    END IF;

    UPDATE wallets
       SET balance    = balance - p_amount,
           updated_at = NOW()
     WHERE id = p_sender_id
    RETURNING balance INTO sender_balance;

    UPDATE wallets
       SET balance    = balance + p_amount,
           updated_at = NOW()
     WHERE id = p_recipient_id
    RETURNING balance INTO recipient_balance;

    IF recipient_balance IS NULL THEN
        RAISE EXCEPTION 'recipient wallet not found: %', p_recipient_id;
    END IF;
END;
$$;
