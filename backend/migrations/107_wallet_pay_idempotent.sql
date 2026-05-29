-- 107_wallet_pay_idempotent.sql
--
-- Make wallet_pay_for_ride idempotent so a retry or concurrent re-drive of the
-- same ride can never debit the rider's wallet twice.
--
-- Background: migration 50 introduced wallet_pay_for_ride as an atomic
-- (debit + mark-paid) transaction, but it debited UNCONDITIONALLY — it never
-- checked whether the ride was already paid. The only thing preventing a
-- double debit was the process_payment 'pending' -> 'processing' claim. When a
-- ride got stuck in 'processing' (a settle crash/timeout between the claim and
-- this RPC), there was no safe way to re-drive it: re-running risked a double
-- debit. This adds an explicit paid-guard so recovery of a stuck 'processing'
-- ride (see routes/rides.py process_payment) is double-charge-proof.
--
-- Idempotency: if the ride is already 'paid', return NULL (not the current
-- balance) so the caller can distinguish a no-op from a real debit and skip
-- the wallet_transactions ledger write. Lock ordering preserved (wallet row
-- first, then ride row) to match wallet_transfer and avoid deadlocks.
--
-- Forward-compatible: CREATE OR REPLACE only; no schema change, no data
-- migration, safe to run against live traffic.
--
-- Rollback: re-apply migration 50's body of wallet_pay_for_ride (the version
-- without the v_status paid-guard).

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

    -- Idempotency guard: if this ride has already been paid, do NOT debit
    -- again. Return the (unchanged) wallet balance. Lock the ride row so a
    -- concurrent caller serializes behind us.
    SELECT payment_status INTO v_status
      FROM rides
     WHERE id = p_ride_id::text  -- rides.id is TEXT; see migration 108
       FOR UPDATE;

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
     WHERE id = p_ride_id::text;  -- rides.id is TEXT; see migration 108

    RETURN v_new_balance;
END;
$$;
