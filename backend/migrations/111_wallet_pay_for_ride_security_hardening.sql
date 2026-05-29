-- 111_wallet_pay_for_ride_security_hardening.sql
--
-- Closes three security gaps that became exploitable once the rides.id TEXT
-- cast (migration 108) made the RPC succeed instead of rolling back.
--
-- Gap 1 — Underpayment via client-supplied amount
--   /wallet/pay only rejected amounts ABOVE total_fare (route-level guard);
--   a rider could submit p_amount = $0.01 for a $30 ride and the function
--   would debit that smaller amount then mark the ride paid.
--   Fix: after locking the ride row, read COALESCE(grand_total, total_fare)
--   as the server-authoritative fare and reject if
--   p_amount - p_tip_amount < v_server_fare (with 0.02 rounding tolerance).
--
-- Gap 2 — Payment against a non-completed ride
--   /wallet/pay only checked that the ride existed and belonged to the rider;
--   it did not verify that the ride was in a terminal state ready for payment.
--   This RPC unconditionally marks payment_status='paid', so a call on a
--   searching, in_progress, or cancelled ride would permanently corrupt its
--   payment state.
--   Fix: check rides.status = 'completed' inside the locked transaction.
--
-- Gap 3 — No EXECUTE restriction on PostgREST roles
--   SECURITY DEFINER bypasses RLS, so any caller that could reach the
--   PostgREST /rpc endpoint could supply arbitrary wallet_id, ride_id,
--   amount and have the function move money / mark rides paid without
--   ownership checks.  The prior text/uuid type error accidentally blocked
--   those calls; the cast fix made them succeed.
--   Fix: revoke EXECUTE from anon and authenticated (PostgREST roles).
--   The backend uses the service_role which is not affected by REVOKE.
--
-- Also drops the legacy 3-parameter overload (UUID, UUID, NUMERIC) left
-- behind by migrations 107-110 (CREATE OR REPLACE on a 4-param signature
-- does NOT replace a 3-param overload — they coexist as separate functions).
-- All callers now send 4 named params so the 3-param version is dead code
-- and an unprotected backdoor.
--
-- Forward-compatible: no schema change, safe to run against live traffic.
-- Rollback: re-apply migration 110's function body and re-grant EXECUTE.

-- Drop the unprotected 3-param overload left behind by earlier migrations.
DROP FUNCTION IF EXISTS wallet_pay_for_ride(UUID, UUID, NUMERIC);

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
    v_status      TEXT;      -- rides.payment_status
    v_ride_status TEXT;      -- rides.status (trip lifecycle state)
    v_server_fare NUMERIC;   -- authoritative fare from DB (no tip)
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

    -- Lock the ride row and read state needed for both guards below.
    -- rides.id is TEXT; cast the UUID parameter to text for the comparison.
    SELECT payment_status,
           status,
           COALESCE(grand_total, total_fare, 0)
      INTO v_status, v_ride_status, v_server_fare
      FROM rides
     WHERE id = p_ride_id::text
       FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'ride not found: %', p_ride_id
            USING ERRCODE = 'P0002';
    END IF;

    -- Gap 2 guard: only allow payment on completed rides.  The process_payment
    -- route already enforces this for the normal path; this check closes the
    -- gap for the legacy /wallet/pay bypass and any direct RPC call.
    IF v_ride_status != 'completed' THEN
        RAISE EXCEPTION 'ride_not_payable: ride % has status %, expected completed',
            p_ride_id, v_ride_status
            USING ERRCODE = 'P0004';
    END IF;

    -- Idempotency guard: if this ride has already been paid, do NOT debit
    -- again. Return NULL so the caller can distinguish a no-op from a real
    -- debit and skip the wallet_transactions ledger write.
    IF v_status = 'paid' THEN
        RETURN NULL;  -- NULL signals caller: ride already paid, no money moved
    END IF;

    -- Gap 1 guard: the fare component of p_amount must cover the server-derived
    -- ride fare. p_amount may legitimately exceed v_server_fare when a tip is
    -- included (p_tip_amount accounts for that). A 0.02 tolerance absorbs minor
    -- floating-point rounding differences in client-computed amounts.
    IF p_amount - COALESCE(p_tip_amount, 0) < v_server_fare - 0.02 THEN
        RAISE EXCEPTION 'fare_underpaid: debit amount % is below server fare % for ride %',
            p_amount - COALESCE(p_tip_amount, 0), v_server_fare, p_ride_id
            USING ERRCODE = 'P0003';
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

-- Gap 3 fix: revoke direct execution from PostgREST roles.
-- The backend service_role bypasses REVOKE by design (it has superuser-like
-- privilege in Supabase and is not subject to REVOKE on individual objects).
REVOKE EXECUTE ON FUNCTION wallet_pay_for_ride(UUID, UUID, NUMERIC, NUMERIC)
    FROM anon, authenticated;
