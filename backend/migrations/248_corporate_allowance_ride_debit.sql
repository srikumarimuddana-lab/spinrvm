-- ============================================================
-- Migration 248: Corporate allowance ride_debit branch
--
-- WHY
-- ---
-- settle_corporate() charged the allowance-covered portion of a corporate ride
-- by calling corporate_allowance_apply_delta with p_type='allowance_rollback',
-- which maps to master +p_amount / used +p_amount. The `used` side is right for
-- a ride (consumption raises the usage counter) but the master side has the
-- WRONG SIGN: the company's master wallet balance INCREASED by the fare on
-- every allowance-covered ride instead of being charged. For a member on a base
-- allowance (amount=500, used=0) taking a $50 ride, master went UP $50 — company
-- funds grew because an employee took a ride, and the ride was never paid for.
--
-- WHAT
-- ----
-- Adds a dedicated 'ride_debit' type: master -p_amount, used +p_amount.
-- 'allowance_rollback' keeps its original meaning (undo a prior grant) and is no
-- longer used by ride settlement.
--
-- NOTE ON GRANTS (follow-up, deliberately not changed here)
-- --------------------------------------------------------
-- 'allowance_grant' also debits master (master -p_amount, used -p_amount) when
-- an admin approves a top-up request. Base allowances are never grant-funded, so
-- ride_debit is correct for the dominant path. Grant-funded spend is debited at
-- grant time AND at ride time until grant semantics are revisited (tracked as a
-- follow-up: make grant a pure limit raise). This is a strictly smaller exposure
-- than the bug being fixed, which mis-signed EVERY allowance-covered ride.
--
-- BACKFILL
-- --------
-- Historical corporate_wallet_transactions rows with type='allowance_rollback'
-- and notes LIKE 'ride:%:allowance' are mis-signed settlements; each overstates
-- the master balance by 2x the fare (credited instead of debited). This
-- migration does NOT auto-correct balances — that is a customer-facing billing
-- correction requiring finance sign-off. Reporting query:
--
--   SELECT wallet_id, count(*), sum(amount) AS overstated_by_1x
--   FROM corporate_wallet_transactions
--   WHERE scope = 'master' AND type = 'allowance_rollback'
--     AND notes LIKE 'ride:%:allowance'
--   GROUP BY wallet_id;
--
-- rollback: re-apply migration 29's function body verbatim (CREATE OR REPLACE),
-- then revert the payment_service call site to apply_rollback. No schema/DDL
-- change is made here beyond the function body, so the rollback is a pure
-- function replace with no data migration and no downtime.
--
-- SAFE TO RE-RUN (CREATE OR REPLACE).
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
SET search_path = public, pg_temp
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
    IF p_type NOT IN ('allowance_grant','allowance_reset','allowance_rollback','ride_debit','ride_debit_reversal') THEN
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
    -- grant:      master -p_amount, used -p_amount  (usage counter decreases → more available)
    -- reset:      master 0,         used -v_used    (zero out current usage, no master move)
    -- rollback:   master +p_amount, used +p_amount  (undo a prior grant)
    -- ride_debit: master -p_amount, used +p_amount  (ride consumes allowance AND charges the company)
    -- ride_debit_reversal: master +p_amount, used -p_amount  (exact inverse of ride_debit;
    --             used when a later step of the same settlement fails and the
    --             allowance charge must be compensated. apply_grant CANNOT do this
    --             — its master delta is negative, so it would charge again.)
    IF p_type = 'allowance_grant' THEN
        v_master_delta := -p_amount;
        v_used_delta   := -p_amount;
    ELSIF p_type = 'allowance_reset' THEN
        v_master_delta := 0;
        v_used_delta   := -v_used;
    ELSIF p_type = 'ride_debit' THEN
        v_master_delta := -p_amount;
        v_used_delta   := p_amount;
    ELSIF p_type = 'ride_debit_reversal' THEN
        v_master_delta := p_amount;
        v_used_delta   := -p_amount;
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
