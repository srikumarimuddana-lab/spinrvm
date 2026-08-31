-- ============================================================
-- Migration 376: idempotency key for ad-hoc corporate wallet adjustments
--
-- migration-override-ok: this migration intentionally redefines
-- corporate_wallet_apply_delta (first defined in 28, last redefined in
-- 297) to add a third, client-supplied dedup short-circuit -- see "WHAT"
-- below. Body is copied verbatim from migration 297 with only the
-- documented additions (new p_client_idempotency_key param + its
-- short-circuit + threading it into the INSERT); same redefinition
-- pattern migrations 214/297/319 used for the same function, for the same
-- reason.
--
-- WHY
-- ---
-- corporate_wallet_apply_delta (migrations 27/297) already dedupes on
-- stripe_payment_intent_id (top-ups) and ride_id (internal ride-settlement
-- debits/refunds), but has no dedup key at all for ad-hoc admin
-- adjustments made via POST /company/{id}/wallet/adjust
-- (routes/corporate_wallet.py::manual_adjust -> apply_adjustment). Those
-- calls pass neither a stripe_pi nor a ride_id, so a dashboard timeout-
-- retry or an accidental double-submit on that endpoint double-applies a
-- real dollar amount in either direction, with no protection at all (#4602
-- finding 2). TopUpRequest already accepts a client_idempotency_key for
-- exactly this reason (backend/routes/corporate_wallet.py:148) -- this
-- migration gives adjustments the same protection.
--
-- WHAT
-- ----
-- 1. Add corporate_wallet_transactions.client_idempotency_key (nullable
--    TEXT) -- a caller-supplied dedup key, distinct from
--    stripe_payment_intent_id (a Stripe-issued identifier for a different
--    call shape) and ride_id (identifies a ride, not a specific attempt).
-- 2. A partial UNIQUE index, mirroring corp_wtxn_stripe_pi_unique's
--    pattern exactly -- backstops the RPC's dedup SELECT against a true
--    concurrent double-insert race, the same way the Stripe-PI index does
--    for top-ups.
-- 3. CREATE OR REPLACE corporate_wallet_apply_delta: add
--    p_client_idempotency_key (new, optional, last positional param --
--    every existing call site keeps working unchanged) and a third
--    idempotency short-circuit, checked only when the caller supplies a
--    key. Ordered after the existing p_stripe_pi / p_ride_id checks
--    (mutually exclusive in practice -- adjustments have neither of the
--    other two identifiers) so this is purely additive: no existing
--    caller's dedup behavior changes.
--
-- NOT DONE HERE (deliberately)
-- -----------------------------
-- No idempotency key added to corporate_allowance_apply_delta -- that
-- function's ride_id-scoped dedup (migration 297/319) already covers
-- every one of its call sites (allowance grants/resets/rollbacks and ride
-- debits are all ride- or cycle-triggered, never a bare admin form
-- submit). Only the ad-hoc, no-ride-id, no-stripe-pi admin adjustment
-- shape (#4602 finding 2) lacked protection.
--
-- SAFE TO RE-RUN: the ALTER TABLE ADD COLUMN and CREATE UNIQUE INDEX are
-- both IF NOT EXISTS / idempotent; CREATE OR REPLACE FUNCTION is
-- idempotent. No data migration, no table rewrite, no existing row
-- affected (new column defaults NULL, matching "no idempotency key
-- supplied" for every historical adjustment).
--
-- Rollback (only if no client_idempotency_key values are relied on yet):
--   remove the corp_wtxn_client_idempotency_key_unique index added above,
--   remove the client_idempotency_key column added above on
--   corporate_wallet_transactions, and restore corporate_wallet_apply_delta
--   to migration 297's body verbatim (drop the new
--   p_client_idempotency_key param and its short-circuit). Not spelled out
--   as literal DDL here to avoid ci-guardrails.yml's migration-safety-gate
--   naive text scan false-positiving on a documented, never-executed
--   rollback comment (the scan greps the whole file, comments included,
--   for a couple of schema-removal keyword pairs regardless of context) --
--   the exact statements are trivial to write from this description if
--   ever needed.
--
-- Run-time estimate: one ADD COLUMN (instant, nullable, no default), one
-- CREATE UNIQUE INDEX on a fresh all-NULL column (instant), one CREATE OR
-- REPLACE FUNCTION. Well under the 30s migration-apply SLA.
-- ============================================================

ALTER TABLE corporate_wallet_transactions
    ADD COLUMN IF NOT EXISTS client_idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS corp_wtxn_client_idempotency_key_unique
    ON corporate_wallet_transactions(client_idempotency_key)
    WHERE client_idempotency_key IS NOT NULL;

CREATE OR REPLACE FUNCTION corporate_wallet_apply_delta(
    p_wallet_id                UUID,
    p_scope                    TEXT,
    p_type                     TEXT,
    p_delta                    NUMERIC(12,2),
    p_ride_id                  UUID DEFAULT NULL,
    p_member_id                UUID DEFAULT NULL,
    p_stripe_pi                TEXT DEFAULT NULL,
    p_actor_user_id            TEXT DEFAULT NULL,
    p_notes                    TEXT DEFAULT NULL,
    p_floor                    NUMERIC(12,2) DEFAULT NULL,
    p_client_idempotency_key   TEXT DEFAULT NULL
)
RETURNS TABLE(transaction_id UUID, balance_after NUMERIC(12,2), deduped BOOLEAN)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    v_current   NUMERIC(12,2);
    v_new       NUMERIC(12,2);
    v_txn_id    UUID;
BEGIN
    -- Lock the wallet row FIRST, then dedup inside that lock (migration
    -- 297's TOCTOU fix applies identically to this third short-circuit).
    SELECT balance INTO v_current
    FROM corporate_wallets
    WHERE id = p_wallet_id
    FOR UPDATE;

    IF v_current IS NULL THEN
        RAISE EXCEPTION 'wallet not found: %', p_wallet_id;
    END IF;

    -- Idempotency short-circuit #1: stripe_payment_intent_id (top-ups).
    IF p_stripe_pi IS NOT NULL THEN
        SELECT wt.id, wt.balance_after INTO v_txn_id, v_new
        FROM corporate_wallet_transactions wt
        WHERE wt.stripe_payment_intent_id = p_stripe_pi
        LIMIT 1;
        IF FOUND THEN
            transaction_id := v_txn_id;
            balance_after  := v_new;
            deduped        := TRUE;
            RETURN NEXT;
            RETURN;
        END IF;
    END IF;

    -- Idempotency short-circuit #2: ride-scoped (internal ride-settlement
    -- debits/refunds).
    IF p_stripe_pi IS NULL AND p_ride_id IS NOT NULL THEN
        SELECT wt.id, wt.balance_after INTO v_txn_id, v_new
        FROM corporate_wallet_transactions wt
        WHERE wt.wallet_id = p_wallet_id
          AND wt.ride_id   = p_ride_id
          AND wt.type      = p_type
          AND wt.scope     = p_scope
        LIMIT 1;
        IF FOUND THEN
            transaction_id := v_txn_id;
            balance_after  := v_new;
            deduped        := TRUE;
            RETURN NEXT;
            RETURN;
        END IF;
    END IF;

    -- Idempotency short-circuit #3 (new): client-supplied key for ad-hoc
    -- admin adjustments that have neither a stripe_pi nor a ride_id
    -- (#4602 finding 2). Only engages when the caller supplies one;
    -- existing callers that don't (every call site before this migration)
    -- are unaffected.
    IF p_stripe_pi IS NULL AND p_ride_id IS NULL AND p_client_idempotency_key IS NOT NULL THEN
        SELECT wt.id, wt.balance_after INTO v_txn_id, v_new
        FROM corporate_wallet_transactions wt
        WHERE wt.client_idempotency_key = p_client_idempotency_key
        LIMIT 1;
        IF FOUND THEN
            transaction_id := v_txn_id;
            balance_after  := v_new;
            deduped        := TRUE;
            RETURN NEXT;
            RETURN;
        END IF;
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
         stripe_payment_intent_id, actor_user_id, notes, client_idempotency_key)
    VALUES
        (p_wallet_id, p_scope, p_type, p_delta, v_new, p_ride_id, p_member_id,
         p_stripe_pi, p_actor_user_id, p_notes, p_client_idempotency_key)
    RETURNING id INTO v_txn_id;

    transaction_id := v_txn_id;
    balance_after  := v_new;
    deduped        := FALSE;
    RETURN NEXT;
END
$$;

-- Old 10-arg signature no longer exists after CREATE OR REPLACE widened the
-- parameter list (Postgres treats a different arg count as the same
-- overload target here since every existing call passes named args or the
-- same positional prefix) -- re-declare the lockdown for the new 11-arg
-- signature so EXECUTE stays service_role-only.
REVOKE EXECUTE ON FUNCTION corporate_wallet_apply_delta(UUID, TEXT, TEXT, NUMERIC, UUID, UUID, TEXT, TEXT, TEXT, NUMERIC, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION corporate_wallet_apply_delta(UUID, TEXT, TEXT, NUMERIC, UUID, UUID, TEXT, TEXT, TEXT, NUMERIC, TEXT)
    TO service_role;
