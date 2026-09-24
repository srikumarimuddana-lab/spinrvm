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

COMMIT;

NOTIFY pgrst, 'reload schema';
