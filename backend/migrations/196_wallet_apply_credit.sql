-- Migration 196: Idempotent consumer-wallet credit RPC (C6)
--
-- Context: the wallet_topup Stripe webhook (routes/webhooks.py) credited the
-- wallet via wallet_increment_balance + a SEPARATE wallet_transactions insert,
-- with no idempotency on reference_id. A crash between the balance write and the
-- stripe_events ack (mark_stripe_event_processed), followed by a Stripe retry,
-- double-credited the wallet (silent money creation).
--
-- wallet_apply_credit locks the wallet row FIRST, then dedups on
-- (wallet_id, reference_id, type) inside that lock, then writes the balance and
-- the ledger row together — so a retried (or concurrently re-delivered) webhook
-- is a no-op that returns the original transaction + balance. This mirrors the
-- corporate_wallet_apply_delta idempotency pattern (migration 28), with the
-- idempotency check moved INSIDE the row lock so it also serialises concurrent
-- deliveries for the same wallet (corporate checks before the lock, which has a
-- TOCTOU window relying on a unique constraint as backstop).
--
-- SECURITY DEFINER + pinned search_path per the Spinr money-function convention.
--
-- A unique index on (wallet_id, reference_id, type) is intentionally NOT added:
-- the lock-then-check above is authoritative, and building a unique index could
-- fail against any pre-existing duplicate rows produced by the very bug this
-- fixes. A separate dedup-then-index migration can follow if desired.
--
-- Forward-compatible: CREATE OR REPLACE FUNCTION only; no table rewrite, safe to
-- run against live traffic.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS wallet_apply_credit(UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, JSONB);

CREATE OR REPLACE FUNCTION wallet_apply_credit(
    p_wallet_id    UUID,
    p_user_id      UUID,
    p_type         TEXT,
    p_amount       NUMERIC,
    p_reference_id TEXT,
    p_description  TEXT  DEFAULT NULL,
    p_metadata     JSONB DEFAULT '{}'::jsonb
)
RETURNS TABLE(transaction_id UUID, balance_after NUMERIC, deduped BOOLEAN)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    v_current NUMERIC;
    v_new     NUMERIC;
    v_txn_id  UUID;
BEGIN
    -- Lock the wallet row first so the idempotency check + insert below are
    -- atomic against concurrent webhook deliveries for the same wallet.
    SELECT balance INTO v_current
      FROM wallets
     WHERE id = p_wallet_id
       AND is_active = TRUE
       FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'wallet not found or suspended: %', p_wallet_id
            USING ERRCODE = 'P0002';
    END IF;

    -- Idempotency (C6): if this (wallet, reference, type) credit already landed,
    -- return it unchanged instead of crediting again. reference_id for a top-up
    -- is the Stripe payment_intent id, so a retried webhook dedups here.
    IF p_reference_id IS NOT NULL THEN
        SELECT wt.id, wt.balance_after
          INTO v_txn_id, v_new
          FROM wallet_transactions wt
         WHERE wt.wallet_id    = p_wallet_id
           AND wt.reference_id = p_reference_id
           AND wt.type         = p_type
         LIMIT 1;

        IF FOUND THEN
            transaction_id := v_txn_id;
            balance_after  := v_new;
            deduped        := TRUE;
            RETURN NEXT;
            RETURN;
        END IF;
    END IF;

    v_new := v_current + p_amount;

    UPDATE wallets
       SET balance    = v_new,
           updated_at = NOW()
     WHERE id = p_wallet_id;

    INSERT INTO wallet_transactions
        (wallet_id, user_id, type, amount, balance_after, reference_id, description, metadata)
    VALUES
        (p_wallet_id, p_user_id, p_type, p_amount, v_new, p_reference_id, p_description,
         COALESCE(p_metadata, '{}'::jsonb))
    RETURNING id INTO v_txn_id;

    transaction_id := v_txn_id;
    balance_after  := v_new;
    deduped        := FALSE;
    RETURN NEXT;
END;
$$;

-- Backend-only: this is a SECURITY DEFINER money function and Supabase exposes
-- every public function at /rest/v1/rpc/. Without this REVOKE, any authenticated
-- (or anon) client could call it directly with an arbitrary wallet_id/amount and
-- self-credit, bypassing all application-layer auth. Mirrors migration 194
-- (revoke PUBLIC, then grant service_role back).
REVOKE EXECUTE ON FUNCTION wallet_apply_credit(UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, JSONB)
    FROM PUBLIC, anon, authenticated;

-- Revoking PUBLIC also strips service_role's inherited EXECUTE: the migration
-- runner connects as `postgres` (the function owner), and service_role is
-- neither the owner nor a superuser, so it held EXECUTE only via the PUBLIC
-- grant. Grant it back so the backend's service-role key can still call the RPC
-- from the wallet_topup webhook — otherwise the call fails with "permission
-- denied for function wallet_apply_credit" and the top-up never credits.
-- (Migration 111 does not need this because it revokes only anon/authenticated,
-- leaving the PUBLIC grant intact.)
GRANT EXECUTE ON FUNCTION wallet_apply_credit(UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, JSONB)
    TO service_role;
