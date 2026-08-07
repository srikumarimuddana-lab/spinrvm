-- 288_settle_ride_card_payment.sql
-- Atomic card-settlement finalizer: rides.payment_status flip + the
-- financial_events tax-ledger header in ONE transaction.
--
-- Context: after a Stripe capture/charge succeeds, the Python side previously
-- issued two separate writes — insert the financial_events header, then
-- update_ride to 'paid'. Process death between the Stripe call and those
-- writes (or between them) left either a paid ride with no tax-ledger row
-- (revenue/GST under-reported for 7-year CRA records) or a ledger row with
-- the ride stuck in 'processing'. Retries narrowed the window; this closes
-- it: both rows commit or neither does.
--
-- Modeled on wallet_pay_for_ride (migrations 50/107/110/111): row lock,
-- already-paid idempotency gate returning NULL, tip delta computed IN-DB
-- under the lock. The delta mirrors payment_service._tip_ride_update
-- exactly: applied in BOTH directions (a downward tip correction claws the
-- over-credit back out of driver_earnings), clamped at 0 — NOT the
-- increase-only guard wallet_pay_for_ride uses.
--
-- Cross-attempt duplicate-header guard is the payment_status='paid' gate,
-- NOT the ON CONFLICT (id): each Python attempt generates a fresh
-- p_event_id, so ON CONFLICT only dedupes a retry of the SAME attempt
-- (e.g. lost response). Do not "simplify" one away — they cover different
-- replays.
--
-- Rollback plan (no second deploy):
--   1. app_settings.ledger_atomic_settle_enabled = false  (callers fall back
--      to the legacy two-write path immediately; this function goes unused)
--   2. If the function itself must go:
--        DROP FUNCTION IF EXISTS settle_ride_card_payment(text, uuid, text, bigint, text, numeric, jsonb, text);
--   Rows it already wrote are indistinguishable from legacy-path rows.
--
-- Forward-compatible: new function only; no table altered, no backfill.

-- CREATE OR REPLACE cannot change a signature and a different parameter list
-- would silently coexist as an overload (migration 111 incident) — drop first.
DROP FUNCTION IF EXISTS settle_ride_card_payment(text, uuid, text, bigint, text, numeric, jsonb, text);

CREATE OR REPLACE FUNCTION settle_ride_card_payment(
    p_ride_id           text,
    p_event_id          uuid,
    p_user_id           text,
    p_amount_cents      bigint,
    p_payment_intent_id text,
    p_tip_amount        numeric,
    p_metadata          jsonb,
    p_auth_status       text DEFAULT NULL
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_status          text;
    v_tip_existing    numeric;
    v_earnings        numeric;
    v_tip_delta       numeric;
BEGIN
    IF p_amount_cents IS NULL OR p_amount_cents < 0 THEN
        RAISE EXCEPTION 'settle_ride_card_payment: invalid amount %', p_amount_cents;
    END IF;

    SELECT payment_status,
           COALESCE(tip_amount, 0),
           COALESCE(driver_earnings, 0)
      INTO v_status, v_tip_existing, v_earnings
      FROM rides
     WHERE id = p_ride_id
       FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'ride not found' USING ERRCODE = 'P0002';
    END IF;

    -- Idempotency gate: a replay of an attempt that already committed (or a
    -- concurrent second settle) is a no-op. NULL (not the event id) so the
    -- caller can distinguish "settled now" from "was already settled" and
    -- skip receipt/WS side effects accordingly.
    IF v_status = 'paid' THEN
        RETURN NULL;
    END IF;

    -- Tip delta under the row lock, both directions, clamped at 0 —
    -- byte-for-byte the semantics of payment_service._tip_ride_update.
    v_tip_delta := COALESCE(p_tip_amount, 0) - v_tip_existing;
    IF v_tip_delta <> 0 THEN
        v_earnings := GREATEST(v_earnings + v_tip_delta, 0);
    END IF;

    UPDATE rides
       SET payment_status    = 'paid',
           payment_intent_id = p_payment_intent_id,
           tip_amount        = COALESCE(p_tip_amount, 0),
           driver_earnings   = v_earnings,
           auth_status       = COALESCE(p_auth_status, auth_status),
           updated_at        = now()
     WHERE id = p_ride_id;

    -- The 7-year tax-ledger header, in the same transaction as the flip.
    -- ON CONFLICT covers a same-attempt replay (client retried after a lost
    -- response with the same p_event_id); the paid-gate above covers
    -- cross-attempt replays (fresh p_event_id).
    INSERT INTO financial_events (id, event_type, user_id, ride_id, delta_cents, ref, metadata, created_at)
    VALUES (p_event_id, 'stripe_charge', p_user_id, p_ride_id, p_amount_cents, p_payment_intent_id,
            COALESCE(p_metadata, '{}'::jsonb), now())
    ON CONFLICT (id) DO NOTHING;

    RETURN p_event_id::text;
END;
$$;

-- Money-mutating: backend service role only. Migration-205 grant form —
-- revoking PUBLIC also strips service_role's inherited EXECUTE, so grant it
-- back explicitly.
REVOKE EXECUTE ON FUNCTION settle_ride_card_payment(text, uuid, text, bigint, text, numeric, jsonb, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION settle_ride_card_payment(text, uuid, text, bigint, text, numeric, jsonb, text)
    TO service_role;

COMMENT ON FUNCTION settle_ride_card_payment(text, uuid, text, bigint, text, numeric, jsonb, text) IS
    'Atomic card-settlement finalizer: flips rides.payment_status to paid '
    '(tip delta computed under the row lock, both directions, clamped at 0) '
    'and inserts the financial_events header in the same transaction. '
    'Returns the event id, or NULL when the ride was already paid. Called '
    'behind the ledger_atomic_settle_enabled app-settings flag with legacy '
    'two-write fallback. Service-role only. Migration 288.';
