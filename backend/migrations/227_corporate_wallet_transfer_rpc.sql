-- 227_corporate_wallet_transfer_rpc.sql
--
-- Money movement for the departmental funding hierarchy (Phase 5, Q9/Q10):
--
--   1. Ledger `type` CHECK gains 'allocation' — the paired legs of a
--      master↔section transfer (inline auto-named constraint from migration
--      27, dropped by name-lookup like migration 226's pattern).
--   2. corporate_wallet_apply_delta re-forked VERBATIM from migration 214
--      (the authoritative version: TEXT actor, SECURITY DEFINER, pinned
--      search_path) changing ONLY the floor enforcement: previously only
--      p_scope='master' was floor-checked; section scopes
--      ('section:<uuid>') now enforce p_floor too (Q9 — departments are
--      hard-floored at 0; the caller passes the wallet's
--      soft_negative_floor, which ensure_section_wallet sets to 0).
--   3. NEW corporate_wallet_transfer — atomic two-wallet movement used by
--      "admin allocates master → section" (and back):
--        * locks BOTH wallet rows FOR UPDATE in id order (deadlock-safe)
--        * same-wallet and cross-company transfers RAISE
--        * source floor: master may draw down into its credit floor
--          (allocation of extended credit, Q10); sections pass floor 0
--        * idempotency via p_idempotency_key stored in the ledger's
--          stripe_payment_intent_id column (partial unique index from
--          migration 27) — legs suffixed ':out' / ':in' so the pair
--          coexists under the unique index; replays return the original out
--          leg's result
--        * paired 'allocation' ledger rows, scope 'master' /
--          'section:<uuid>' derived from each wallet row
--
-- Money rules (CLAUDE.md): SECURITY DEFINER + pinned search_path; EXECUTE
-- revoked from PUBLIC/anon/authenticated, granted to service_role only
-- (backend-only, never client) — same lockdown as migration 203/214.
--
-- Rollback (on paper):
--   DROP FUNCTION IF EXISTS corporate_wallet_transfer(UUID, UUID, NUMERIC, TEXT, TEXT, TEXT, NUMERIC);
--   Re-apply migration 214's corporate_wallet_apply_delta verbatim (restores
--   master-only floor enforcement).
--   ALTER TABLE corporate_wallet_transactions DROP CONSTRAINT <type-check name>;
--   ALTER TABLE corporate_wallet_transactions ADD CONSTRAINT corporate_wallet_transactions_type_check
--     CHECK (type IN ('topup','allowance_grant','allowance_reset','allowance_rollback','ride_debit','refund','adjustment'));
--   (only valid while no 'allocation' rows exist)

-- 1. Extend the ledger type CHECK with 'allocation'.
--    Lock note (214/226 precedent): the DROP+ADD CONSTRAINT revalidates the
--    CHECK across all existing rows under ACCESS EXCLUSIVE on
--    corporate_wallet_transactions — a B2B-only ledger, sub-second at
--    current volume; not a hot consumer table.
DO $$
DECLARE
    v_conname TEXT;
BEGIN
    SELECT c.conname INTO v_conname
    FROM pg_constraint c
    WHERE c.conrelid = 'corporate_wallet_transactions'::regclass
      AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) ILIKE '%ride_debit%';
    IF v_conname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE corporate_wallet_transactions DROP CONSTRAINT %I', v_conname);
    ELSE
        RAISE NOTICE 'corporate_wallet_transactions: type CHECK not found to drop';
    END IF;
    ALTER TABLE corporate_wallet_transactions ADD CONSTRAINT corporate_wallet_transactions_type_check
        CHECK (type IN (
            'topup','allowance_grant','allowance_reset','allowance_rollback',
            'ride_debit','refund','adjustment','allocation'
        ));
END $$;

-- 2. apply_delta: floor enforcement extended to section scopes.
--    Body verbatim from migration 214 except the floor IF condition.
CREATE OR REPLACE FUNCTION corporate_wallet_apply_delta(
    p_wallet_id          UUID,
    p_scope              TEXT,
    p_type               TEXT,
    p_delta              NUMERIC(12,2),
    p_ride_id            UUID DEFAULT NULL,
    p_member_id          UUID DEFAULT NULL,
    p_stripe_pi          TEXT DEFAULT NULL,
    p_actor_user_id      TEXT DEFAULT NULL,
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

    -- Enforce floor for master AND section scopes (migration 227 / Q9 —
    -- sections are hard-floored at 0; member scope checked by caller).
    IF (p_scope = 'master' OR p_scope LIKE 'section:%')
       AND p_floor IS NOT NULL AND v_new < p_floor THEN
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

-- 3. Atomic two-wallet transfer (allocation).
CREATE OR REPLACE FUNCTION corporate_wallet_transfer(
    p_from_wallet_id     UUID,
    p_to_wallet_id       UUID,
    p_amount             NUMERIC(12,2),
    p_actor_user_id      TEXT DEFAULT NULL,
    p_idempotency_key    TEXT DEFAULT NULL,
    p_notes              TEXT DEFAULT NULL,
    p_source_floor       NUMERIC(12,2) DEFAULT 0
)
RETURNS TABLE(out_txn_id UUID, in_txn_id UUID, from_balance NUMERIC(12,2), to_balance NUMERIC(12,2))
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    v_from      corporate_wallets%ROWTYPE;
    v_to        corporate_wallets%ROWTYPE;
    v_first     UUID;
    v_second    UUID;
    v_from_new  NUMERIC(12,2);
    v_to_new    NUMERIC(12,2);
    v_floor     NUMERIC(12,2);
    v_out_id    UUID;
    v_in_id     UUID;
    v_from_scope TEXT;
    v_to_scope   TEXT;
BEGIN
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'transfer_amount_invalid: %', p_amount;
    END IF;
    IF p_from_wallet_id = p_to_wallet_id THEN
        RAISE EXCEPTION 'transfer_same_wallet';
    END IF;

    -- Idempotency: the out leg carries '<key>:out' in the PI column.
    IF p_idempotency_key IS NOT NULL THEN
        SELECT wt.id, wt.balance_after INTO v_out_id, v_from_new
        FROM corporate_wallet_transactions wt
        WHERE wt.stripe_payment_intent_id = p_idempotency_key || ':out'
        LIMIT 1;
        IF FOUND THEN
            SELECT wt.id, wt.balance_after INTO v_in_id, v_to_new
            FROM corporate_wallet_transactions wt
            WHERE wt.stripe_payment_intent_id = p_idempotency_key || ':in'
            LIMIT 1;
            out_txn_id   := v_out_id;
            in_txn_id    := v_in_id;
            from_balance := v_from_new;
            to_balance   := v_to_new;
            RETURN NEXT;
            RETURN;
        END IF;
    END IF;

    -- Lock both rows in id order — deadlock-safe against concurrent
    -- transfers in the opposite direction.
    IF p_from_wallet_id < p_to_wallet_id THEN
        v_first := p_from_wallet_id; v_second := p_to_wallet_id;
    ELSE
        v_first := p_to_wallet_id; v_second := p_from_wallet_id;
    END IF;
    PERFORM 1 FROM corporate_wallets WHERE id = v_first FOR UPDATE;
    PERFORM 1 FROM corporate_wallets WHERE id = v_second FOR UPDATE;

    SELECT * INTO v_from FROM corporate_wallets WHERE id = p_from_wallet_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'wallet not found: %', p_from_wallet_id;
    END IF;
    SELECT * INTO v_to FROM corporate_wallets WHERE id = p_to_wallet_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'wallet not found: %', p_to_wallet_id;
    END IF;

    IF v_from.company_id <> v_to.company_id THEN
        RAISE EXCEPTION 'transfer_cross_company';
    END IF;

    v_from_new := v_from.balance - p_amount;
    v_to_new   := v_to.balance + p_amount;

    -- Source floor, derived SERVER-SIDE (reviewer hardening): a section
    -- source is ALWAYS floored at 0 (Q9 as a DB invariant — no caller bug
    -- can push a department negative); a master source may draw down into
    -- its credit floor (caller may pass a tighter p_source_floor, but never
    -- a looser one than the wallet's own soft_negative_floor).
    IF v_from.section_id IS NOT NULL THEN
        v_floor := 0;
    ELSE
        v_floor := GREATEST(
            COALESCE(v_from.soft_negative_floor, 0),
            COALESCE(p_source_floor, v_from.soft_negative_floor, 0)
        );
    END IF;
    IF v_from_new < v_floor THEN
        RAISE EXCEPTION 'wallet_below_floor: new=% floor=%', v_from_new, v_floor;
    END IF;

    UPDATE corporate_wallets SET balance = v_from_new, updated_at = now() WHERE id = p_from_wallet_id;
    UPDATE corporate_wallets SET balance = v_to_new,   updated_at = now() WHERE id = p_to_wallet_id;

    v_from_scope := CASE WHEN v_from.section_id IS NULL THEN 'master' ELSE 'section:' || v_from.section_id END;
    v_to_scope   := CASE WHEN v_to.section_id   IS NULL THEN 'master' ELSE 'section:' || v_to.section_id   END;

    INSERT INTO corporate_wallet_transactions
        (wallet_id, scope, type, amount, balance_after, stripe_payment_intent_id, actor_user_id, notes)
    VALUES
        (p_from_wallet_id, v_from_scope, 'allocation', -p_amount, v_from_new,
         CASE WHEN p_idempotency_key IS NULL THEN NULL ELSE p_idempotency_key || ':out' END,
         p_actor_user_id, p_notes)
    RETURNING id INTO v_out_id;

    INSERT INTO corporate_wallet_transactions
        (wallet_id, scope, type, amount, balance_after, stripe_payment_intent_id, actor_user_id, notes)
    VALUES
        (p_to_wallet_id, v_to_scope, 'allocation', p_amount, v_to_new,
         CASE WHEN p_idempotency_key IS NULL THEN NULL ELSE p_idempotency_key || ':in' END,
         p_actor_user_id, p_notes)
    RETURNING id INTO v_in_id;

    out_txn_id   := v_out_id;
    in_txn_id    := v_in_id;
    from_balance := v_from_new;
    to_balance   := v_to_new;
    RETURN NEXT;
END
$$;

-- Lockdown (migration 203 pattern): backend-only via service_role.
REVOKE EXECUTE ON FUNCTION corporate_wallet_transfer(UUID, UUID, NUMERIC, TEXT, TEXT, TEXT, NUMERIC)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION corporate_wallet_transfer(UUID, UUID, NUMERIC, TEXT, TEXT, TEXT, NUMERIC)
    TO service_role;
REVOKE EXECUTE ON FUNCTION corporate_wallet_apply_delta(UUID, TEXT, TEXT, NUMERIC, UUID, UUID, TEXT, TEXT, TEXT, NUMERIC)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION corporate_wallet_apply_delta(UUID, TEXT, TEXT, NUMERIC, UUID, UUID, TEXT, TEXT, TEXT, NUMERIC)
    TO service_role;
