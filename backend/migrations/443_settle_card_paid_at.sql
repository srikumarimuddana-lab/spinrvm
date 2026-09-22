-- 443_settle_card_paid_at.sql
-- Rollback plan: restore settle_ride_card_payment from migration 337 (or a
-- future function definition) without the paid_at assignment. No data is
-- removed; rows written after this migration retain their timestamp.
--
-- The atomic card settlement function is the source of truth for both the
-- paid flip and financial_events header. Keep paid_at in that same transaction
-- so a successful settlement cannot leave a paid ride with no confirmation
-- timestamp.

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

    IF v_status = 'paid' THEN
        RETURN NULL;
    END IF;

    -- Canonical, idempotent driver_earnings formula from migration 337.
    -- Preserve the reviewed base fare calculation while adding paid_at.
    v_base_earnings := GREATEST(v_total_fare - (v_booking_fee + v_airport_fee), 0);
    v_earnings := v_base_earnings + COALESCE(p_tip_amount, 0);

    UPDATE rides
       SET payment_status    = 'paid',
           payment_intent_id = p_payment_intent_id,
           tip_amount        = COALESCE(p_tip_amount, 0),
           driver_earnings   = v_earnings,
           auth_status       = COALESCE(p_auth_status, auth_status),
           paid_at           = COALESCE(paid_at, now()),
           updated_at        = now()
     WHERE id = p_ride_id;

    INSERT INTO financial_events (id, event_type, user_id, ride_id, delta_cents, ref, metadata, created_at)
    VALUES (p_event_id, 'stripe_charge', p_user_id, p_ride_id, p_amount_cents, p_payment_intent_id,
            COALESCE(p_metadata, '{}'::jsonb), now())
    ON CONFLICT (id) DO NOTHING;

    RETURN p_event_id::text;
END;
$$;

REVOKE EXECUTE ON FUNCTION settle_ride_card_payment(text, uuid, text, bigint, text, numeric, jsonb, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION settle_ride_card_payment(text, uuid, text, bigint, text, numeric, jsonb, text)
    TO service_role;

COMMENT ON FUNCTION settle_ride_card_payment(text, uuid, text, bigint, text, numeric, jsonb, text) IS
    'Atomically flips rides.payment_status to paid, preserves the first paid_at timestamp, '
    'applies the tip delta, and inserts the financial_events header. Service-role only.';
