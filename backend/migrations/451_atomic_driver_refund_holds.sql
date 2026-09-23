-- 451: Atomic, cumulative driver refund holds in the existing refund transaction.
-- Rollout: leave settings.driver_refund_holds_enabled=false until every backend
-- runs without the Python per-event hold writer. Then enable after reconciliation.
-- Rollback: disable that flag. Keep attributed adjustment rows and this RPC;
-- erroneous historical deductions require audited compensating entries, not deletion.
-- migration-override-ok: replaces 446 RPC without changing its signature.
-- Never restore the old Python hold writer after enabling this SQL path.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '20s';
ALTER TABLE public.settings ADD COLUMN IF NOT EXISTS driver_refund_holds_enabled boolean DEFAULT false;
-- Immutable audit attribution, deliberately not a foreign key: seven-year ride
-- retention must not be blocked by adjustment history.
ALTER TABLE public.payouts ADD COLUMN IF NOT EXISTS refund_ride_id text;
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
SET search_path = public, pg_catalog
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
    v_held_cents bigint := 0;
    v_hold_cents bigint := 0;
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
           driver_id, driver_earnings, ride_completed_at
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
        -- Refund.updated, charge.refunded, and the retry worker share this lock
        -- and transaction. A failed hold also rolls back the refund ledger.
        IF COALESCE((SELECT driver_refund_holds_enabled FROM settings WHERE id='app_settings'), false)
           AND v_ride.driver_id IS NOT NULL AND v_ride.ride_completed_at IS NOT NULL
           AND EXISTS (SELECT 1 FROM payouts WHERE driver_id=v_ride.driver_id
                       AND payout_type IN ('auto', 'instant', 'standard')
                       AND status='completed' AND amount > 0
                       AND created_at >= v_ride.ride_completed_at
                       AND created_at <= now()) THEN
            -- Bounded to this driver, under the already-held ride lock. Attribute
            -- known legacy rows before summing; never guess about unknown rows.
            UPDATE payouts p SET refund_ride_id=r.id FROM rides r
             WHERE p.payout_type='clawback' AND p.refund_ride_id IS NULL
               AND p.driver_id=v_ride.driver_id AND r.driver_id=p.driver_id
               AND r.id=substring(p.failure_reason FROM '^Rider refund hold for ride (.+) event [^ ]+$');
            -- Detect old writers after migration instead of double-deducting.
            IF EXISTS (SELECT 1 FROM payouts WHERE driver_id=v_ride.driver_id
                       AND payout_type='clawback' AND refund_ride_id IS NULL) THEN
                RAISE EXCEPTION 'Unattributed refund hold requires manual reconciliation';
            END IF;
            SELECT ROUND(COALESCE(SUM(amount::numeric),0)*100)::bigint INTO v_held_cents
              FROM payouts WHERE refund_ride_id=p_ride_id AND driver_id=v_ride.driver_id
                AND payout_type='clawback' AND status='completed';
            IF v_held_cents > ROUND(GREATEST(COALESCE(v_ride.driver_earnings,0),0)::numeric*100)::bigint THEN
                RAISE EXCEPTION 'Existing refund holds exceed driver earnings; manual adjustment required';
            END IF;
            v_hold_cents := LEAST(v_delta_cents,
                GREATEST(0, ROUND(GREATEST(COALESCE(v_ride.driver_earnings,0),0)::numeric*100)::bigint-v_held_cents));
            IF v_hold_cents > 0 THEN
                INSERT INTO payouts(id,driver_id,amount,status,payout_type,refund_ride_id,bank_name,failure_reason,created_at)
                VALUES ('refund-hold-' || p_event_id::text, v_ride.driver_id, v_hold_cents::numeric/100,
                        'completed','clawback',p_ride_id,'Refund hold','Atomic rider refund adjustment',now());
            END IF;
        END IF;
        -- Previously absorbed refunds (including pre-rollout history) are never
        -- retroactively charged to a driver on a zero-delta replay.
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
            'driver_pay_absorbed_by_platform', v_hold_cents = 0,
            'driver_refund_hold_cents', v_hold_cents,
            'platform_absorbed_cents', v_delta_cents-v_hold_cents,
            'refund_amount', to_char(v_refund_amount, 'FM999999999999990.00'),
            'tax_reversed', to_char(v_tax_reversed, 'FM999999999999990.00'),
            'tax_breakdown', COALESCE(v_ride.tax_breakdown, '{}'::jsonb),
            'driver_id', COALESCE(v_ride.driver_id::text, ''),
            'driver_earnings_retained', to_char(ROUND(GREATEST(COALESCE(v_ride.driver_earnings,0)::numeric-(v_held_cents+v_hold_cents)::numeric/100,0),2), 'FM999999999999990.00')
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
COMMIT;
-- Runner selects per-statement autocommit because this index is CONCURRENTLY.
-- On interrupted build, DROP INDEX CONCURRENTLY public.payouts_refund_ride_driver_idx
-- if pg_index.indisvalid=false, then rerun; never enable the flag without a valid index.
CREATE INDEX CONCURRENTLY IF NOT EXISTS payouts_refund_ride_driver_idx
    ON public.payouts(refund_ride_id, driver_id) WHERE payout_type = 'clawback';
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_index WHERE indexrelid='public.payouts_refund_ride_driver_idx'::regclass AND NOT indisvalid) THEN
        RAISE EXCEPTION 'Invalid refund hold index: drop concurrently and rerun migration';
    END IF;
END $$;
