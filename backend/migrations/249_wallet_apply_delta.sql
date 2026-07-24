-- Migration 249: General signed-delta consumer-wallet RPC (WS-6)
--
-- Context: four call sites mutate wallets.balance with a non-atomic
-- read-modify-write — read balance into Python, compute the new value, then
-- UPDATE filtered on {"id": wallet_id} only:
--
--   * routes/admin/wallet.py  admin_credit_wallet
--   * routes/admin/wallet.py  admin_debit_wallet
--   * routes/drivers/ride_cancel.py   rider no-show fee
--   * routes/rides/cancellation.py    rider cancellation fee
--
-- With no compare-and-swap and no row lock, any concurrent mutation landing
-- between the read and the write is silently lost: a top-up webhook overwritten
-- by a cancellation fee, an admin credit erased by a ride payment, or a debit
-- succeeding against a balance that no longer exists. wallet_apply_credit
-- (migration 196) already solved this for credits, and wallet_pay_for_ride for
-- ride settlement; this is the missing general case.
--
-- wallet_apply_delta takes a SIGNED delta so one function serves credits and
-- debits. It locks the wallet row FIRST, then dedups on
-- (wallet_id, reference_id, type) inside that lock, then writes the balance and
-- the ledger row together — the same lock-then-check ordering as 196 (which is
-- stricter than corporate_wallet_apply_delta's check-before-lock).
--
-- Floor handling has two modes because the call sites genuinely differ:
--   p_clamp_to_floor = FALSE (default) -> a delta that would breach p_floor
--       raises. Used by admin debit, where over-drawing must fail loudly.
--   p_clamp_to_floor = TRUE  -> the debit is reduced to whatever is available
--       down to p_floor. Used by the cancellation / no-show fee paths, which
--       today do max(balance - fee, 0) and charge only what the rider has.
-- The actually-applied delta is RETURNED so the caller can record the real
-- amount charged rather than the requested amount.
--
-- SECURITY DEFINER + pinned search_path per the Spinr money-function convention.
-- EXECUTE is revoked from PUBLIC/anon/authenticated and granted back to
-- service_role, matching migrations 196 and 205 — Supabase exposes every public
-- function at /rest/v1/rpc/, so without this any client could self-credit.
--
-- No unique index on (wallet_id, reference_id, type) for the same reason given
-- in 196: the lock-then-check is authoritative, and building the index could
-- fail against pre-existing duplicates created by the very bug being fixed.
--
-- Forward-compatible: CREATE OR REPLACE FUNCTION only, no table rewrite, safe to
-- run against live traffic.
--
-- rollback: DROP FUNCTION IF EXISTS wallet_apply_delta(UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, JSONB, NUMERIC, BOOLEAN);
--   No call site depends on it until the follow-up commits land, and no data is
--   migrated, so dropping it is sufficient and reversible.

CREATE OR REPLACE FUNCTION wallet_apply_delta(
    p_wallet_id      UUID,
    p_user_id        UUID,
    p_type           TEXT,
    p_delta          NUMERIC,
    p_reference_id   TEXT,
    p_description    TEXT    DEFAULT NULL,
    p_metadata       JSONB   DEFAULT '{}'::jsonb,
    p_floor          NUMERIC DEFAULT 0,
    p_clamp_to_floor BOOLEAN DEFAULT FALSE
)
RETURNS TABLE(
    transaction_id UUID,
    balance_after  NUMERIC,
    applied_delta  NUMERIC,
    deduped        BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    v_current NUMERIC;
    v_new     NUMERIC;
    v_delta   NUMERIC;
    v_txn_id  UUID;
BEGIN
    IF p_delta IS NULL OR p_delta = 0 THEN
        RAISE EXCEPTION 'delta must be non-zero';
    END IF;

    -- Lock the wallet row first so the idempotency check + write below are
    -- atomic against any concurrent mutation of the same wallet.
    SELECT balance INTO v_current
      FROM wallets
     WHERE id = p_wallet_id
       AND is_active = TRUE
       FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'wallet not found or suspended: %', p_wallet_id
            USING ERRCODE = 'P0002';
    END IF;

    -- Idempotency: a retried request carrying the same reference returns the
    -- original transaction untouched instead of applying the delta twice.
    IF p_reference_id IS NOT NULL THEN
        SELECT wt.id, wt.balance_after, wt.amount
          INTO v_txn_id, v_new, v_delta
          FROM wallet_transactions wt
         WHERE wt.wallet_id    = p_wallet_id
           AND wt.reference_id = p_reference_id
           AND wt.type         = p_type
         LIMIT 1;

        IF FOUND THEN
            transaction_id := v_txn_id;
            balance_after  := v_new;
            applied_delta  := v_delta;
            deduped        := TRUE;
            RETURN NEXT;
            RETURN;
        END IF;
    END IF;

    v_delta := p_delta;
    v_new   := v_current + v_delta;

    IF p_floor IS NOT NULL AND v_new < p_floor THEN
        IF p_clamp_to_floor THEN
            -- Charge only what is available down to the floor. For a debit this
            -- yields a partial charge; if the wallet is already at/below the
            -- floor there is nothing to take and the delta becomes 0.
            v_delta := p_floor - v_current;
            IF v_delta > 0 THEN
                v_delta := 0;
            END IF;
            v_new := v_current + v_delta;
        ELSE
            RAISE EXCEPTION 'wallet_below_floor: new=% floor=%', v_new, p_floor
                USING ERRCODE = 'P0001';
        END IF;
    END IF;

    UPDATE wallets
       SET balance    = v_new,
           updated_at = NOW()
     WHERE id = p_wallet_id;

    INSERT INTO wallet_transactions
        (wallet_id, user_id, type, amount, balance_after, reference_id, description, metadata)
    VALUES
        (p_wallet_id, p_user_id, p_type, v_delta, v_new, p_reference_id, p_description,
         COALESCE(p_metadata, '{}'::jsonb))
    RETURNING id INTO v_txn_id;

    transaction_id := v_txn_id;
    balance_after  := v_new;
    applied_delta  := v_delta;
    deduped        := FALSE;
    RETURN NEXT;
END;
$$;

REVOKE EXECUTE ON FUNCTION wallet_apply_delta(UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, JSONB, NUMERIC, BOOLEAN)
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION wallet_apply_delta(UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, JSONB, NUMERIC, BOOLEAN)
    TO service_role;
