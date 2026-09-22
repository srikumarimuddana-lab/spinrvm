-- Atomic, cumulative Stripe refund projection.
-- service-role-only: callers must obtain the succeeded cumulative amount from Stripe.
-- Rollback: REVOKE the grant and DROP FUNCTION; no data is removed.

CREATE OR REPLACE FUNCTION public.apply_stripe_refund_cumulative(
    p_ride_id text,
    p_payment_intent_id text,
    p_cumulative_refunded_cents bigint,
    p_captured_cents bigint,
    p_event_id uuid
)
RETURNS TABLE(outcome text, delta_cents bigint, refund_amount_cents bigint)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_ride record;
    v_booked_cents bigint;
    v_ride_cents bigint;
    v_floor_cents bigint;
    v_delta_cents bigint;
    v_total numeric;
    v_tax_total numeric;
    v_refund_amount numeric;
    v_tax_reversed numeric;
    v_fraction numeric;
    v_metadata jsonb;
    v_inserted integer;
    v_existing record;
BEGIN
    IF p_ride_id IS NULL OR p_payment_intent_id IS NULL OR p_event_id IS NULL THEN
        RAISE EXCEPTION 'apply_stripe_refund_cumulative: required identifier is null';
    END IF;
    IF p_cumulative_refunded_cents IS NULL OR p_cumulative_refunded_cents < 0
       OR p_captured_cents IS NULL OR p_captured_cents < 0 THEN
        RAISE EXCEPTION 'apply_stripe_refund_cumulative: invalid cents';
    END IF;
    IF p_cumulative_refunded_cents > p_captured_cents THEN
        RAISE EXCEPTION 'apply_stripe_refund_cumulative: refund exceeds captured amount';
    END IF;

    -- Serialize all writers for this Stripe payment, including callers that
    -- accidentally disagree about ride_id, then lock the ride projection.
    PERFORM pg_advisory_xact_lock(hashtextextended(p_payment_intent_id, 0));
    SELECT id, rider_id, payment_intent_id, refund_amount, payment_status,
           cancel_fee_payment_intent_id, cancellation_fee_admin, cancellation_fee_driver,
           grand_total, total_fare, tax_amount, tax_breakdown,
           driver_id, driver_earnings
      INTO v_ride
      FROM rides
     WHERE id = p_ride_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'apply_stripe_refund_cumulative: ride not found';
    END IF;
    IF v_ride.payment_intent_id IS NOT NULL
       AND v_ride.payment_intent_id <> p_payment_intent_id THEN
        RAISE EXCEPTION 'apply_stripe_refund_cumulative: PaymentIntent does not belong to ride';
    END IF;

    SELECT COALESCE(SUM(-fe.delta_cents), 0)::bigint
      INTO v_booked_cents
      FROM financial_events AS fe
     WHERE fe.event_type = 'stripe_refund'
       AND fe.ref = p_payment_intent_id
       AND fe.delta_cents < 0;

    -- Legacy ride summaries may predate the ledger. Never silently decrease
    -- either projection; return stale so callers can leave the operation for
    -- manual reconciliation rather than rewriting history.
    v_ride_cents := ROUND(COALESCE(v_ride.refund_amount, 0) * 100)::bigint;
    v_floor_cents := GREATEST(v_booked_cents, v_ride_cents);
    IF p_cumulative_refunded_cents < v_floor_cents THEN
        RETURN QUERY SELECT 'stale'::text, 0::bigint, v_floor_cents;
        RETURN;
    END IF;

    v_delta_cents := p_cumulative_refunded_cents - v_booked_cents;
    IF v_delta_cents > 0 THEN
        -- Mirror payment_service.record_refund_event's HALF_UP tax reversal.
        -- ROUND(numeric, 2) rounds half away from zero, matching Decimal's
        -- ROUND_HALF_UP for these nonnegative money values.
        v_total := ROUND(COALESCE(NULLIF(v_ride.grand_total, 0), NULLIF(v_ride.total_fare, 0), 0), 2);
        v_tax_total := ROUND(COALESCE(v_ride.tax_amount, 0), 2);
        v_refund_amount := ROUND(v_delta_cents::numeric / 100, 2);
        v_fraction := CASE
            WHEN v_total > 0 THEN LEAST(v_refund_amount / v_total, 1)
            ELSE 1
        END;
        v_tax_reversed := ROUND(v_tax_total * v_fraction, 2);
        v_metadata := jsonb_build_object(
            'source', 'charge.refunded',
            'driver_pay_absorbed_by_platform', true,
            'refund_amount', to_char(v_refund_amount, 'FM999999999999990.00'),
            'tax_reversed', to_char(v_tax_reversed, 'FM999999999999990.00'),
            'tax_breakdown', COALESCE(v_ride.tax_breakdown, '{}'::jsonb),
            'driver_id', COALESCE(v_ride.driver_id::text, ''),
            'driver_earnings_retained', to_char(ROUND(COALESCE(v_ride.driver_earnings, 0), 2), 'FM999999999999990.00')
        );

        INSERT INTO financial_events (id, event_type, user_id, ride_id, delta_cents, ref, metadata, created_at)
        VALUES (p_event_id, 'stripe_refund', v_ride.rider_id, v_ride.id, -v_delta_cents,
                p_payment_intent_id, v_metadata, now())
        ON CONFLICT (id) DO NOTHING;
        GET DIAGNOSTICS v_inserted = ROW_COUNT;
        IF v_inserted = 0 THEN
            SELECT fe.event_type, fe.user_id, fe.ride_id, fe.delta_cents, fe.ref
              INTO v_existing
              FROM financial_events AS fe
             WHERE fe.id = p_event_id;
            IF NOT FOUND OR v_existing.event_type <> 'stripe_refund'
               OR v_existing.user_id <> v_ride.rider_id
               OR v_existing.ride_id <> v_ride.id
               OR v_existing.ref <> p_payment_intent_id
               OR v_existing.delta_cents <> -v_delta_cents THEN
                RAISE EXCEPTION 'apply_stripe_refund_cumulative: deterministic event id conflicts';
            END IF;
        END IF;
    END IF;

    UPDATE rides
       SET refund_amount = p_cumulative_refunded_cents::numeric / 100,
           payment_status = CASE
               WHEN p_cumulative_refunded_cents = 0 THEN v_ride.payment_status
               -- Historical cancellations may have collected their fee on a
               -- separate PI. Refunding the entire booking capture in that
               -- case still leaves the separately paid fee with the rider.
               WHEN p_cumulative_refunded_cents >= p_captured_cents
                    AND v_ride.payment_status IN ('paid', 'partially_refunded')
                    AND (COALESCE(v_ride.cancellation_fee_admin, 0) > 0
                         OR COALESCE(v_ride.cancellation_fee_driver, 0) > 0)
                    AND v_ride.cancel_fee_payment_intent_id IS NOT NULL
                    AND v_ride.cancel_fee_payment_intent_id <> p_payment_intent_id
                   THEN 'partially_refunded'
               WHEN p_cumulative_refunded_cents >= p_captured_cents THEN 'refunded'
               ELSE 'partially_refunded'
           END,
           updated_at = now()
     WHERE id = p_ride_id;

    RETURN QUERY SELECT 'applied'::text, v_delta_cents, p_cumulative_refunded_cents;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.apply_stripe_refund_cumulative(text, text, bigint, bigint, uuid)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.apply_stripe_refund_cumulative(text, text, bigint, bigint, uuid)
    TO service_role;
