-- ============================================================
-- Migration 297: Ride-scoped idempotency for the corporate money RPCs
--
-- WHY
-- ---
-- corporate_wallet_apply_delta already dedupes on p_stripe_pi (a UNIQUE
-- index, corp_wtxn_stripe_pi_unique from migration 27) but that's Stripe-
-- specific — it does nothing for the two INTERNAL debits settle_corporate
-- makes per ride (services/corporate_allowance_service.py apply_ride_debit,
-- services/corporate_wallet_service.py apply_adjustment for the
-- master-wallet fallback), neither of which carries a Stripe PI.
-- corporate_allowance_apply_delta has NO ride-scoping at all — it doesn't
-- even take a ride_id parameter; the caller only embeds one in a free-text
-- `notes` string ("ride:<id>:allowance"), which is not queryable or
-- dedupable.
--
-- settle_corporate (services/payment_service.py) has no outer try/except.
-- Several steps after the wallet/allowance debits succeed are unguarded
-- (the ride_payment_sources insert, the final payment_status='paid'
-- write) — if one of those throws, the ride is left debited but stuck at
-- payment_status='processing' with no compensating action. Today a
-- caller-side guard in routes/rides/payments.py (skip re-driving
-- 'processing' for non-wallet payment methods) happens to prevent a
-- second call to settle_corporate from ever landing — but that guard also
-- means the ride never reconciles either, and it provides zero protection
-- at the RPC layer itself: a future fix that adds corporate rides to the
-- existing retry/re-drive loop (a very plausible fix for the "stuck
-- processing" symptom) would immediately reintroduce a real double-debit,
-- because neither RPC has ever been able to tell "this ride's debit
-- already happened" from "this ride hasn't been charged yet."
--
-- Found by a fresh spinr-corporate-billing-reviewer audit of scheduled
-- rides (P0 finding #2) — flagged as a "landmine that's only inert today
-- because of one narrow caller-side guard."
--
-- WHAT
-- ----
-- 1. corporate_wallet_apply_delta: the existing p_ride_id parameter
--    (already present, already stored on every row via the
--    corp_wtxn_ride_idx partial index from migration 27) is now also used
--    for dedup, not just storage. When p_stripe_pi is NULL and p_ride_id
--    IS NOT NULL, a repeat call with the same (wallet_id, ride_id, type,
--    scope) returns the original transaction untouched instead of
--    re-applying the delta.
-- 2. corporate_allowance_apply_delta: adds a new p_ride_id UUID DEFAULT
--    NULL parameter, threaded into both ledger INSERTs (master + member
--    rows) via the ride_id column corporate_wallet_transactions already
--    has (previously NULL for every row this function wrote). Same dedup
--    semantics as above, keyed on (wallet_id, ride_id, type, member_id).
-- 3. Both functions now return an added `deduped BOOLEAN` output column
--    so the caller can distinguish "applied" from "no-op, already
--    applied" — mirrors wallet_apply_delta's own `deduped` column
--    (migration 249).
-- 4. Both functions' idempotency checks are moved to run AFTER the
--    row lock (FOR UPDATE) is acquired, not before. corporate_wallet_
--    apply_delta's existing p_stripe_pi check previously ran
--    check-before-lock (migration 249's own comment on this file calls
--    that out as strictly weaker than wallet_apply_delta's lock-then-check
--    ordering) — closing a genuine TOCTOU window: two concurrent calls
--    for the same (wallet, ride, type) could otherwise both pass the
--    dedup SELECT before either had inserted, and both apply the delta.
--    corporate_allowance_apply_delta already locked both rows first for
--    other reasons (deterministic lock order to prevent deadlock), so its
--    new dedup check slots in naturally after the existing locks with no
--    reordering needed.
-- 5. corporate_allowance_apply_delta: RESTORES the per-member allowance-cap
--    guard (v_cap / 'allowance_cap_exceeded') that migration 258 added and
--    migration 277 silently dropped — see "REGRESSION FOUND AND FIXED"
--    below. Unrelated to idempotency; bundled here because the function is
--    already being redefined for the idempotency fix and the requester
--    confirmed bundling over a separate migration.
--
-- NO UNIQUE INDEX (deliberately)
-- -------------------------------
-- Same reasoning as migration 249's wallet_apply_delta: a UNIQUE index on
-- (wallet_id, ride_id, type, scope) would be stronger than lock-then-check
-- alone, but CREATE UNIQUE INDEX would fail outright against any
-- pre-existing duplicate rows already created by the very bug this
-- migration fixes (a company that hit the described stuck-processing
-- retry path before today). The existing corp_wtxn_ride_idx partial index
-- (ride_id) from migration 27 already makes the new dedup SELECT cheap —
-- a ride has at most a handful of wallet_transaction rows — so no new
-- index is added here.
--
-- PRESERVED BEHAVIOR
-- -------------------
-- corporate_allowance_apply_delta's current type->delta mapping (migration
-- 277: allowance_grant is master-neutral, ride_debit is the only path that
-- debits master) is copied verbatim and NOT otherwise changed here.
--
-- REGRESSION FOUND AND FIXED (item 5 above)
-- --------------------------------------------
-- While tracing the true current body for this migration, a second, wholly
-- unrelated bug surfaced: migration 258 added a per-member allowance-cap
-- guard (v_cap / RAISE 'allowance_cap_exceeded' when a ride_debit would push
-- `used` past the allowance's `amount`) that closed a real double-spend race
-- (docs/change-log/2026-07-26-corporate-allowance-cap-race-fix.md). Migration
-- 277 based its own CREATE OR REPLACE on migration 248's body instead of
-- 258's (or 261's, which also carried the guard forward) when fixing an
-- unrelated grant-semantics bug, and silently dropped the guard in the
-- process — the per-employee spending cap has been unenforced in production
-- ever since 277 shipped. services/payment_service.py:493-536 still contains
-- exception handling for 'allowance_cap_exceeded' that has been dead code
-- since then, and backend/tests/test_corporate_allowance_cap_race.py tests a
-- hand-ported COPY of the locked-section algorithm, not the real function,
-- so it kept passing throughout the regression and gave false assurance.
--
-- Fix (item 5 above): restore the v_cap read + 'allowance_cap_exceeded'
-- guard verbatim from migration 258/261, on top of the 277 type->delta
-- mapping and the new idempotency logic. Confirmed with the requester
-- before bundling this into the same DROP+CREATE rather than a separate
-- migration — zero extra schema risk since this function is already being
-- redefined for the idempotency fix, and it closes a live money-cap gap
-- immediately instead of leaving it open for a second migration cycle. See
-- docs/change-log/2026-08-11-corporate-rpc-ride-idempotency.md ("Restored
-- per-member allowance cap" section) for the full before/after and
-- blast-radius discussion of this specific sub-fix.
--
-- FORWARD-COMPATIBLE, WITH ONE MANDATORY ORDERING REQUIREMENT
-- --------------------------------------------------------------
-- Old backend code calling the NEW db signature is fine (both new/extended
-- params default to NULL). The reverse is NOT automatically safe: NEW
-- backend code calling the OLD (pre-297) db signature.
-- corporate_wallet_apply_delta already had p_ride_id since migration 28, so
-- corporate_wallet_service.py's calls are safe against either signature
-- regardless of deploy order. corporate_allowance_apply_delta's p_ride_id
-- is genuinely NEW here — PostgREST resolves RPC calls by exact
-- named-parameter match, so a call carrying a p_ride_id key the DB function
-- doesn't yet declare fails to resolve (function does not exist), not
-- "ignores the extra key." services/corporate_allowance_service.py's
-- apply() only includes the p_ride_id key in the RPC payload when a caller
-- actually passes one, so apply_grant/apply_reset/apply_rollback (which
-- never do) are safe regardless of ordering — but apply_ride_debit and
-- apply_ride_debit_reversal (settle_corporate's ride-settlement path, the
-- money-critical one this migration exists for) always pass a real
-- ride_id, so THOSE calls will fail until this migration has been applied.
--
-- MANDATORY DEPLOY SEQUENCE: apply this migration to every environment's
-- Supabase instance (`python backend/scripts/migrate.py`) BEFORE deploying
-- the paired backend code (services/corporate_allowance_service.py,
-- services/corporate_wallet_service.py, services/payment_service.py). This
-- repo's Fly deploy triggers automatically on push to main with no
-- dependency on the (workflow_dispatch-only) migration runner, so pushing
-- both in the same commit does NOT guarantee ordering — treat this the same
-- way docs/runbooks/deploy-migration-64-65.md §2 treats its own
-- mandatory-sequence migration pair. See this change's Change Impact Log
-- (docs/change-log/2026-08-11-corporate-rpc-ride-idempotency.md) for the
-- explicit pre-deploy checklist.
--
-- Run-time estimate: two CREATE OR REPLACE-shaped function bodies, no
-- table rewrite, no data migration. Sub-second.
--
-- migration-override-ok: intentionally redefines corporate_wallet_apply_delta
-- (last defined in 214_corporate_actor_user_id_text.sql) and
-- corporate_allowance_apply_delta (last defined in
-- 277_corporate_allowance_grant_no_master_debit.sql). The idempotency
-- changes themselves are additive (new dedup path, new output column, new
-- optional parameter) — no existing caller's behavior changes for calls
-- that don't pass a ride_id/don't retry. The bundled cap-guard restoration
-- (item 5) is NOT purely additive: a ride_debit that would push a member's
-- `used` past their allowance `amount` now raises 'allowance_cap_exceeded'
-- again (as it correctly did under 258/261), where migration 277's
-- regression let it silently succeed. payment_service.settle_corporate
-- already has a live exception handler for this exact error string
-- (routes the fare to the master wallet instead) — restoring the guard
-- reactivates that existing, tested handler rather than requiring new
-- application code.
--
-- Both RETURNS TABLE column lists change (new `deduped` column), which
-- CREATE OR REPLACE FUNCTION cannot do — DROP FUNCTION first, matching the
-- pattern migrations 214/261 already established for this exact
-- constraint, then re-apply the migration-214/261 EXECUTE lockdown (DROP
-- drops grants) at the end.
--
-- Rollback: DROP FUNCTION IF EXISTS corporate_wallet_apply_delta(UUID,
-- TEXT, TEXT, NUMERIC, UUID, UUID, TEXT, TEXT, TEXT, NUMERIC) -- 10 args,
-- unchanged parameter count, only the RETURNS TABLE shape changed; DROP
-- FUNCTION IF EXISTS corporate_allowance_apply_delta(UUID, UUID, UUID,
-- TEXT, NUMERIC, TEXT, TEXT, NUMERIC, UUID) -- 9 args, the new trailing
-- p_ride_id; then re-apply migration 214's corporate_wallet_apply_delta
-- body verbatim and migration 277's corporate_allowance_apply_delta body
-- verbatim (both reproduced in full above them in this repo's migration
-- history), plus their EXECUTE grants. No data was migrated, so this is a
-- pure function-definition revert.
--
-- IMPORTANT #1: rolling back to migration 277's body ALSO removes the
-- restored allowance-cap guard (item 5) — a straight rollback reintroduces
-- the "per-employee cap unenforced" regression this migration also fixed.
-- If the idempotency logic is what's broken but the cap guard is fine,
-- prefer a new migration (298+) that keeps 258/261's v_cap guard and only
-- reverts the dedup/lock-ordering changes, over a full rollback to 277.
--
-- IMPORTANT #2: roll back the backend code (or at minimum revert
-- corporate_allowance_service.py's ride_id-passing call sites) BEFORE or
-- WITH this rollback — reverting the DB function first while ride-
-- settlement code still sends p_ride_id reintroduces the
-- exact "function does not exist" failure this migration's ordering
-- requirement exists to avoid, just in the opposite direction.
--
-- SAFE TO RE-RUN: DROP ... IF EXISTS is a no-op after the first apply;
-- CREATE OR REPLACE on the new signatures is idempotent.
-- ============================================================

-- ============================================================
-- 1. corporate_wallet_apply_delta
-- ============================================================
DROP FUNCTION IF EXISTS corporate_wallet_apply_delta(
    UUID, TEXT, TEXT, NUMERIC, UUID, UUID, TEXT, TEXT, TEXT, NUMERIC);

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
    -- Lock the wallet row FIRST, then dedup inside that lock -- a
    -- concurrent second caller for the same wallet blocks here until the
    -- first commits, then its own dedup SELECT below sees the just-
    -- inserted row. Previously the p_stripe_pi dedup check ran BEFORE this
    -- lock (see this migration's header comment); moving it after closes
    -- that TOCTOU window for both dedup paths.
    SELECT balance INTO v_current
    FROM corporate_wallets
    WHERE id = p_wallet_id
    FOR UPDATE;

    IF v_current IS NULL THEN
        RAISE EXCEPTION 'wallet not found: %', p_wallet_id;
    END IF;

    -- Idempotency short-circuit #1: stripe_payment_intent_id (unchanged
    -- dedup key; also backstopped by the corp_wtxn_stripe_pi_unique
    -- UNIQUE index from migration 27).
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

    -- Idempotency short-circuit #2 (new): ride-scoped dedup for the
    -- internal (non-Stripe) money movements settle_corporate makes per
    -- ride -- the master-wallet fallback debit and refunds. Only engages
    -- when the caller passes a ride_id; existing callers that don't
    -- (admin manual adjustments, top-ups without a ride) are unaffected.
    --
    -- NOTE (latent, not exploitable today): this key intentionally omits
    -- member_id. Every current ride+scope='master' caller (apply_adjustment,
    -- apply_refund) has at most one master-scope row per (wallet, ride,
    -- type), so this is safe. If a future member-SCOPED, ride-scoped
    -- wallet-level call is ever added, two different members' rows for the
    -- same ride could false-dedupe against each other under this key --
    -- widen to (wallet_id, ride_id, type, scope, member_id) if that
    -- call shape is ever introduced.
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
    deduped        := FALSE;
    RETURN NEXT;
END
$$;

REVOKE EXECUTE ON FUNCTION corporate_wallet_apply_delta(UUID, TEXT, TEXT, NUMERIC, UUID, UUID, TEXT, TEXT, TEXT, NUMERIC)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION corporate_wallet_apply_delta(UUID, TEXT, TEXT, NUMERIC, UUID, UUID, TEXT, TEXT, TEXT, NUMERIC)
    TO service_role;

-- ============================================================
-- 2. corporate_allowance_apply_delta
-- ============================================================
DROP FUNCTION IF EXISTS corporate_allowance_apply_delta(
    UUID, UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, NUMERIC);

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

    -- Read the usage counter AND the ceiling under the same row lock so the
    -- cap check below is atomic against a concurrent settle of the same
    -- member (migration 258's guard, RESTORED here -- see this migration's
    -- "REGRESSION FOUND AND FIXED" header section for why it was missing).
    SELECT used, amount INTO v_used, v_cap
    FROM corporate_member_allowances
    WHERE id = p_allowance_id
    FOR UPDATE;
    IF v_used IS NULL THEN
        RAISE EXCEPTION 'allowance not found: %', p_allowance_id;
    END IF;

    -- Idempotency short-circuit (new): both locks above are already held,
    -- so this check is race-free against a concurrent identical call --
    -- it either sees no row yet (proceeds) or blocks on the lock until the
    -- first caller commits, then sees the row this check looks for.
    -- Master + member rows are always inserted together by this function,
    -- so finding the master row is sufficient evidence the pair exists;
    -- the member row is looked up the same way to return its data too.
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

    -- Map type → (master delta, used delta). Verbatim from migration 277
    -- (allowance_grant is a pure limit raise, no master debit; ride_debit
    -- is the only path that debits master for a corporate ride).
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

    -- Per-member ceiling — ride_debit only, and only for capped (non-unlimited)
    -- allowances. Migration 258's guard, RESTORED here. This is the atomic
    -- gate that a non-locking application-side min(remaining, total) split
    -- cannot provide. On breach the caller (payment_service.settle_corporate)
    -- catches 'allowance_cap_exceeded' and routes the fare to the master
    -- wallet instead.
    IF p_type = 'ride_debit' AND v_cap IS NOT NULL AND v_used_new > v_cap THEN
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

REVOKE EXECUTE ON FUNCTION corporate_allowance_apply_delta(UUID, UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, NUMERIC, UUID)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION corporate_allowance_apply_delta(UUID, UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, NUMERIC, UUID)
    TO service_role;
