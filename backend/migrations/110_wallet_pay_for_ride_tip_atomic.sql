-- 110_wallet_pay_for_ride_tip_atomic.sql
--
-- Make the tip credit to driver_earnings atomic with the wallet debit.
--
-- Gap: migration 109 made wallet_pay_for_ride atomic for the debit and the
-- payment_status='paid' mark, but tip_amount and driver_earnings were still
-- written by a separate Python update_ride call after settle_wallet returned.
-- If that call failed transiently, the rider was charged (wallet debited, ride
-- paid) while the driver's earnings record was missing the tip delta. A retry
-- returned immediately on the already-paid path without attempting the repair.
--
-- Fix: add p_tip_amount NUMERIC DEFAULT 0 to wallet_pay_for_ride. When
-- p_tip_amount > 0, the UPDATE rides now atomically:
--   - writes tip_amount = p_tip_amount
--   - adds the tip delta (p_tip_amount - existing tip_amount) to driver_earnings
--     only when p_tip_amount exceeds whatever tip_amount is already stored
--     (idempotent: a re-run with the same value is a no-op on driver_earnings)
-- When p_tip_amount = 0 or is omitted, tip_amount and driver_earnings are left
-- unchanged — callers that do not track tips separately are unaffected.
--
-- The DEFAULT 0 keeps the function signature backwards-compatible: existing
-- callers (wallet_repo.py before this fix is deployed, /wallet/pay direct path)
-- continue to work without modification.
--
-- fare_breakdown_snapshot is intentionally NOT written here — JSONB manipulation
-- in a SECURITY DEFINER function is fragile and the snapshot is cosmetic
-- (display only). Python continues to update it best-effort after settlement.
--
-- Forward-compatible: CREATE OR REPLACE only; no schema change, no data
-- migration, safe to run against live traffic.
--
-- Rollback: re-apply migration 109's body (omits p_tip_amount parameter and the
-- tip_amount/driver_earnings CASE expressions in UPDATE rides).

CREATE OR REPLACE FUNCTION wallet_pay_for_ride(
    p_wallet_id  UUID,
    p_ride_id    UUID,
    p_amount     NUMERIC,
    p_tip_amount NUMERIC DEFAULT 0
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

    -- Mark the ride paid and atomically credit the tip to driver_earnings.
    -- tip_amount: set when p_tip_amount > 0, preserve existing value otherwise.
    -- driver_earnings delta: applied only when the new tip exceeds whatever is
    -- already stored — guards against double-crediting on idempotent replays.
    UPDATE rides
       SET payment_status  = 'paid',
           tip_amount      = CASE
                                 WHEN p_tip_amount > 0 THEN p_tip_amount
                                 ELSE tip_amount
                             END,
           driver_earnings = CASE
                                 WHEN p_tip_amount > COALESCE(tip_amount, 0)
                                 THEN COALESCE(driver_earnings, 0)
                                      + (p_tip_amount - COALESCE(tip_amount, 0))
                                 ELSE driver_earnings
                             END,
           updated_at      = NOW()
     WHERE id = p_ride_id::text;

    RETURN v_new_balance;
END;
$$;
