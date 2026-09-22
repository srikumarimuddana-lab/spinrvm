-- 442: Serialize pending-offer creation, period transitions, and cancelled-offer release.
-- Rollback: restore record_insurance_period_transition from migration 421;
-- drop enforce_pending_offer_ride_searching() and
-- release_batch_offer_driver_and_close_period(text, text).
-- No historical period rows are changed.
--
-- The dispatch path writes Period 2 after inserting a pending offer. The accept
-- route's redundant Period-2 safety-net can run after rider cancellation won its
-- ride-state CAS. Locking the ride row serializes this RPC with cancellation: if
-- cancellation won, no stale acceptance write can reopen Period 2; if this RPC
-- won, cancellation proceeds afterward and closes the period normally.

CREATE OR REPLACE FUNCTION record_insurance_period_transition(
    p_driver_id text,
    p_new_period smallint,
    p_ride_id text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    v_now timestamptz := now();
    v_closed int;
    v_current_period smallint;
    v_current_ride_id text;
    v_ride_status text;
    v_ride_driver_id text;
BEGIN
    IF p_new_period = 3 AND p_ride_id IS NULL THEN
        RAISE EXCEPTION 'ride_id is required when new_period = 3 (passenger aboard)';
    END IF;

    IF p_new_period NOT IN (0, 1, 2, 3) THEN
        RAISE EXCEPTION 'new_period must be 0, 1, 2, or 3; got %', p_new_period;
    END IF;

    -- A Period-2 assignment must still be live. Batch offers are live while
    -- the ride is searching and this driver's offer is pending; direct
    -- assignment/acceptance carries the driver on the ride row. FOR UPDATE
    -- prevents a cancellation CAS from interleaving with this decision/write.
    IF p_new_period = 2 AND p_ride_id IS NOT NULL THEN
        SELECT status, driver_id INTO v_ride_status, v_ride_driver_id
        FROM rides
        WHERE id = p_ride_id
        FOR UPDATE;

        IF NOT FOUND OR NOT (
            (
                v_ride_status = 'searching'
                AND EXISTS (
                    SELECT 1 FROM ride_offers
                    WHERE ride_id = p_ride_id
                      AND driver_id = p_driver_id
                      AND status = 'pending'
                )
            )
            OR (
                v_ride_status IN ('driver_assigned', 'driver_accepted', 'driver_arrived')
                AND v_ride_driver_id = p_driver_id
            )
        ) THEN
            RETURN jsonb_build_object('status', 'stale_ride', 'closed', 0, 'opened', false);
        END IF;
    END IF;

    SELECT period, ride_id INTO v_current_period, v_current_ride_id
    FROM driver_insurance_periods
    WHERE driver_id = p_driver_id AND ended_at IS NULL;

    IF v_current_period IS NOT NULL AND v_current_period = p_new_period
       AND v_current_ride_id IS NOT DISTINCT FROM p_ride_id THEN
        RETURN jsonb_build_object('status', 'noop', 'closed', 0, 'opened', false);
    END IF;

    UPDATE driver_insurance_periods
    SET ended_at = v_now
    WHERE driver_id = p_driver_id AND ended_at IS NULL;

    GET DIAGNOSTICS v_closed = ROW_COUNT;

    BEGIN
        INSERT INTO driver_insurance_periods (driver_id, period, started_at, ride_id)
        VALUES (p_driver_id, p_new_period, v_now, p_ride_id);
    EXCEPTION WHEN unique_violation THEN
        RETURN jsonb_build_object('status', 'race', 'closed', v_closed, 'opened', false);
    END;

    RETURN jsonb_build_object('status', 'ok', 'closed', v_closed, 'opened', true);
END;
$$;

REVOKE ALL ON FUNCTION record_insurance_period_transition(text, smallint, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION record_insurance_period_transition(text, smallint, text)
    TO service_role;

-- Hold the ride row through the transaction that inserts a pending offer.
-- Cancellation updates the same row, so it either wins first (this trigger
-- rejects the offer) or waits until claim + offer + Period 2 are committed.
CREATE OR REPLACE FUNCTION enforce_pending_offer_ride_searching()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    v_status text;
BEGIN
    SELECT status INTO v_status
      FROM rides
     WHERE id = NEW.ride_id
     FOR UPDATE;

    IF NOT FOUND OR v_status <> 'searching' THEN
        RAISE EXCEPTION 'pending ride offer requires a searching ride (ride_id=%)', NEW.ride_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION enforce_pending_offer_ride_searching() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION enforce_pending_offer_ride_searching() TO service_role;

CREATE TRIGGER ride_offers_pending_ride_searching
    BEFORE INSERT OR UPDATE OF status ON ride_offers
    FOR EACH ROW
    WHEN (NEW.status = 'pending')
    EXECUTE FUNCTION enforce_pending_offer_ride_searching();

-- Atomically release a cancelled batch offer only if it still owns the driver.
-- Rollback: DROP FUNCTION release_batch_offer_driver_and_close_period(text, text).
-- No historical insurance rows are modified; only the current open period may
-- be closed. Code must not fall back to set_driver_available on RPC failure.
--
-- Lock order is driver row -> current open period row. Dispatch claims also
-- lock/update the driver before opening Period 2, so the row lock serializes a
-- release against a new claim. The claim timestamp check catches the PostgREST
-- path's split claim/period writes when claim committed but its Period-2 RPC is
-- still waiting to run. The ride_id match and other-obligation checks prevent
-- stale cleanup from freeing or closing a driver's newer assignment.

CREATE OR REPLACE FUNCTION release_batch_offer_driver_and_close_period(
    p_driver_id text,
    p_ride_id text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    v_is_online boolean;
    v_user_id text;
    v_claimed_at timestamptz;
    v_period smallint;
    v_period_ride_id text;
    v_period_started_at timestamptz;
    v_cancelled_offer_offered_at timestamptz;
    v_ownership_started_at timestamptz;
    v_new_period smallint;
    v_transition jsonb;
BEGIN
    SELECT is_online, availability_claimed_at, user_id
      INTO v_is_online, v_claimed_at, v_user_id
      FROM drivers
     WHERE id = p_driver_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'driver_missing');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM rides
         WHERE id = p_ride_id AND status = 'cancelled'
    ) THEN
        RETURN jsonb_build_object('status', 'target_ride_not_cancelled');
    END IF;

    IF EXISTS (
        SELECT 1 FROM ride_offers
         WHERE driver_id = p_driver_id
           AND ride_id = p_ride_id
           AND status IN ('pending', 'accepted')
    ) THEN
        RETURN jsonb_build_object('status', 'target_offer_active');
    END IF;

    SELECT offered_at INTO v_cancelled_offer_offered_at
      FROM ride_offers
     WHERE driver_id = p_driver_id
       AND ride_id = p_ride_id
       AND status = 'cancelled'
     ORDER BY responded_at DESC
     LIMIT 1;

    IF NOT FOUND OR v_cancelled_offer_offered_at IS NULL THEN
        RETURN jsonb_build_object('status', 'cancelled_offer_missing');
    END IF;

    SELECT period, ride_id, started_at
      INTO v_period, v_period_ride_id, v_period_started_at
      FROM driver_insurance_periods
     WHERE driver_id = p_driver_id AND ended_at IS NULL
     FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'ownership_mismatch');
    END IF;

    IF v_period = 2 AND v_period_ride_id = p_ride_id THEN
        v_ownership_started_at := v_period_started_at;
    ELSIF v_period = 1 AND v_period_ride_id IS NULL THEN
        -- Cancellation may beat the legacy PostgREST Period-2 write after it
        -- cancels an inserted offer. Period 1 is still the open interval and
        -- the offer timestamp ties this claim to the same canceled offer.
        v_ownership_started_at := v_cancelled_offer_offered_at;
    ELSE
        RETURN jsonb_build_object('status', 'ownership_mismatch');
    END IF;

    -- On the legacy PostgREST path, the atomic driver claim and insurance
    -- transition are separate requests. A later claim stamp than this open
    -- Period-2 start means a newer claim may still be between those requests.
    -- In the direct-pool transaction they share now(), so equality is valid.
    IF v_claimed_at IS NULL THEN
        RETURN jsonb_build_object('status', 'claim_stamp_missing');
    END IF;

    IF v_claimed_at > v_ownership_started_at THEN
        RETURN jsonb_build_object('status', 'newer_claim_in_flight');
    END IF;

    IF EXISTS (
        SELECT 1
          FROM ride_offers AS offer
          JOIN rides AS active_ride ON active_ride.id = offer.ride_id
         WHERE offer.driver_id = p_driver_id
           AND offer.ride_id <> p_ride_id
           AND (
               (offer.status = 'pending' AND active_ride.status IN ('searching', 'driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress'))
               OR (offer.status = 'accepted' AND active_ride.status IN ('driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress'))
           )
    ) THEN
        RETURN jsonb_build_object('status', 'other_offer_active');
    END IF;

    IF EXISTS (
        SELECT 1 FROM rides
         WHERE driver_id = p_driver_id
           AND id <> p_ride_id
           AND status IN ('driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress')
    ) THEN
        RETURN jsonb_build_object('status', 'other_ride_active');
    END IF;

    v_new_period := CASE WHEN COALESCE(v_is_online, false) THEN 1 ELSE 0 END;

    -- Keep the dispatch-availability invariant and close/open the regulatory
    -- interval in this transaction, before a waiting claim can acquire the
    -- driver row. Do not set is_online: it remains driver-controlled.
    UPDATE drivers
       SET is_available = COALESCE(v_is_online, false),
           availability_claimed_at = NULL
     WHERE id = p_driver_id;

    v_transition := record_insurance_period_transition(p_driver_id, v_new_period, NULL);
    IF COALESCE(v_transition->>'status', '') NOT IN ('ok', 'noop') THEN
        RAISE EXCEPTION 'batch offer release period transition failed driver_id=% ride_id=% result=%',
            p_driver_id, p_ride_id, v_transition
            USING ERRCODE = 'P0001';
    END IF;

    RETURN jsonb_build_object('status', 'released', 'period', v_new_period, 'user_id', v_user_id);
END;
$$;

REVOKE ALL ON FUNCTION release_batch_offer_driver_and_close_period(text, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION release_batch_offer_driver_and_close_period(text, text)
    TO service_role;
