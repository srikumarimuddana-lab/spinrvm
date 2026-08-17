-- ============================================================
-- Migration 319: late-tip debit types (wallet + corporate)
--
-- WHY A NEW TYPE INSTEAD OF REUSING 'ride_payment' / 'ride_debit' / 'adjustment'
-- -------------------------------------------------------------------------
-- wallet_apply_delta (migration 249) and corporate_wallet_apply_delta /
-- corporate_allowance_apply_delta (migration 297) all dedupe on
-- (wallet_id, reference_id / ride_id, type) — a plain SELECT against the
-- ledger table that matches ANY existing row with the same key, regardless
-- of how that row was inserted. The ORIGINAL ride settlement already writes
-- a wallet_transactions row with type='ride_payment' (settle_wallet,
-- services/payment_service.py) and/or corporate_wallet_transactions rows
-- with type='ride_debit' / type='adjustment' (settle_corporate), all keyed
-- on the ride's own id. A tip debited AFTER that settlement — this
-- migration's whole purpose — reuses the same ride_id. Reusing any of
-- those original types for the late-tip debit would silently DEDUPLICATE
-- against the original settlement row: the RPC returns "deduped=true" and
-- applies zero additional money movement while the caller still reports
-- success. That is exactly the "looks collected, wasn't" failure this
-- late-tip work exists to close (docs/proposals/2026-08-17-tip-capture-
-- stripe-cost-minimization-strategy.md Finding 1; docs/change-log/2026-08-17
-- -add-tip-late-charge-guard.md). New, disjoint type values sidestep it.
--
-- WHAT
-- ----
-- 1. wallet_transactions.type: add 'late_tip_debit' (personal wallet).
-- 2. corporate_wallet_transactions.type: add 'late_tip_debit' (member
--    allowance debit, written by corporate_allowance_apply_delta) and
--    'late_tip_adjustment' (master-wallet fallback debit when the
--    allowance can't cover it, written by corporate_wallet_apply_delta —
--    disjoint from settle_corporate's own master-fallback type
--    'adjustment' for the same dedup reason as above).
-- 3. corporate_allowance_apply_delta: CREATE OR REPLACE (body copied
--    verbatim from migration 297) to accept 'late_tip_debit' with the same
--    master/used delta math as 'ride_debit' (master -amount, used
--    +amount), and extend the per-member allowance-cap guard to cover it
--    too — the existing guard checks p_type = 'ride_debit' only; without
--    this addition the per-member cap would silently go unenforced for
--    late-tip debits.
-- 4. corporate_wallet_apply_delta needs NO function-body change — unlike
--    corporate_allowance_apply_delta, it never validates p_type itself;
--    only the table CHECK constraint gates it (widened in step 2).
--
-- BONUS FIX FOUND WHILE WRITING THIS (unrelated to late tips, bundled here
-- with the same justification migration 297 itself used for its own
-- bundled fix — see that file's "REGRESSION FOUND AND FIXED" section)
-- -------------------------------------------------------------------------
-- corporate_allowance_apply_delta has accepted and inserted
-- type='ride_debit_reversal' since migration 248 (the settle_corporate
-- compensation path: reverse the allowance debit if the master-wallet
-- fallback then fails — services/payment_service.py, the
-- apply_ride_debit_reversal call in settle_corporate's except block).
-- corporate_wallet_transactions' table-level CHECK constraint, however,
-- has NEVER included 'ride_debit_reversal' — migration 27 defined the
-- original 7-value list and no migration since has touched it (confirmed
-- by grepping every migration that references corporate_wallet_transactions
-- for a CHECK/ALTER on the type column). Every real compensation reversal
-- has therefore been hitting a Postgres 23514 check-violation since
-- migration 248 shipped. The failure IS caught (settle_corporate's
-- try/except around apply_ride_debit_reversal logs "Allowance compensation
-- failed ... manual ledger fix required" and returns a 503 rather than
-- crashing), so this has not been silent data corruption — but every time
-- this rare compensation path has fired (master-wallet debit failing right
-- after an allowance debit succeeded), the allowance was left over-debited
-- relative to what was actually collected, requiring a MANUAL ledger fix
-- every single time instead of the automatic reversal the code already
-- believes it's performing. Adding 'ride_debit_reversal' to the table
-- CHECK constraint here (step 2) closes it, since this migration is
-- already recreating that exact constraint for the late-tip types.
--
-- NOT DONE HERE (deliberately)
-- -----------------------------
-- No 'late_tip_debit_reversal' / compensation type for the new late-tip
-- path. The original settlement's allowance-then-master-wallet saga
-- reverses the allowance debit if the master-wallet fallback then fails
-- because that flow must be all-or-nothing — the ride has to be fully paid
-- or the caller retries with a different payment method. A late tip has no
-- such constraint: per the 2026-08-17 trust-first product decision (see the
-- Change Impact Log for this migration's paired code change), partially
-- collecting from the allowance and absorbing only the master-side
-- remainder when the fallback debit itself fails is the INTENDED outcome,
-- not a failure to compensate away. No reversal type is needed for that.
--
-- SAFE TO RE-RUN: both CHECK constraint DROP+ADD statements are idempotent
-- and are supersets of the existing allowlists, so every existing row
-- stays valid and both ADD CONSTRAINT statements validate instantly (same
-- precedent as migrations 198/199 for wallet_transactions, 214/297 for the
-- corporate RPCs). CREATE OR REPLACE FUNCTION is idempotent.
--
-- Rollback: only if no 'late_tip_debit' / 'late_tip_adjustment' rows exist
-- yet — 'ride_debit_reversal' rows may legitimately exist post-apply from
-- the bonus fix, so do NOT drop that value on rollback.
--   ALTER TABLE wallet_transactions DROP CONSTRAINT IF EXISTS wallet_transactions_type_check;
--   ALTER TABLE wallet_transactions ADD CONSTRAINT wallet_transactions_type_check
--       CHECK (type IN ('top_up','ride_payment','ride_refund','bonus','referral',
--           'referral_reward','referral_bonus','cashout','fare_split_received',
--           'fare_split_sent','fare_split_refund','quest_reward','admin_credit','admin_debit'));
--   ALTER TABLE corporate_wallet_transactions DROP CONSTRAINT IF EXISTS corporate_wallet_transactions_type_check;
--   ALTER TABLE corporate_wallet_transactions ADD CONSTRAINT corporate_wallet_transactions_type_check
--       CHECK (type IN ('topup','allowance_grant','allowance_reset','allowance_rollback',
--           'ride_debit','ride_debit_reversal','refund','adjustment'));
--   -- restore corporate_allowance_apply_delta to migration 297's body verbatim
--
-- Run-time estimate: two CHECK constraint DROP+ADD (instant, superset
-- validation) and one CREATE OR REPLACE FUNCTION-shaped body, no data
-- migration, no table rewrite. Well under the 30s migration-apply SLA.
-- ============================================================

ALTER TABLE wallet_transactions DROP CONSTRAINT IF EXISTS wallet_transactions_type_check;

ALTER TABLE wallet_transactions
    ADD CONSTRAINT wallet_transactions_type_check
    CHECK (type IN (
        'top_up',
        'ride_payment',
        'ride_refund',
        'bonus',
        'referral',
        'referral_reward',
        'referral_bonus',
        'cashout',
        'fare_split_received',
        'fare_split_sent',
        'fare_split_refund',
        'quest_reward',
        'admin_credit',
        'admin_debit',
        'late_tip_debit'
    ));

ALTER TABLE corporate_wallet_transactions DROP CONSTRAINT IF EXISTS corporate_wallet_transactions_type_check;

ALTER TABLE corporate_wallet_transactions
    ADD CONSTRAINT corporate_wallet_transactions_type_check
    CHECK (type IN (
        'topup',
        'allowance_grant',
        'allowance_reset',
        'allowance_rollback',
        'ride_debit',
        'ride_debit_reversal',
        'refund',
        'adjustment',
        'late_tip_debit',
        'late_tip_adjustment'
    ));

-- Re-create corporate_allowance_apply_delta, body copied verbatim from
-- migration 297, with 'late_tip_debit' added to the type whitelist, its
-- master/used delta mapping (identical math to 'ride_debit'), and the
-- per-member allowance-cap guard.
CREATE OR REPLACE FUNCTION corporate_allowance_apply_delta(
    p_wallet_id          UUID,
    p_allowance_id       UUID,
    p_member_id          UUID,
    p_type               TEXT,
    p_amount             NUMERIC(12,2),
    p_actor_user_id      TEXT DEFAULT NULL,
    p_notes              TEXT DEFAULT NULL,
    p_floor              NUMERIC(12,2) DEFAULT NULL,
    p_ride_id            UUID DEFAULT NULL
)
RETURNS TABLE(
    master_txn_id        UUID,
    member_txn_id        UUID,
    master_balance_after NUMERIC(12,2),
    allowance_used_after NUMERIC(12,2),
    deduped               BOOLEAN
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
    v_cap            NUMERIC(12,2);
    v_master_delta   NUMERIC(12,2);
    v_used_delta     NUMERIC(12,2);
    v_master_txn     UUID;
    v_member_txn     UUID;
BEGIN
    IF p_type NOT IN ('allowance_grant','allowance_reset','allowance_rollback','ride_debit','ride_debit_reversal','late_tip_debit') THEN
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

    -- Read the usage counter AND the ceiling under the same row lock so the
    -- cap check below is atomic against a concurrent settle of the same
    -- member (migration 258's guard, carried forward here).
    SELECT used, amount INTO v_used, v_cap
    FROM corporate_member_allowances
    WHERE id = p_allowance_id
    FOR UPDATE;
    IF v_used IS NULL THEN
        RAISE EXCEPTION 'allowance not found: %', p_allowance_id;
    END IF;

    -- Idempotency short-circuit: both locks above are already held, so this
    -- check is race-free against a concurrent identical call — it either
    -- sees no row yet (proceeds) or blocks on the lock until the first
    -- caller commits, then sees the row this check looks for. Master +
    -- member rows are always inserted together by this function, so
    -- finding the master row is sufficient evidence the pair exists; the
    -- member row is looked up the same way to return its data too.
    IF p_ride_id IS NOT NULL THEN
        SELECT wt.id, wt.balance_after INTO v_master_txn, v_master_new
        FROM corporate_wallet_transactions wt
        WHERE wt.wallet_id = p_wallet_id
          AND wt.ride_id   = p_ride_id
          AND wt.type      = p_type
          AND wt.member_id = p_member_id
          AND wt.scope     = 'master'
        LIMIT 1;

        IF FOUND THEN
            SELECT wt.id, wt.balance_after INTO v_member_txn, v_used_new
            FROM corporate_wallet_transactions wt
            WHERE wt.wallet_id = p_wallet_id
              AND wt.ride_id   = p_ride_id
              AND wt.type      = p_type
              AND wt.member_id = p_member_id
              AND wt.scope     = 'member:' || p_member_id::text
            LIMIT 1;

            master_txn_id        := v_master_txn;
            member_txn_id        := v_member_txn;
            master_balance_after := v_master_new;
            allowance_used_after := v_used_new;
            deduped               := TRUE;
            RETURN NEXT;
            RETURN;
        END IF;
    END IF;

    -- Map type → (master delta, used delta). 'late_tip_debit' uses the
    -- exact same math as 'ride_debit' — it IS a ride debit, just applied
    -- after settlement instead of during it; kept as a separate ELSIF
    -- branch (rather than merged into the 'ride_debit' condition) so the
    -- two remain independently greppable/auditable as this function
    -- evolves.
    IF p_type = 'allowance_grant' THEN
        v_master_delta := 0;
        v_used_delta   := -p_amount;
    ELSIF p_type = 'allowance_reset' THEN
        v_master_delta := 0;
        v_used_delta   := -v_used;
    ELSIF p_type = 'ride_debit' THEN
        v_master_delta := -p_amount;
        v_used_delta   := p_amount;
    ELSIF p_type = 'late_tip_debit' THEN
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

    -- Per-member ceiling — ride_debit AND late_tip_debit, only for capped
    -- (non-unlimited) allowances. Migration 258's guard, extended here to
    -- cover the new type: without this a late tip would bypass the
    -- per-employee spending cap entirely. On breach the caller
    -- (payment_service.charge_late_corporate_tip) catches
    -- 'allowance_cap_exceeded' and routes the tip to the master wallet
    -- instead, mirroring settle_corporate's own contention-handling.
    IF p_type IN ('ride_debit', 'late_tip_debit') AND v_cap IS NOT NULL AND v_used_new > v_cap THEN
        RAISE EXCEPTION 'allowance_cap_exceeded: used_new=% cap=%', v_used_new, v_cap;
    END IF;

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
        (wallet_id, scope, type, amount, balance_after, ride_id, member_id, actor_user_id, notes)
    VALUES
        (p_wallet_id, 'master', p_type, v_master_delta, v_master_new,
         p_ride_id, p_member_id, p_actor_user_id, p_notes)
    RETURNING id INTO v_master_txn;

    INSERT INTO corporate_wallet_transactions
        (wallet_id, scope, type, amount, balance_after, ride_id, member_id, actor_user_id, notes)
    VALUES
        (p_wallet_id, 'member:' || p_member_id::text, p_type, v_used_delta, v_used_new,
         p_ride_id, p_member_id, p_actor_user_id, p_notes)
    RETURNING id INTO v_member_txn;

    master_txn_id        := v_master_txn;
    member_txn_id        := v_member_txn;
    master_balance_after := v_master_new;
    allowance_used_after := v_used_new;
    deduped               := FALSE;
    RETURN NEXT;
END
$$;

-- Function bodies redefined via CREATE OR REPLACE preserve their existing
-- grants in Postgres (unlike DROP+CREATE), so no re-GRANT/REVOKE block is
-- needed here — migration 297's own EXECUTE lockdown (REVOKE PUBLIC/anon/
-- authenticated, GRANT service_role) carries forward unchanged.
