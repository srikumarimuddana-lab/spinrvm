-- 460: T5 atomic offer decisions (backend design section 4.2).
-- Rollback: keep driver_availability_v2_enabled false, then drop
-- resolve_driver_offer, finalize_deferred_driver_availability,
-- _release_offer_claim_locked, _finalize_deferred_availability_locked,
-- offer_expiry_counts_as_miss, driver_offer_decisions, the ride_offers
-- outcome/outcome_at columns and ride_offers_outcome_check, and the three
-- drivers offer_miss_streak* columns. Legacy offers never touch any of these.
-- migration-override-ok: T5 adds new RPCs that call 457's T1 transition and
-- 442's insurance transition; no earlier function body is replaced.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '20s';

-- ── Part A: schema ─────────────────────────────────────────────────

ALTER TABLE public.ride_offers
    ADD COLUMN IF NOT EXISTS outcome text,
    ADD COLUMN IF NOT EXISTS outcome_at timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'ride_offers_outcome_check'
                      AND conrelid = 'public.ride_offers'::regclass) THEN
        ALTER TABLE public.ride_offers ADD CONSTRAINT ride_offers_outcome_check
            CHECK (outcome IS NULL OR outcome IN (
                'accepted','declined','preempted','cancelled','expired_nonresponse',
                'expired_delivery_unknown','expired_availability_changed','expired_late_response'))
            NOT VALID;
    END IF;
END $$;

-- The durable miss streak replaces the Redis streak under v2. A streak from
-- another epoch or older than 30 minutes counts as zero.
ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS offer_miss_streak integer
    NOT NULL DEFAULT 0;
ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS offer_miss_streak_epoch bigint,
    ADD COLUMN IF NOT EXISTS offer_miss_streak_at timestamptz;

-- service-role-only: decisions are written by resolve_driver_offer only.
CREATE TABLE IF NOT EXISTS public.driver_offer_decisions (
    offer_id uuid NOT NULL REFERENCES public.ride_offers(id) ON DELETE CASCADE,
    request_id text NOT NULL,
    action text NOT NULL,
    claim_id uuid,
    expected_epoch bigint,
    actor_session_id text,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (offer_id, request_id)
);
ALTER TABLE public.driver_offer_decisions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.driver_offer_decisions FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON public.driver_offer_decisions TO service_role;

-- ── Seam: does an expired pending offer count as a miss? ───────────
-- 460 keeps legacy counting: the driver is still on the epoch and session
-- the offer was addressed to. The receipts migration replaces this body.
CREATE OR REPLACE FUNCTION public.offer_expiry_counts_as_miss(
    p_offer public.ride_offers,
    p_driver public.drivers
) RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
    SELECT p_driver.online_epoch IS NOT DISTINCT FROM p_offer.online_epoch
       AND p_driver.controller_session_id IS NOT DISTINCT FROM p_offer.controller_session_id
$$;
REVOKE ALL ON FUNCTION public.offer_expiry_counts_as_miss(public.ride_offers, public.drivers)
    FROM PUBLIC, anon, authenticated, service_role;

-- ── Private: finish a deferred stop/pause once obligations are gone ─
-- Caller holds the driver lock. T1 records Period 0 and bumps the epoch.
CREATE OR REPLACE FUNCTION public._finalize_deferred_availability_locked(
    p_driver public.drivers,
    p_request_id text
) RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_t1 jsonb;
BEGIN
    IF NOT (p_driver.is_online AND NOT p_driver.accepting_requests
            AND p_driver.availability_reason IN
                ('stop_requests','pause_policy','pause_unreachable','pause_idle','pause_misses')) THEN
        RETURN jsonb_build_object('finalized', false, 'availability', NULL);
    END IF;
    IF EXISTS (SELECT 1 FROM public.rides r
                WHERE r.driver_id = p_driver.id
                  AND r.status IN ('driver_assigned','driver_accepted','driver_arrived','in_progress'))
       OR EXISTS (SELECT 1 FROM public.ride_offers ro JOIN public.rides r ON r.id = ro.ride_id
                   WHERE ro.driver_id = p_driver.id AND ro.status IN ('pending','accepted')
                     AND r.status IN ('searching','driver_assigned','driver_accepted','driver_arrived','in_progress')) THEN
        RETURN jsonb_build_object('finalized', false, 'availability', NULL, 'reason', 'obligation');
    END IF;
    v_t1 := public.transition_driver_availability(
        p_driver.id, p_driver.online_epoch, 'system:finalize', p_driver.availability_reason, p_request_id);
    RETURN jsonb_build_object('finalized', COALESCE(v_t1->>'code', '') = 'OK', 'availability', v_t1);
END;
$$;
REVOKE ALL ON FUNCTION public._finalize_deferred_availability_locked(public.drivers, text)
    FROM PUBLIC, anon, authenticated, service_role;

-- ── Private: release the claim an offer holds ──────────────────────
-- Caller holds driver -> ride -> offer. Produces exactly one insurance
-- transition when it releases: the miss pause, the deferred finalize, or a
-- plain Period 1/0 transition.
CREATE OR REPLACE FUNCTION public._release_offer_claim_locked(
    p_driver_id text,
    p_offer public.ride_offers,
    p_now timestamptz,
    p_streak_mode text,
    p_refresh_ready boolean,
    p_miss_threshold integer
) RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_driver public.drivers%ROWTYPE;
    v_streak integer;
    v_pause boolean;
    v_t1 jsonb;
    v_fin jsonb;
    v_period smallint;
    v_transition jsonb;
    v_availability jsonb;
BEGIN
    IF p_streak_mode NOT IN ('reset','increment','keep') THEN
        RAISE EXCEPTION 'invalid streak mode: %', p_streak_mode USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_driver FROM public.drivers WHERE id = p_driver_id;
    IF v_driver.availability_claim_id IS DISTINCT FROM p_offer.claim_id OR p_offer.claim_id IS NULL THEN
        RETURN jsonb_build_object('released', false, 'reason', 'claim_changed',
                                  'miss_counted', false, 'miss_streak', v_driver.offer_miss_streak, 'paused', false);
    END IF;
    IF EXISTS (SELECT 1 FROM public.rides r
                WHERE r.driver_id = p_driver_id
                  AND r.status IN ('driver_assigned','driver_accepted','driver_arrived','in_progress'))
       OR EXISTS (SELECT 1 FROM public.ride_offers ro JOIN public.rides r ON r.id = ro.ride_id
                   WHERE ro.driver_id = p_driver_id AND ro.id <> p_offer.id
                     AND ro.status IN ('pending','accepted')
                     AND r.status IN ('searching','driver_assigned','driver_accepted','driver_arrived','in_progress')) THEN
        RETURN jsonb_build_object('released', false, 'reason', 'other_obligation',
                                  'miss_counted', false, 'miss_streak', v_driver.offer_miss_streak, 'paused', false);
    END IF;

    v_streak := CASE WHEN v_driver.offer_miss_streak_epoch IS DISTINCT FROM v_driver.online_epoch
                       OR v_driver.offer_miss_streak_at IS NULL
                       OR v_driver.offer_miss_streak_at < p_now - interval '30 minutes'
                     THEN 0 ELSE v_driver.offer_miss_streak END;
    IF p_streak_mode = 'increment' THEN
        v_streak := v_streak + 1;
    ELSIF p_streak_mode = 'reset' THEN
        v_streak := 0;
    END IF;
    v_pause := p_streak_mode = 'increment' AND v_streak >= p_miss_threshold
               AND v_driver.is_online AND v_driver.accepting_requests;

    UPDATE public.drivers
       SET is_available = is_online AND accepting_requests,
           availability_claim_id = NULL,
           availability_claimed_at = NULL,
           offer_miss_streak = CASE WHEN p_streak_mode = 'keep' THEN offer_miss_streak
                                    WHEN v_pause THEN 0 ELSE v_streak END,
           offer_miss_streak_epoch = CASE WHEN p_streak_mode = 'keep' THEN offer_miss_streak_epoch
                                          ELSE online_epoch END,
           offer_miss_streak_at = CASE WHEN p_streak_mode = 'keep' THEN offer_miss_streak_at ELSE p_now END,
           ready_until = CASE WHEN p_refresh_ready AND accepting_requests AND is_online
                              THEN p_now + public.driver_ready_window() ELSE ready_until END,
           state_version = state_version + 1,
           updated_at = now()
     WHERE id = p_driver_id
     RETURNING * INTO v_driver;

    IF v_pause THEN
        v_t1 := public.transition_driver_availability(
            p_driver_id, v_driver.online_epoch, 'system:missed_offers', 'pause_misses',
            'miss-pause:' || p_offer.id::text);
        IF COALESCE(v_t1->>'code', '') = 'OK' THEN
            RETURN jsonb_build_object('released', true, 'period', CASE WHEN (v_t1->>'is_online')::boolean THEN 1 ELSE 0 END,
                                      'miss_counted', true, 'miss_streak', v_streak, 'paused', true,
                                      'availability', v_t1);
        END IF;
        v_pause := false;  -- fall through to exactly one plain transition
    END IF;

    v_fin := public._finalize_deferred_availability_locked(v_driver, 'finalize:' || p_offer.id::text);
    IF COALESCE((v_fin->>'finalized')::boolean, false) THEN
        RETURN jsonb_build_object('released', true, 'period', 0,
                                  'miss_counted', p_streak_mode = 'increment', 'miss_streak', v_streak,
                                  'paused', false, 'availability', v_fin->'availability');
    END IF;

    PERFORM 1 FROM public.driver_insurance_periods dip
     WHERE dip.driver_id = p_driver_id AND dip.ended_at IS NULL FOR UPDATE;
    v_period := CASE WHEN v_driver.is_online THEN 1 ELSE 0 END;
    v_transition := public.record_insurance_period_transition(p_driver_id, v_period, NULL);
    IF COALESCE(v_transition->>'status', '') NOT IN ('ok','noop') THEN
        RAISE EXCEPTION 'offer release period transition failed driver_id=% offer_id=% result=%',
            p_driver_id, p_offer.id, v_transition USING ERRCODE = 'P0001';
    END IF;
    v_availability := jsonb_build_object(
        'online_epoch', v_driver.online_epoch::text, 'state_version', v_driver.state_version::text,
        'is_online', v_driver.is_online, 'accepting_requests', v_driver.accepting_requests,
        'is_available', v_driver.is_available, 'availability_reason', v_driver.availability_reason);
    RETURN jsonb_build_object('released', true, 'period', v_period,
                              'miss_counted', p_streak_mode = 'increment', 'miss_streak', v_streak,
                              'paused', false, 'availability', v_availability);
END;
$$;
REVOKE ALL ON FUNCTION public._release_offer_claim_locked(text, public.ride_offers, timestamptz, text, boolean, integer)
    FROM PUBLIC, anon, authenticated, service_role;

-- ── Public: finalize a deferred stop/pause ─────────────────────────
CREATE OR REPLACE FUNCTION public.finalize_deferred_driver_availability(
    p_driver_id text,
    p_request_id text
) RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
SET lock_timeout = '2s'
AS $$
DECLARE
    v_driver public.drivers%ROWTYPE;
BEGIN
    IF p_driver_id IS NULL OR p_request_id IS NULL
       OR length(p_request_id) < 1 OR length(p_request_id) > 128 THEN
        RAISE EXCEPTION 'driver_id and request_id (1..128 chars) are required' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_driver FROM public.drivers WHERE id = p_driver_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('code', 'DRIVER_NOT_FOUND'); END IF;
    IF NOT COALESCE((SELECT driver_availability_v2_enabled FROM public.settings WHERE id = 'app_settings'), false) THEN
        RETURN jsonb_build_object('code', 'AVAILABILITY_V2_DISABLED');
    END IF;
    RETURN jsonb_build_object('code', 'OK') || public._finalize_deferred_availability_locked(v_driver, p_request_id);
END;
$$;
REVOKE ALL ON FUNCTION public.finalize_deferred_driver_availability(text, text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.finalize_deferred_driver_availability(text, text) TO service_role;

-- ── Part B: resolve_driver_offer ───────────────────────────────────
-- Lock order: driver -> ride -> (accept: every offer of the ride by id) ->
-- offer -> open insurance period. Loser drivers are never locked here; they
-- are released afterwards, one transaction each, via 'cancel_unaccepted'.
CREATE OR REPLACE FUNCTION public.resolve_driver_offer(
    p_offer_id uuid,
    p_claim_id uuid,
    p_expected_epoch bigint,
    p_actor_session_id text,
    p_action text,
    p_request_id text,
    p_miss_threshold integer DEFAULT 3
) RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
SET lock_timeout = '2s'
AS $$
DECLARE
    v_driver_id text;
    v_ride_id text;
    v_driver public.drivers%ROWTYPE;
    v_ride public.rides%ROWTYPE;
    v_offer public.ride_offers%ROWTYPE;
    v_saved public.driver_offer_decisions%ROWTYPE;
    v_now timestamptz;
    v_current_session text;
    v_code text := 'OK';
    v_extra jsonb := '{}'::jsonb;
    v_release jsonb := jsonb_build_object('released', false, 'miss_counted', false, 'paused', false);
    v_losers jsonb := '[]'::jsonb;
    v_already boolean := false;
    v_persist boolean := false;
    v_outcome text;
    v_status text;
    v_period jsonb;
    v_result jsonb;
BEGIN
    IF p_action IS NULL OR p_action NOT IN ('accept','decline','expire','cancel_unaccepted') THEN
        RAISE EXCEPTION 'unsupported offer action: %', p_action USING ERRCODE = '22023';
    END IF;
    IF p_offer_id IS NULL OR p_claim_id IS NULL THEN
        RAISE EXCEPTION 'offer_id and claim_id are required' USING ERRCODE = '22023';
    END IF;
    IF p_action IN ('accept','decline') AND (p_expected_epoch IS NULL OR p_expected_epoch < 0
            OR p_actor_session_id IS NULL OR length(btrim(p_actor_session_id)) = 0
            OR p_actor_session_id LIKE 'system:%') THEN
        RAISE EXCEPTION '% needs a non-negative epoch and a user session', p_action USING ERRCODE = '22023';
    END IF;
    IF p_action IN ('expire','cancel_unaccepted') AND (p_expected_epoch IS NOT NULL OR p_actor_session_id IS NOT NULL) THEN
        RAISE EXCEPTION '% takes no epoch or session', p_action USING ERRCODE = '22023';
    END IF;
    IF p_request_id IS NULL OR length(p_request_id) < 1 OR length(p_request_id) > 128 THEN
        RAISE EXCEPTION 'request_id must be 1..128 characters' USING ERRCODE = '22023';
    END IF;
    IF p_miss_threshold IS NULL OR p_miss_threshold < 1 OR p_miss_threshold > 20 THEN
        RAISE EXCEPTION 'miss_threshold must be 1..20' USING ERRCODE = '22023';
    END IF;

    -- 1. Unlocked lookup, 2. driver lock.
    SELECT driver_id, ride_id INTO v_driver_id, v_ride_id FROM public.ride_offers WHERE id = p_offer_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('code','OFFER_NOT_FOUND','offer_id',p_offer_id); END IF;
    SELECT * INTO v_driver FROM public.drivers WHERE id = v_driver_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('code','OFFER_NOT_FOUND','offer_id',p_offer_id); END IF;

    -- 3. Idempotency under the driver lock.
    SELECT * INTO v_saved FROM public.driver_offer_decisions
     WHERE offer_id = p_offer_id AND request_id = p_request_id;
    IF FOUND THEN
        IF v_saved.action <> p_action OR v_saved.claim_id IS DISTINCT FROM p_claim_id
           OR v_saved.expected_epoch IS DISTINCT FROM p_expected_epoch
           OR v_saved.actor_session_id IS DISTINCT FROM p_actor_session_id THEN
            RETURN jsonb_build_object('code','IDEMPOTENCY_KEY_CONFLICT','offer_id',p_offer_id);
        END IF;
        RETURN v_saved.result || jsonb_build_object('replayed', true);
    END IF;

    -- 4. Flag.
    IF NOT COALESCE((SELECT driver_availability_v2_enabled FROM public.settings WHERE id = 'app_settings'), false) THEN
        RETURN jsonb_build_object('code','AVAILABILITY_V2_DISABLED');
    END IF;

    -- 5. Ride, then (accept) every offer of the ride, 6. the offer.
    SELECT * INTO v_ride FROM public.rides WHERE id = v_ride_id FOR UPDATE;
    IF p_action = 'accept' THEN
        PERFORM 1 FROM public.ride_offers WHERE ride_id = v_ride_id ORDER BY id FOR UPDATE;
    END IF;
    SELECT * INTO v_offer FROM public.ride_offers WHERE id = p_offer_id FOR UPDATE;
    v_now := clock_timestamp();

    -- 7. Protocol and claim identity.
    IF v_offer.claim_id IS NULL OR v_offer.online_epoch IS NULL
       OR v_offer.controller_session_id IS NULL OR v_offer.expires_at IS NULL THEN
        RETURN jsonb_build_object('code','OFFER_PROTOCOL_MISMATCH','offer_id',p_offer_id);
    END IF;
    IF v_offer.claim_id <> p_claim_id THEN
        RETURN jsonb_build_object('code','CLAIM_MISMATCH','offer_id',p_offer_id);
    END IF;

    IF p_action IN ('accept','decline') THEN
        SELECT u.current_session_id INTO v_current_session FROM public.users u WHERE u.id = v_driver.user_id;
    END IF;

    IF p_action = 'accept' THEN
        IF v_offer.status = 'accepted' AND v_ride.driver_id = v_driver_id
           AND v_ride.status IN ('driver_accepted','driver_arrived','in_progress','completed') THEN
            v_already := true;
        ELSIF v_offer.status <> 'pending' THEN
            RETURN jsonb_build_object('offer_id', p_offer_id, 'outcome', v_offer.outcome,
                                      'expires_at', v_offer.expires_at, 'server_time', v_now,
                                      'ride_status', v_ride.status)
                || CASE v_offer.status
                     WHEN 'expired' THEN jsonb_build_object('code','OFFER_EXPIRED')
                     WHEN 'preempted' THEN jsonb_build_object('code','RIDE_STATE_CONFLICT','reason_code','RIDE_TAKEN')
                     WHEN 'cancelled' THEN jsonb_build_object('code','RIDE_STATE_CONFLICT','reason_code','RIDE_CANCELLED')
                     WHEN 'accepted' THEN jsonb_build_object('code','RIDE_STATE_CONFLICT','reason_code',
                         CASE WHEN v_ride.status = 'cancelled' THEN 'RIDE_CANCELLED' ELSE 'RIDE_NOT_SEARCHING' END)
                     ELSE jsonb_build_object('code','OFFER_ALREADY_RESOLVED') END;
        ELSIF v_current_session IS DISTINCT FROM p_actor_session_id THEN
            RETURN jsonb_build_object('code','SESSION_SUPERSEDED','offer_id',p_offer_id);
        ELSIF v_offer.controller_session_id <> p_actor_session_id
              OR v_driver.controller_session_id IS DISTINCT FROM p_actor_session_id THEN
            -- The caller is the newest login, but this offer was addressed to
            -- an earlier one: it simply ended for this caller. No state change.
            RETURN jsonb_build_object('code','OFFER_EXPIRED','reason_code','OFFER_SESSION_ENDED',
                                      'offer_id',p_offer_id,'expires_at',v_offer.expires_at,'server_time',v_now);
        ELSIF v_now >= v_offer.expires_at THEN
            UPDATE public.ride_offers SET status = 'expired', outcome = 'expired_late_response',
                   outcome_at = v_now, responded_at = v_now
             WHERE id = p_offer_id RETURNING * INTO v_offer;
            v_release := public._release_offer_claim_locked(v_driver_id, v_offer, v_now, 'keep', false, p_miss_threshold);
            v_code := 'OFFER_EXPIRED';
            v_persist := true;
        ELSIF v_driver.online_epoch <> p_expected_epoch OR v_offer.online_epoch <> p_expected_epoch THEN
            RETURN jsonb_build_object('code','ONLINE_EPOCH_STALE','offer_id',p_offer_id,
                                      'online_epoch',v_driver.online_epoch::text);
        ELSIF NOT v_driver.is_online THEN
            RETURN jsonb_build_object('code','DRIVER_OFFLINE','offer_id',p_offer_id,
                                      'online_epoch',v_driver.online_epoch::text);
        ELSIF v_ride.status <> 'searching' OR v_ride.driver_id IS NOT NULL THEN
            RETURN jsonb_build_object('code','RIDE_STATE_CONFLICT','offer_id',p_offer_id,'ride_status',v_ride.status,
                                      'reason_code', CASE WHEN v_ride.status = 'cancelled' THEN 'RIDE_CANCELLED'
                                                          WHEN v_ride.driver_id IS NOT NULL THEN 'RIDE_TAKEN'
                                                          ELSE 'RIDE_NOT_SEARCHING' END);
        ELSIF v_driver.availability_claim_id IS DISTINCT FROM v_offer.claim_id THEN
            RETURN jsonb_build_object('code','CLAIM_MISMATCH','offer_id',p_offer_id);
        ELSE
            UPDATE public.rides SET status = 'driver_accepted', driver_id = v_driver_id,
                   driver_accepted_at = v_now, updated_at = v_now
             WHERE id = v_ride_id RETURNING * INTO v_ride;
            UPDATE public.ride_offers SET status = 'accepted', outcome = 'accepted', outcome_at = v_now,
                   responded_at = v_now
             WHERE id = p_offer_id RETURNING * INTO v_offer;
            WITH l AS (
                UPDATE public.ride_offers SET status = 'preempted', outcome = 'preempted',
                       outcome_at = v_now, responded_at = v_now
                 WHERE ride_id = v_ride_id AND id <> p_offer_id AND status = 'pending'
                 RETURNING id, driver_id, claim_id)
            SELECT COALESCE(jsonb_agg(jsonb_build_object('offer_id', l.id, 'driver_id', l.driver_id,
                                                         'claim_id', l.claim_id) ORDER BY l.id), '[]'::jsonb)
              INTO v_losers FROM l;
            UPDATE public.drivers
               SET offer_miss_streak = 0, offer_miss_streak_epoch = online_epoch, offer_miss_streak_at = v_now,
                   ready_until = CASE WHEN accepting_requests THEN v_now + public.driver_ready_window()
                                      ELSE ready_until END,
                   state_version = state_version + 1, updated_at = now()
             WHERE id = v_driver_id RETURNING * INTO v_driver;
            PERFORM 1 FROM public.driver_insurance_periods dip
             WHERE dip.driver_id = v_driver_id AND dip.ended_at IS NULL FOR UPDATE;
            v_period := public.record_insurance_period_transition(v_driver_id, 2::smallint, v_ride_id);
            IF COALESCE(v_period->>'status', '') NOT IN ('ok','noop') THEN
                RAISE EXCEPTION 'accept Period-2 transition failed driver_id=% ride_id=% result=%',
                    v_driver_id, v_ride_id, v_period USING ERRCODE = 'P0001';
            END IF;
            v_release := jsonb_build_object('released', false, 'period', 2, 'miss_counted', false,
                                            'miss_streak', 0, 'paused', false);
            v_persist := true;
        END IF;

    ELSIF p_action = 'decline' THEN
        IF v_current_session IS DISTINCT FROM p_actor_session_id THEN
            RETURN jsonb_build_object('code','SESSION_SUPERSEDED','offer_id',p_offer_id);
        ELSIF v_offer.status <> 'pending' THEN
            v_release := public._release_offer_claim_locked(v_driver_id, v_offer, v_now, 'keep', false, p_miss_threshold);
            v_code := 'OFFER_ALREADY_RESOLVED';
        ELSIF v_offer.controller_session_id <> p_actor_session_id
              OR v_driver.controller_session_id IS DISTINCT FROM p_actor_session_id THEN
            RETURN jsonb_build_object('code','OFFER_EXPIRED','reason_code','OFFER_SESSION_ENDED',
                                      'offer_id',p_offer_id,'expires_at',v_offer.expires_at,'server_time',v_now);
        ELSE
            UPDATE public.ride_offers SET status = 'declined', outcome = 'declined', outcome_at = v_now,
                   responded_at = v_now
             WHERE id = p_offer_id RETURNING * INTO v_offer;
            -- A stale epoch may still decline; only a current one refreshes readiness.
            v_release := public._release_offer_claim_locked(v_driver_id, v_offer, v_now, 'reset',
                                                            v_driver.online_epoch = p_expected_epoch, p_miss_threshold);
            v_persist := true;
        END IF;

    ELSIF p_action = 'expire' THEN
        IF v_offer.status <> 'pending' THEN
            v_release := public._release_offer_claim_locked(v_driver_id, v_offer, v_now, 'keep', false, p_miss_threshold);
            v_code := 'OFFER_ALREADY_RESOLVED';
        ELSIF v_now < v_offer.expires_at THEN
            RETURN jsonb_build_object('code','OFFER_NOT_EXPIRED','offer_id',p_offer_id,
                                      'expires_at',v_offer.expires_at,'server_time',v_now);
        ELSE
            IF v_ride.status = 'cancelled' AND v_ride.cancelled_at IS NOT NULL
               AND v_ride.cancelled_at <= v_offer.expires_at THEN
                v_outcome := 'cancelled'; v_status := 'cancelled';
            ELSIF public.offer_expiry_counts_as_miss(v_offer, v_driver) THEN
                v_outcome := 'expired_nonresponse'; v_status := 'expired';
            ELSIF v_driver.online_epoch <> v_offer.online_epoch
                  OR v_driver.controller_session_id IS DISTINCT FROM v_offer.controller_session_id THEN
                v_outcome := 'expired_availability_changed'; v_status := 'expired';
            ELSE
                v_outcome := 'expired_delivery_unknown'; v_status := 'expired';
            END IF;
            UPDATE public.ride_offers SET status = v_status, outcome = v_outcome, outcome_at = v_now,
                   responded_at = COALESCE(responded_at, v_now)
             WHERE id = p_offer_id RETURNING * INTO v_offer;
            v_release := public._release_offer_claim_locked(
                v_driver_id, v_offer, v_now,
                CASE WHEN v_outcome = 'expired_nonresponse' THEN 'increment' ELSE 'keep' END,
                false, p_miss_threshold);
            v_persist := true;
        END IF;

    ELSE  -- cancel_unaccepted
        IF v_offer.status = 'pending' AND v_ride.status = 'searching' THEN
            RETURN jsonb_build_object('code','RIDE_STATE_CONFLICT','reason_code','RIDE_STILL_SEARCHING',
                                      'offer_id',p_offer_id,'ride_status',v_ride.status);
        ELSIF v_offer.status = 'accepted' THEN
            RETURN jsonb_build_object('code','OFFER_ALREADY_RESOLVED','offer_id',p_offer_id,
                                      'outcome',v_offer.outcome,'offer_status',v_offer.status);
        ELSIF v_offer.status = 'pending' THEN
            UPDATE public.ride_offers SET status = 'cancelled', outcome = 'cancelled', outcome_at = v_now,
                   responded_at = COALESCE(responded_at, v_now)
             WHERE id = p_offer_id RETURNING * INTO v_offer;
        ELSIF v_offer.outcome IS NULL THEN
            -- Bulk-cancelled or legacy-preempted rows: record the outcome once.
            UPDATE public.ride_offers
               SET outcome = CASE status WHEN 'cancelled' THEN 'cancelled' WHEN 'preempted' THEN 'preempted'
                                         WHEN 'declined' THEN 'declined' ELSE 'expired_delivery_unknown' END,
                   outcome_at = v_now
             WHERE id = p_offer_id RETURNING * INTO v_offer;
        END IF;
        v_release := public._release_offer_claim_locked(v_driver_id, v_offer, v_now, 'keep', false, p_miss_threshold);
        v_persist := true;
    END IF;

    SELECT * INTO v_ride FROM public.rides WHERE id = v_ride_id;
    v_result := jsonb_build_object(
        'code', v_code, 'action', p_action, 'offer_id', v_offer.id, 'claim_id', v_offer.claim_id,
        'ride_id', v_ride_id, 'driver_id', v_driver_id, 'driver_user_id', v_driver.user_id,
        'outcome', v_offer.outcome, 'offer_status', v_offer.status,
        'online_epoch', v_offer.online_epoch::text, 'offered_at', v_offer.offered_at,
        'expires_at', v_offer.expires_at, 'server_time', v_now, 'ride_status', v_ride.status,
        'remaining_pending_offers', (SELECT count(*) FROM public.ride_offers
                                      WHERE ride_id = v_ride_id AND status = 'pending'),
        'released', COALESCE((v_release->>'released')::boolean, false),
        'period', v_release->'period',
        'miss_counted', COALESCE((v_release->>'miss_counted')::boolean, false),
        'miss_streak', v_release->'miss_streak',
        'paused', COALESCE((v_release->>'paused')::boolean, false),
        'availability', v_release->'availability',
        'losers', v_losers, 'already_accepted', v_already
    ) || v_extra;
    IF v_persist THEN
        INSERT INTO public.driver_offer_decisions
            (offer_id, request_id, action, claim_id, expected_epoch, actor_session_id, result)
        VALUES (p_offer_id, p_request_id, p_action, p_claim_id, p_expected_epoch, p_actor_session_id, v_result)
        ON CONFLICT (offer_id, request_id) DO NOTHING;
    END IF;
    RETURN v_result;
END;
$$;
REVOKE ALL ON FUNCTION public.resolve_driver_offer(uuid, uuid, bigint, text, text, text, integer)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.resolve_driver_offer(uuid, uuid, bigint, text, text, text, integer)
    TO service_role;

COMMIT;

NOTIFY pgrst, 'reload schema';
