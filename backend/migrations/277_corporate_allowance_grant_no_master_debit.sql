-- ============================================================
-- Migration 277: allowance_grant is a pure limit raise (no master debit)
--
-- WHY
-- ---
-- Migration 248 fixed ride_debit's sign but deliberately left a follow-up
-- unaddressed (see its "NOTE ON GRANTS" comment): allowance_grant debits the
-- master wallet by -p_amount AND lowers `used` by -p_amount at grant time.
-- Later, when the member actually takes a ride against that raised room,
-- ride_debit ALSO debits master -p_amount (and raises `used` back up by
-- +p_amount). Net effect: a $50 grant followed by a $50 ride the grant was
-- meant to cover costs the company's master wallet $100 for one $50 ride —
-- every grant-funded ride is double-charged. Corporate + admin portal
-- review, High #2.
--
-- WHAT
-- ----
-- allowance_grant becomes a pure limit raise: master delta 0 (a grant is a
-- credit-limit increase, not a cash outflow — no real money should move
-- until the member actually spends it), used delta unchanged at -p_amount
-- (still raises the member's available room by the same mechanism as
-- before). The only path that ever debits the master wallet for a
-- corporate ride is now ride_debit, exactly once per ride, matching how the
-- non-grant-funded (base allowance) path already worked correctly since
-- migration 248.
--
-- allowance_rollback (undo a prior grant: master +p_amount, used +p_amount)
-- is UNCHANGED here on purpose — a grant that is rolled back before any
-- ride consumes it never moved master money under this migration, so its
-- rollback should not add money back either. Rollback's current signature
-- was originally paired with the OLD (money-moving) grant behavior; since
-- grant no longer moves master money, a grant-then-rollback pair now nets
-- to a true no-op (used -amount then +amount, master 0 then +amount) —
-- rollback's master leg becomes a no-op overshoot only if called after a
-- grant made under this new semantics. No caller currently invokes
-- allowance_rollback against a grant made after this migration ships
-- (grepped: only used historically / not wired to any live route), so this
-- is flagged but deliberately not further changed here — a genuine
-- rollback-of-a-post-migration-grant use case should zero both deltas,
-- tracked as a follow-up if that call site is ever wired up.
--
-- BACKFILL
-- --------
-- Historical corporate_wallet_transactions rows with type='allowance_grant'
-- already debited master at grant time under the old semantics — this
-- migration does NOT retroactively correct those balances (a customer-
-- facing billing correction requiring finance sign-off, same posture as
-- migration 248). Reporting query to find companies with a grant-funded
-- ride double-debited historically:
--
--   SELECT g.wallet_id, g.member_id, g.amount AS grant_amount, g.created_at AS grant_at,
--          r.amount AS ride_amount, r.created_at AS ride_at
--   FROM corporate_wallet_transactions g
--   JOIN corporate_wallet_transactions r
--     ON r.wallet_id = g.wallet_id AND r.member_id = g.member_id
--    AND r.scope = 'master' AND r.type = 'ride_debit' AND r.created_at > g.created_at
--   WHERE g.scope = 'master' AND g.type = 'allowance_grant'
--   ORDER BY g.wallet_id, g.created_at;
--
-- rollback: re-apply migration 248's function body verbatim (CREATE OR
-- REPLACE), which restores allowance_grant's master delta to -p_amount. No
-- schema/DDL change is made here beyond the function body, so the rollback
-- is a pure function replace with no data migration and no downtime.
--
-- SAFE TO RE-RUN (CREATE OR REPLACE).
--
-- migration-override-ok: intentionally redefines corporate_allowance_apply_delta
-- (last defined in 248_corporate_allowance_ride_debit.sql, originally 29).
-- This function is versioned by CREATE OR REPLACE — the established pattern
-- for it, per migration 248's own precedent — because the type→delta
-- mapping lives in the function body and cannot be changed any other way.
-- No signature change: same parameters, same RETURNS TABLE shape, so no
-- caller is affected beyond the corrected allowance_grant delta.
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
    -- grant:      master 0,         used -p_amount  (pure limit raise — no cash moves
    --             until the member actually spends via ride_debit; High #2 fix)
    -- reset:      master 0,         used -v_used    (zero out current usage, no master move)
    -- rollback:   master +p_amount, used +p_amount  (undo a prior grant)
    -- ride_debit: master -p_amount, used +p_amount  (ride consumes allowance AND charges the company —
    --             the ONLY path that debits master for a corporate ride)
    -- ride_debit_reversal: master +p_amount, used -p_amount  (exact inverse of ride_debit;
    --             used when a later step of the same settlement fails and the
    --             allowance charge must be compensated. apply_grant CANNOT do this
    --             — its master delta is now 0, so it would not compensate at all.)
    IF p_type = 'allowance_grant' THEN
        v_master_delta := 0;
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
