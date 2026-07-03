-- Migration 203: SECURITY DEFINER + pinned search_path on corporate money RPCs (C3)
--
-- Context: corporate_wallet_apply_delta (migration 28) and
-- corporate_allowance_apply_delta (migration 29) move real corporate money but
-- were created without SECURITY DEFINER or a pinned search_path, violating the
-- Spinr money-function convention (CLAUDE.md: "All money-touching functions
-- must be SECURITY DEFINER with explicit search_path pinning"). Unqualified
-- table references (corporate_wallets, corporate_wallet_transactions,
-- corporate_member_allowances) resolve via the caller's search_path, leaving a
-- search_path-hijack surface. Mirrors migrations 50 and 196, which do this
-- correctly. Function bodies are copied verbatim from 28/29 — only the
-- SECURITY DEFINER / SET search_path clauses and the EXECUTE grants change.
-- Conflict-safety check (docs/runbooks/migration-conflict-detection.md) done:
-- no migration between 28/29 and this one redefines either function (grep
-- across all migrations hits only 28, 29, and comment-only refs in 142/196).
--
-- Forward-compatible: CREATE OR REPLACE FUNCTION only; no table rewrite, safe
-- to run against live traffic.
--
-- Rollback:
--   Re-run the CREATE OR REPLACE FUNCTION statements from migrations
--   28_corporate_wallet_rpc.sql and 29_corporate_allowance_rpc.sql (definitions
--   without SECURITY DEFINER), then:
--   GRANT EXECUTE ON FUNCTION corporate_wallet_apply_delta(UUID, TEXT, TEXT, NUMERIC, UUID, UUID, TEXT, UUID, TEXT, NUMERIC) TO PUBLIC;
--   GRANT EXECUTE ON FUNCTION corporate_allowance_apply_delta(UUID, UUID, UUID, TEXT, NUMERIC, UUID, TEXT, NUMERIC) TO PUBLIC;

-- ============================================================
-- corporate_wallet_apply_delta — body verbatim from migration 28
-- ============================================================
CREATE OR REPLACE FUNCTION corporate_wallet_apply_delta(
    p_wallet_id          UUID,
    p_scope              TEXT,
    p_type               TEXT,
    p_delta              NUMERIC(12,2),
    p_ride_id            UUID DEFAULT NULL,
    p_member_id          UUID DEFAULT NULL,
    p_stripe_pi          TEXT DEFAULT NULL,
    p_actor_user_id      UUID DEFAULT NULL,
    p_notes              TEXT DEFAULT NULL,
    p_floor              NUMERIC(12,2) DEFAULT NULL
)
RETURNS TABLE(transaction_id UUID, balance_after NUMERIC(12,2))
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    v_current   NUMERIC(12,2);
    v_new       NUMERIC(12,2);
    v_txn_id    UUID;
BEGIN
    -- Idempotency short-circuit: if stripe_payment_intent_id already in ledger, no-op.
    IF p_stripe_pi IS NOT NULL THEN
        SELECT wt.id, wt.balance_after INTO v_txn_id, v_new
        FROM corporate_wallet_transactions wt
        WHERE wt.stripe_payment_intent_id = p_stripe_pi
        LIMIT 1;
        IF FOUND THEN
            transaction_id := v_txn_id;
            balance_after  := v_new;
            RETURN NEXT;
            RETURN;
        END IF;
    END IF;

    SELECT balance INTO v_current
    FROM corporate_wallets
    WHERE id = p_wallet_id
    FOR UPDATE;

    IF v_current IS NULL THEN
        RAISE EXCEPTION 'wallet not found: %', p_wallet_id;
    END IF;

    v_new := v_current + p_delta;

    -- Enforce soft-negative floor (master-scope only; member scope checked by caller).
    IF p_scope = 'master' AND p_floor IS NOT NULL AND v_new < p_floor THEN
        RAISE EXCEPTION 'wallet_below_floor: new=% floor=%', v_new, p_floor;
    END IF;

    UPDATE corporate_wallets
    SET balance = v_new, updated_at = now()
    WHERE id = p_wallet_id;

    INSERT INTO corporate_wallet_transactions
        (wallet_id, scope, type, amount, balance_after, ride_id, member_id,
         stripe_payment_intent_id, actor_user_id, notes)
    VALUES
        (p_wallet_id, p_scope, p_type, p_delta, v_new, p_ride_id, p_member_id,
         p_stripe_pi, p_actor_user_id, p_notes)
    RETURNING id INTO v_txn_id;

    transaction_id := v_txn_id;
    balance_after  := v_new;
    RETURN NEXT;
END
$$;

-- ============================================================
-- corporate_allowance_apply_delta — body verbatim from migration 29
-- ============================================================
CREATE OR REPLACE FUNCTION corporate_allowance_apply_delta(
    p_wallet_id          UUID,
    p_allowance_id       UUID,
    p_member_id          UUID,
    p_type               TEXT,
    p_amount             NUMERIC(12,2),
    p_actor_user_id      UUID DEFAULT NULL,
    p_notes              TEXT DEFAULT NULL,
    p_floor              NUMERIC(12,2) DEFAULT NULL
)
RETURNS TABLE(
    master_txn_id        UUID,
    member_txn_id        UUID,
    master_balance_after NUMERIC(12,2),
    allowance_used_after NUMERIC(12,2)
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    v_master_balance NUMERIC(12,2);
    v_master_new     NUMERIC(12,2);
    v_used           NUMERIC(12,2);
    v_used_new       NUMERIC(12,2);
    v_master_delta   NUMERIC(12,2);
    v_used_delta     NUMERIC(12,2);
    v_master_txn     UUID;
    v_member_txn     UUID;
BEGIN
    IF p_type NOT IN ('allowance_grant','allowance_reset','allowance_rollback') THEN
        RAISE EXCEPTION 'invalid allowance type: %', p_type;
    END IF;
    IF p_amount < 0 THEN
        RAISE EXCEPTION 'amount must be non-negative';
    END IF;

    -- Deterministic lock order (prevents deadlock): master wallet first, then allowance.
    SELECT balance INTO v_master_balance
    FROM corporate_wallets
    WHERE id = p_wallet_id
    FOR UPDATE;
    IF v_master_balance IS NULL THEN
        RAISE EXCEPTION 'wallet not found: %', p_wallet_id;
    END IF;

    SELECT used INTO v_used
    FROM corporate_member_allowances
    WHERE id = p_allowance_id
    FOR UPDATE;
    IF v_used IS NULL THEN
        RAISE EXCEPTION 'allowance not found: %', p_allowance_id;
    END IF;

    -- Map type → (master delta, used delta).
    -- grant:    master -p_amount, used -p_amount  (usage counter decreases → more available)
    -- reset:    master 0,         used -v_used    (zero out current usage, no master move)
    -- rollback: master +p_amount, used +p_amount  (undo a prior grant)
    IF p_type = 'allowance_grant' THEN
        v_master_delta := -p_amount;
        v_used_delta   := -p_amount;
    ELSIF p_type = 'allowance_reset' THEN
        v_master_delta := 0;
        v_used_delta   := -v_used;
    ELSE
        v_master_delta := p_amount;
        v_used_delta   := p_amount;
    END IF;

    v_master_new := v_master_balance + v_master_delta;
    v_used_new   := v_used + v_used_delta;

    -- Floor check on master only; allowance soft-negative is governed by master's floor.
    IF p_floor IS NOT NULL AND v_master_new < p_floor THEN
        RAISE EXCEPTION 'wallet_below_floor: new=% floor=%', v_master_new, p_floor;
    END IF;

    UPDATE corporate_wallets
    SET balance = v_master_new, updated_at = now()
    WHERE id = p_wallet_id;

    UPDATE corporate_member_allowances
    SET used = v_used_new, updated_at = now()
    WHERE id = p_allowance_id;

    INSERT INTO corporate_wallet_transactions
        (wallet_id, scope, type, amount, balance_after, member_id, actor_user_id, notes)
    VALUES
        (p_wallet_id, 'master', p_type, v_master_delta, v_master_new,
         p_member_id, p_actor_user_id, p_notes)
    RETURNING id INTO v_master_txn;

    INSERT INTO corporate_wallet_transactions
        (wallet_id, scope, type, amount, balance_after, member_id, actor_user_id, notes)
    VALUES
        (p_wallet_id, 'member:' || p_member_id::text, p_type, v_used_delta, v_used_new,
         p_member_id, p_actor_user_id, p_notes)
    RETURNING id INTO v_member_txn;

    master_txn_id        := v_master_txn;
    member_txn_id        := v_member_txn;
    master_balance_after := v_master_new;
    allowance_used_after := v_used_new;
    RETURN NEXT;
END
$$;

-- Backend-only: these are now SECURITY DEFINER money functions and Supabase
-- exposes every public function at /rest/v1/rpc/. Without this REVOKE, any
-- authenticated (or anon) client could call them directly with an arbitrary
-- wallet_id/amount, bypassing all application-layer auth. Mirrors migration
-- 196 (revoke PUBLIC, then grant service_role back — revoking PUBLIC also
-- strips service_role's inherited EXECUTE, and the backend's service-role key
-- must keep calling these from corporate_wallet_service.py /
-- corporate_allowance_service.py).
REVOKE EXECUTE ON FUNCTION corporate_wallet_apply_delta(UUID, TEXT, TEXT, NUMERIC, UUID, UUID, TEXT, UUID, TEXT, NUMERIC)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION corporate_wallet_apply_delta(UUID, TEXT, TEXT, NUMERIC, UUID, UUID, TEXT, UUID, TEXT, NUMERIC)
    TO service_role;

REVOKE EXECUTE ON FUNCTION corporate_allowance_apply_delta(UUID, UUID, UUID, TEXT, NUMERIC, UUID, TEXT, NUMERIC)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION corporate_allowance_apply_delta(UUID, UUID, UUID, TEXT, NUMERIC, UUID, TEXT, NUMERIC)
    TO service_role;
