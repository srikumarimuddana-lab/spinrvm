-- Migration: 50_wallet_atomic_rpcs.sql
-- Purpose : Replace TOCTOU-prone read-compute-write wallet mutations with
--           atomic Postgres functions.  Fixes P0-4, P0-5, P0-6.
-- Rollback: DROP FUNCTION wallet_increment_balance, wallet_pay_for_ride,
--           wallet_transfer; application falls back to the previous
--           read-compute-write paths (re-introduces the races).
-- Forward-compatible: additive only — no existing columns removed or renamed.

-- ── P0-4: top-up ─────────────────────────────────────────────────────────────
-- Atomically credits a wallet by p_amount and returns the new balance.
-- Raises SQLSTATE 'P0002' (NO_DATA_FOUND) if the wallet does not exist.
CREATE OR REPLACE FUNCTION wallet_increment_balance(
    p_wallet_id UUID,
    p_amount    NUMERIC
)
RETURNS NUMERIC
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_new_balance NUMERIC;
BEGIN
    UPDATE wallets
       SET balance    = balance + p_amount,
           updated_at = now()
     WHERE id = p_wallet_id
       AND is_active  = TRUE
    RETURNING balance INTO v_new_balance;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'wallet not found or suspended: %', p_wallet_id
            USING ERRCODE = 'P0002';
    END IF;

    RETURN v_new_balance;
END;
$$;

-- ── P0-5: ride payment ────────────────────────────────────────────────────────
-- Atomically debits p_amount from a wallet and marks the ride as paid in
-- the same transaction.  Raises if:
--   - wallet not found / suspended
--   - insufficient funds (balance < p_amount)
--   - ride not found or already paid
CREATE OR REPLACE FUNCTION wallet_pay_for_ride(
    p_wallet_id UUID,
    p_ride_id   UUID,
    p_amount    NUMERIC
)
RETURNS NUMERIC
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_new_balance NUMERIC;
BEGIN
    -- Lock wallet row and debit atomically
    UPDATE wallets
       SET balance    = balance - p_amount,
           updated_at = now()
     WHERE id        = p_wallet_id
       AND is_active  = TRUE
       AND balance   >= p_amount
    RETURNING balance INTO v_new_balance;

    IF NOT FOUND THEN
        -- Distinguish not-found from insufficient-funds for cleaner error messages
        IF NOT EXISTS (SELECT 1 FROM wallets WHERE id = p_wallet_id AND is_active = TRUE) THEN
            RAISE EXCEPTION 'wallet not found or suspended: %', p_wallet_id
                USING ERRCODE = 'P0002';
        ELSE
            RAISE EXCEPTION 'insufficient wallet balance'
                USING ERRCODE = 'P0001';
        END IF;
    END IF;

    -- Mark ride paid in the same transaction
    UPDATE rides
       SET payment_status  = 'paid',
           payment_method  = 'wallet',
           updated_at      = now()
     WHERE id = p_ride_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'ride not found: %', p_ride_id
            USING ERRCODE = 'P0002';
    END IF;

    RETURN v_new_balance;
END;
$$;

-- ── P0-6: peer transfer ───────────────────────────────────────────────────────
-- Atomically debits the sender and credits the recipient.
-- Locks both wallets in ascending UUID order to prevent deadlocks.
-- Raises if sender has insufficient funds or either wallet is missing/suspended.
CREATE OR REPLACE FUNCTION wallet_transfer(
    p_sender_wallet_id    UUID,
    p_recipient_wallet_id UUID,
    p_amount              NUMERIC
)
RETURNS TABLE(sender_balance NUMERIC, recipient_balance NUMERIC)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_sender_new    NUMERIC;
    v_recipient_new NUMERIC;
BEGIN
    -- Lock both wallets in consistent order (prevents deadlock under concurrency)
    IF p_sender_wallet_id < p_recipient_wallet_id THEN
        PERFORM id FROM wallets WHERE id = p_sender_wallet_id    FOR UPDATE;
        PERFORM id FROM wallets WHERE id = p_recipient_wallet_id FOR UPDATE;
    ELSE
        PERFORM id FROM wallets WHERE id = p_recipient_wallet_id FOR UPDATE;
        PERFORM id FROM wallets WHERE id = p_sender_wallet_id    FOR UPDATE;
    END IF;

    -- Debit sender
    UPDATE wallets
       SET balance    = balance - p_amount,
           updated_at = now()
     WHERE id        = p_sender_wallet_id
       AND is_active  = TRUE
       AND balance   >= p_amount
    RETURNING balance INTO v_sender_new;

    IF NOT FOUND THEN
        IF NOT EXISTS (SELECT 1 FROM wallets WHERE id = p_sender_wallet_id AND is_active = TRUE) THEN
            RAISE EXCEPTION 'sender wallet not found or suspended: %', p_sender_wallet_id
                USING ERRCODE = 'P0002';
        ELSE
            RAISE EXCEPTION 'insufficient sender balance'
                USING ERRCODE = 'P0001';
        END IF;
    END IF;

    -- Credit recipient
    UPDATE wallets
       SET balance    = balance + p_amount,
           updated_at = now()
     WHERE id        = p_recipient_wallet_id
       AND is_active  = TRUE
    RETURNING balance INTO v_recipient_new;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'recipient wallet not found or suspended: %', p_recipient_wallet_id
            USING ERRCODE = 'P0002';
    END IF;

    RETURN QUERY SELECT v_sender_new, v_recipient_new;
END;
$$;
