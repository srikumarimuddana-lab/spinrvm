-- 188_wallet_cancellation_fee_atomic_debit.sql
--
-- C3: make the rider cancellation-fee wallet debit atomic and idempotent.
--
-- Background
-- ----------
-- routes/rides.py:cancel_ride_rider charged the cancellation fee with a
-- read-compute-write over two separate PostgREST calls:
--     read balance -> compute new_balance in Python -> update_one(balance=...)
--                  -> insert_one(wallet_transactions, ...)
-- Two defects:
--   1. Non-atomic / lost-update: no row lock and no conditional filter on the
--      prior balance, so a cancel racing a top-up (or a double-tapped cancel
--      that slips past the ride status claim) can lose a write and mischarge
--      the rider. Every other money mutation already goes through an atomic
--      RPC (wallet_pay_for_ride, corporate_wallet_apply_delta).
--   2. Split write integrity hole: the wallet balance UPDATE and the ledger
--      INSERT are independent HTTP calls, so a failure of the second leaves a
--      debited balance with NO ledger row. This was already happening in
--      practice: 'cancellation_fee' was never added to the
--      wallet_transactions_type_check allowlist (migrations 19 + 29 stop at
--      'admin_debit'), so the ledger INSERT 23514s on every cancellation fee
--      — rider debit AND driver credit — while the balance change had already
--      committed.
--
-- This migration:
--   1. Adds 'cancellation_fee' to the wallet_transactions type allowlist.
--   2. Adds wallet_debit_cancellation_fee(): one transaction that locks the
--      wallet row, debits up to the available balance (never negative — keeps
--      the prior clamp semantics), and writes the ledger row. Idempotent on
--      (wallet_id, reference_id, type): a replay finds the prior debit row and
--      moves no money. Both halves now commit or roll back together.
--
-- Forward-compatible: idempotent DROP+ADD of the CHECK and CREATE OR REPLACE of
-- the function; no data migration; safe to run against live traffic.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS wallet_debit_cancellation_fee(UUID, UUID, TEXT, NUMERIC);
--   ALTER TABLE wallet_transactions DROP CONSTRAINT IF EXISTS wallet_transactions_type_check;
--   ALTER TABLE wallet_transactions ADD CONSTRAINT wallet_transactions_type_check
--     CHECK (type IN ('top_up','ride_payment','ride_refund','bonus','referral',
--                     'cashout','fare_split_received','fare_split_sent',
--                     'quest_reward','admin_credit','admin_debit'));  -- migration 29's list

BEGIN;

-- ── 1. Allow the 'cancellation_fee' ledger type ──────────────────────
-- Idempotent: drop then re-add with the full allowlist (migration 29's set
-- plus 'cancellation_fee').
ALTER TABLE wallet_transactions DROP CONSTRAINT IF EXISTS wallet_transactions_type_check;

ALTER TABLE wallet_transactions
    ADD CONSTRAINT wallet_transactions_type_check
    CHECK (type IN (
        'top_up',
        'ride_payment',
        'ride_refund',
        'bonus',
        'referral',
        'cashout',
        'fare_split_received',
        'fare_split_sent',
        'quest_reward',
        'admin_credit',
        'admin_debit',
        'cancellation_fee'
    ));

-- ── 2. Atomic, idempotent cancellation-fee debit ─────────────────────
-- SECURITY DEFINER + pinned search_path per Spinr convention for
-- money-touching functions.
--
-- Returns (new_balance, actual_charge):
--   * actual_charge is the amount actually debited on THIS call (0 on an
--     idempotent replay, or when the balance is empty / fee is non-positive).
--   * new_balance is the wallet balance after this call.
CREATE OR REPLACE FUNCTION wallet_debit_cancellation_fee(
    p_wallet_id UUID,
    p_user_id   UUID,
    p_ride_id   TEXT,
    p_amount    NUMERIC,
    OUT new_balance   NUMERIC,
    OUT actual_charge NUMERIC
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_balance NUMERIC;
BEGIN
    -- Lock the wallet row for the duration of the transaction so a concurrent
    -- top-up / pay / cancel serializes behind us instead of racing the balance.
    SELECT balance INTO v_balance
      FROM wallets
     WHERE id = p_wallet_id
       FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'wallet not found: %', p_wallet_id
            USING ERRCODE = 'P0002';
    END IF;

    -- Idempotency guard: a prior cancellation-fee debit for this ride already
    -- moved money on this wallet. Return the current (locked) balance and
    -- report no new charge. Filter amount < 0 so the driver-side credit row
    -- (same type + reference_id, positive amount, on a different wallet) can
    -- never be mistaken for this debit.
    IF EXISTS (
        SELECT 1
          FROM wallet_transactions
         WHERE wallet_id = p_wallet_id
           AND reference_id = p_ride_id
           AND type = 'cancellation_fee'
           AND amount < 0
    ) THEN
        new_balance   := v_balance;
        actual_charge := 0;
        RETURN;
    END IF;

    -- Clamp the debit to the available balance — never drive the wallet
    -- negative. Preserves the prior Python semantics: charge min(fee, balance).
    actual_charge := LEAST(GREATEST(p_amount, 0), GREATEST(v_balance, 0));

    IF actual_charge <= 0 THEN
        new_balance := v_balance;   -- nothing to charge; write no ledger row
        RETURN;
    END IF;

    UPDATE wallets
       SET balance    = balance - actual_charge,
           updated_at = NOW()
     WHERE id = p_wallet_id
    RETURNING balance INTO new_balance;

    INSERT INTO wallet_transactions (
        id, wallet_id, user_id, type, amount, balance_after,
        reference_id, description, metadata, created_at
    ) VALUES (
        gen_random_uuid(),
        p_wallet_id,
        p_user_id,
        'cancellation_fee',
        -actual_charge,
        new_balance,
        p_ride_id,
        'Cancellation fee for ride ' || left(p_ride_id, 8),
        jsonb_build_object('ride_id', p_ride_id),
        NOW()
    );
END;
$$;

-- PostgREST roles must never move money directly. SECURITY DEFINER bypasses
-- RLS, so without this any caller reaching /rpc could supply an arbitrary
-- wallet_id/amount. The backend uses service_role, which bypasses REVOKE by
-- design. Mirrors migration 111's hardening of wallet_pay_for_ride.
REVOKE EXECUTE ON FUNCTION wallet_debit_cancellation_fee(UUID, UUID, TEXT, NUMERIC)
    FROM anon, authenticated;

COMMIT;

-- After applying in Supabase, reload PostgREST's schema cache:
--   NOTIFY pgrst, 'reload schema';
