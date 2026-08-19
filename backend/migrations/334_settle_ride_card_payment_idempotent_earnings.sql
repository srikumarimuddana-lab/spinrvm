-- 334_settle_ride_card_payment_idempotent_earnings.sql
-- Replaces settle_ride_card_payment's tip-delta driver_earnings math with a
-- fresh, idempotent recompute — closing a real production underpayment
-- (2026-08-19 live-testing incident).
--
-- Context: migration 288's RPC computed v_earnings as
-- "COALESCE(driver_earnings,0) + (p_tip_amount - COALESCE(tip_amount,0))" —
-- a DELTA against whatever tip_amount/driver_earnings currently held. That
-- is only correct if tip_amount and driver_earnings always move together
-- through THIS function specifically. But two other code paths can also
-- write tip_amount to the same ride independently:
--   - routes/rides/rating.py's tip-while-rating flow (before payment)
--   - routes/rides/payments.py's add_tip late-tip flow (after payment)
-- A real ride that touched more than one of these landed with
-- driver_earnings = $0.17 (base fare only) instead of $0.67 (base + the
-- $0.50 tip the rider was actually, correctly, charged $0.68 total for via
-- Stripe — confirmed against financial_events: the CHARGE was correct, only
-- the earnings credit was wrong). The rider was never overcharged; the
-- driver was underpaid.
--
-- Fix: driver_earnings is now computed the same way every time, from the
-- ride's own persisted fare columns (total_fare - admin_earnings + tip),
-- matching backend/services/fare_service.py's driver_earnings_with_tip() —
-- the same Python-side fix applied to routes/rides/rating.py,
-- routes/rides/payments.py's add_tip, and services/payment_service.py's
-- _tip_ride_update in the same change. Naturally idempotent: no delta
-- bookkeeping needed, call order and staleness stop mattering.
--
-- admin_earnings = booking_fee + airport_fee only (CLAUDE.md: 0% commission
-- on the ride fare — the driver keeps everything else, including the
-- minimum-fare uplift; see fare_service.calculate_fare's own docstring).
--
-- Rollback: set app_settings.ledger_atomic_settle_enabled = false to route
-- callers to the legacy Python fallback (services/payment_service.py's
-- _tip_ride_update, fixed in the same change to the same formula) without a
-- second deploy. To fully remove this function version:
--   DROP FUNCTION IF EXISTS settle_ride_card_payment(text, uuid, text, bigint, text, numeric, jsonb, text);
-- then re-apply migration 288's original body if the RPC itself must be
-- reverted (not recommended — reintroduces the underpayment bug).
--
-- Forward-compatible: function redefinition only (migration-override-ok,
-- same class of change as migration 214/319's CREATE OR REPLACE precedent
-- for money RPCs). No table altered, no backfill.

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
    v_total_fare      numeric;
    v_booking_fee     numeric;
    v_airport_fee     numeric;
    v_base_earnings   numeric;
    v_earnings        numeric;
BEGIN
    IF p_amount_cents IS NULL OR p_amount_cents < 0 THEN
        RAISE EXCEPTION 'settle_ride_card_payment: invalid amount %', p_amount_cents;
    END IF;

    SELECT payment_status,
           COALESCE(total_fare, 0),
           COALESCE(booking_fee, 0),
           COALESCE(airport_fee, 0)
      INTO v_status, v_total_fare, v_booking_fee, v_airport_fee
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

    -- Canonical, idempotent driver_earnings — mirrors
    -- fare_service.driver_earnings_with_tip() exactly. Never a delta against
    -- the ride's current tip_amount/driver_earnings: those can have been set
    -- by a different code path already (rating.py, add_tip), and a delta
    -- against the wrong baseline is exactly what caused the underpayment
    -- this migration fixes.
    v_base_earnings := GREATEST(v_total_fare - (v_booking_fee + v_airport_fee), 0);
    v_earnings := v_base_earnings + COALESCE(p_tip_amount, 0);

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
-- back explicitly. CREATE OR REPLACE (not DROP+CREATE) preserves the
-- existing grants below regardless, but re-issuing them is cheap insurance
-- against a future edit that switches to DROP+CREATE without noticing.
REVOKE EXECUTE ON FUNCTION settle_ride_card_payment(text, uuid, text, bigint, text, numeric, jsonb, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION settle_ride_card_payment(text, uuid, text, bigint, text, numeric, jsonb, text)
    TO service_role;

COMMENT ON FUNCTION settle_ride_card_payment(text, uuid, text, bigint, text, numeric, jsonb, text) IS
    'Atomic card-settlement finalizer: flips rides.payment_status to paid '
    'and inserts the financial_events header in the same transaction. '
    'driver_earnings is computed fresh every call from total_fare - '
    '(booking_fee + airport_fee) + tip_amount -- idempotent by construction, '
    'no delta/existing-value bookkeeping (migration 334 fix, closing a real '
    'production underpayment caused by migration 288''s delta-based version). '
    'Returns the event id, or NULL when the ride was already paid. Called '
    'behind the ledger_atomic_settle_enabled app-settings flag with legacy '
    'two-write fallback. Service-role only.';
