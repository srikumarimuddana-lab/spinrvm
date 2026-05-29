-- 108_wallet_pay_ride_id_text_cast.sql
--
-- Fix wallet_pay_for_ride throwing `operator does not exist: text = uuid`
-- (SQLSTATE 42883) on every wallet settlement.
--
-- Root cause: rides.id is a TEXT column (confirmed by 103_backfill_incentive_
-- claims.sql, which casts r.id::uuid to compare it against uuid columns). The
-- function declares p_ride_id UUID, so `WHERE id = p_ride_id` becomes
-- `text = uuid`, for which Postgres has no operator — the statement aborts
-- before any debit or status update. This has been latent since migration 50
-- (its `UPDATE rides ... WHERE id = p_ride_id` had the same mismatch); it
-- surfaced now because the stuck-ride wallet re-drive path actually exercises
-- the RPC. The wallet was therefore NEVER debited on these attempts, so the
-- balance is intact and re-driving is safe.
--
-- Fix: cast p_ride_id to text when comparing against rides.id. wallets.id is
-- UUID so p_wallet_id comparisons are left unchanged. We keep the parameter
-- type as UUID (not TEXT) so the function signature is unchanged and callers /
-- overloads are unaffected — CREATE OR REPLACE only.
--
-- Idempotency preserved (migration 107): returns NULL without debiting if the
-- ride is already 'paid', so callers can skip the ledger write.
--
-- Forward-compatible: CREATE OR REPLACE only; no schema change, no data
-- migration, safe to run against live traffic.
--
-- Rollback: re-apply migration 107's body (which has the uuid-comparison bug).

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
    -- again. Return NULL (not the balance) so the caller can distinguish a
    -- no-op from a real debit and skip the wallet_transactions ledger write.
    -- rides.id is TEXT, so cast the UUID parameter to text for the comparison.
    SELECT payment_status INTO v_status
      FROM rides
     WHERE id = p_ride_id::text
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
     WHERE id = p_ride_id::text;

    RETURN v_new_balance;
END;
$$;
