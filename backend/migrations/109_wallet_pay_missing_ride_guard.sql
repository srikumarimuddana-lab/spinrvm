-- 109_wallet_pay_missing_ride_guard.sql
--
-- Guard wallet_pay_for_ride against debiting when p_ride_id does not exist.
--
-- Gap introduced by migration 108: the ::text cast fixes the 42883 operator
-- error, but a call with a stale or nonexistent ride_id now silently leaves
-- v_status NULL (NOT FOUND from a missing row, not a NULL column value).
-- The paid guard `IF v_status = 'paid'` evaluates FALSE for NULL, so execution
-- falls through to the wallet debit and the subsequent UPDATE rides matches
-- zero rows — money is moved with no ride ever marked paid.
--
-- Fix: raise an explicit exception immediately after the rides SELECT when the
-- row is not found, using the same ERRCODE as the wallet-not-found guard
-- (P0002 = NO_DATA_FOUND) so callers can distinguish it from insufficient_funds.
--
-- Forward-compatible: CREATE OR REPLACE only; no schema change, no data
-- migration, safe to run against live traffic.
--
-- Rollback: re-apply migration 108's body (omits the IF NOT FOUND block).

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
    v_balance     NUMERIC;
    v_new_balance NUMERIC;
    v_status      TEXT;
BEGIN
    -- Lock the wallet row first (same order as wallet_transfer).
    SELECT balance INTO v_balance
      FROM wallets
     WHERE id = p_wallet_id
       AND is_active = TRUE
       FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'wallet not found or suspended: %', p_wallet_id
            USING ERRCODE = 'P0002';
    END IF;

    -- Lock the ride row so a concurrent caller serializes behind us.
    -- rides.id is TEXT; cast the UUID parameter to text for the comparison.
    SELECT payment_status INTO v_status
      FROM rides
     WHERE id = p_ride_id::text
       FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'ride not found: %', p_ride_id
            USING ERRCODE = 'P0002';
    END IF;

    -- Idempotency guard: if this ride has already been paid, do NOT debit
    -- again. Return NULL so the caller can distinguish a no-op from a real
    -- debit and skip the wallet_transactions ledger write.
    IF v_status = 'paid' THEN
        RETURN NULL;  -- NULL signals caller: ride already paid, no money moved
    END IF;

    IF v_balance < p_amount THEN
        RAISE EXCEPTION 'insufficient_funds'
            USING ERRCODE = 'P0001';
    END IF;

    UPDATE wallets
       SET balance    = balance - p_amount,
           updated_at = NOW()
     WHERE id = p_wallet_id
    RETURNING balance INTO v_new_balance;

    UPDATE rides
       SET payment_status = 'paid',
           updated_at     = NOW()
     WHERE id = p_ride_id::text;

    RETURN v_new_balance;
END;
$$;
