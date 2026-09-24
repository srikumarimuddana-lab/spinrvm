-- Rollback: drop dispatch_claim_offers_v3, driver_offer_admission_reason,
-- the two ride_offers columns (online_epoch, controller_session_id), and
-- settings.dispatch_admission_shadow_enabled.
-- migration-override-ok: T4 adds new RPC and predicate that build on 457/458
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '20s';

-- ── Part A: schema additions ────────────────────────────────────────

ALTER TABLE public.ride_offers
    ADD COLUMN IF NOT EXISTS online_epoch bigint,
    ADD COLUMN IF NOT EXISTS controller_session_id text;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS dispatch_admission_shadow_enabled boolean
    NOT NULL DEFAULT false;

-- ── Private admission predicate ─────────────────────────────────────
-- Returns the first failing reason, or NULL when the driver is admissible.
-- SECURITY INVOKER: only callable from other server-side functions.
CREATE OR REPLACE FUNCTION public.driver_offer_admission_reason(
    p_driver   public.drivers,
    p_session_id text,
    p_online_epoch bigint,
    p_contact_valid_until timestamptz,
    p_location_valid_until timestamptz,
    p_now      timestamptz,
    p_mode     text,
    p_ride_id  text,
    p_require_subscription boolean
) RETURNS text
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_controller text;
    v_current_session text;
BEGIN
    -- 1. DRIVER_OFFLINE
    IF NOT p_driver.is_online THEN
        RETURN 'DRIVER_OFFLINE';
    END IF;

    -- 2. SESSION_SUPERSEDED
    v_controller := p_driver.controller_session_id;
    IF v_controller IS NULL
       OR v_controller IS DISTINCT FROM COALESCE(p_session_id, v_controller)
    THEN
        RETURN 'SESSION_SUPERSEDED';
    END IF;
    SELECT u.current_session_id INTO v_current_session
      FROM public.users u WHERE u.id = p_driver.user_id;
    IF v_current_session IS DISTINCT FROM v_controller THEN
        RETURN 'SESSION_SUPERSEDED';
    END IF;

    -- 3. ONLINE_EPOCH_STALE (automatic mode only)
    IF p_mode = 'automatic' AND p_driver.online_epoch <> p_online_epoch THEN
        RETURN 'ONLINE_EPOCH_STALE';
    END IF;

    -- 4. REQUESTS_PAUSED
    IF NOT p_driver.accepting_requests THEN
        RETURN 'REQUESTS_PAUSED';
    END IF;

    -- 5. ELIGIBILITY_BLOCKED
    IF p_driver.status <> 'active' OR NOT p_driver.is_verified THEN
        RETURN 'ELIGIBILITY_BLOCKED';
    END IF;

    -- 6. OBLIGATION_CONFLICT
    IF NOT p_driver.is_available THEN
        RETURN 'OBLIGATION_CONFLICT';
    END IF;
    IF p_driver.availability_claim_id IS NOT NULL THEN
        RETURN 'OBLIGATION_CONFLICT';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.rides r
        WHERE r.driver_id = p_driver.id
          AND r.status IN ('driver_assigned','driver_accepted','driver_arrived','in_progress')
          AND r.id <> p_ride_id
    ) THEN
        RETURN 'OBLIGATION_CONFLICT';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.ride_offers ro
        JOIN public.rides r ON r.id = ro.ride_id
        WHERE ro.driver_id = p_driver.id
          AND ro.status IN ('pending','accepted')
          AND r.status IN ('searching','driver_assigned','driver_accepted','driver_arrived','in_progress')
          AND ro.ride_id <> p_ride_id
    ) THEN
        RETURN 'OBLIGATION_CONFLICT';
    END IF;

    -- Automatic mode only checks below
    IF p_mode = 'automatic' THEN
        -- 7. PRESENCE_UNAVAILABLE
        IF p_contact_valid_until IS NULL
           OR p_contact_valid_until <= p_now
           OR p_contact_valid_until > p_now + interval '95 seconds'
        THEN
            RETURN 'PRESENCE_UNAVAILABLE';
        END IF;
        IF p_driver.last_contact_at IS NULL
           OR p_driver.last_contact_at <= p_now - interval '120 seconds'
        THEN
            RETURN 'PRESENCE_UNAVAILABLE';
        END IF;

        -- 8. LOCATION_STALE
        IF p_location_valid_until IS NULL OR p_location_valid_until <= p_now THEN
            RETURN 'LOCATION_STALE';
        END IF;
        IF p_driver.location_captured_at IS NULL
           OR p_driver.location_captured_at < p_now - interval '60 seconds'
           OR p_driver.location_captured_at > p_now + interval '5 seconds'
        THEN
            RETURN 'LOCATION_STALE';
        END IF;

        -- 9. READINESS_EXPIRED
        IF public.driver_readiness_enforced()
           AND (p_driver.ready_until IS NULL OR p_driver.ready_until <= p_now)
        THEN
            RETURN 'READINESS_EXPIRED';
        END IF;

        -- 10. ENTITLEMENT_BLOCKED
        IF p_require_subscription AND NOT EXISTS (
            SELECT 1 FROM public.driver_subscriptions ds
            WHERE ds.driver_id = p_driver.id
              AND ds.status = 'active'
              AND (ds.expires_at IS NULL OR ds.expires_at > p_now)
        ) THEN
            RETURN 'ENTITLEMENT_BLOCKED';
        END IF;
    END IF;

    RETURN NULL;  -- admissible
END;
$$;
REVOKE ALL ON FUNCTION public.driver_offer_admission_reason(
    public.drivers, text, bigint, timestamptz, timestamptz, timestamptz, text, text, boolean
) FROM PUBLIC, anon, authenticated, service_role;

-- ── Part B: dispatch_claim_offers_v3 RPC ────────────────────────────
CREATE OR REPLACE FUNCTION public.dispatch_claim_offers_v3(
    p_ride_id              text,
    p_candidates           jsonb,
    p_max_offers           integer,
    p_offer_ttl_seconds    integer,
    p_require_subscription boolean DEFAULT false,
    p_mode                 text    DEFAULT 'automatic'
) RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
SET lock_timeout = '2s'
AS $$
DECLARE
    v_flag boolean;
    v_ride public.rides%ROWTYPE;
    v_cand jsonb;
    v_ord  bigint;
    v_driver public.drivers%ROWTYPE;
    v_now timestamptz;
    v_reason text;
    v_results jsonb := '[]'::jsonb;
    v_locked_drivers text[] := '{}';
    v_locked_count int := 0;
    v_offer_now timestamptz;
    v_expires timestamptz;
    v_claimed_count int := 0;
    v_claim_id uuid;
    v_offer_id uuid;
    v_period_result jsonb;
    -- candidate parse vars
    v_driver_id text;
    v_session_id text;
    v_online_epoch bigint;
    v_contact_valid timestamptz;
    v_location_valid timestamptz;
    v_eta_seconds int;
BEGIN
    -- ── Validation ──────────────────────────────────────────────────
    IF p_ride_id IS NULL OR p_ride_id = '' THEN
        RAISE EXCEPTION 'ride_id is required' USING ERRCODE = '22023';
    END IF;
    IF p_mode NOT IN ('automatic','admin_direct') THEN
        RAISE EXCEPTION 'mode must be automatic or admin_direct' USING ERRCODE = '22023';
    END IF;
    IF p_max_offers < 1 OR p_max_offers > 10 THEN
        RAISE EXCEPTION 'max_offers must be 1..10' USING ERRCODE = '22023';
    END IF;
    IF p_offer_ttl_seconds < 5 OR p_offer_ttl_seconds > 120 THEN
        RAISE EXCEPTION 'offer_ttl_seconds must be 5..120' USING ERRCODE = '22023';
    END IF;
    IF p_candidates IS NULL OR jsonb_typeof(p_candidates) <> 'array' THEN
        RAISE EXCEPTION 'candidates must be a JSON array' USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(p_candidates) > 50 THEN
        RAISE EXCEPTION 'candidates array exceeds 50 entries' USING ERRCODE = '22023';
    END IF;
    IF p_mode = 'admin_direct' AND (jsonb_array_length(p_candidates) <> 1 OR p_max_offers <> 1) THEN
        RAISE EXCEPTION 'admin_direct requires exactly one candidate and max_offers=1' USING ERRCODE = '22023';
    END IF;

    -- ── Flag check ──────────────────────────────────────────────────
    SELECT s.driver_availability_v2_enabled INTO v_flag
      FROM public.settings s WHERE s.id = 'app_settings';
    IF NOT COALESCE(v_flag, false) THEN
        RETURN jsonb_build_object('code','AVAILABILITY_V2_DISABLED','results','[]'::jsonb);
    END IF;

    -- ── Step 1: unlocked ride pre-check ─────────────────────────────
    SELECT * INTO v_ride FROM public.rides WHERE id = p_ride_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('code','RIDE_STATE_CONFLICT','ride_status','not_found');
    END IF;
    IF p_mode = 'automatic' THEN
        IF v_ride.status <> 'searching' OR v_ride.driver_id IS NOT NULL THEN
            RETURN jsonb_build_object('code','RIDE_STATE_CONFLICT','ride_status',v_ride.status);
        END IF;
    ELSE
        -- admin_direct: ride must be driver_assigned with driver_id = candidate
        IF v_ride.status <> 'driver_assigned' THEN
            RETURN jsonb_build_object('code','RIDE_STATE_CONFLICT','ride_status',v_ride.status);
        END IF;
    END IF;

    -- ── Step 2 (Phase 1): driver locks ──────────────────────────────
    FOR v_cand, v_ord IN SELECT value, ordinality FROM jsonb_array_elements(p_candidates) WITH ORDINALITY
    LOOP
        EXIT WHEN v_locked_count >= p_max_offers;

        -- Parse candidate fields
        v_driver_id := v_cand ->> 'driver_id';
        v_session_id := NULL;
        v_online_epoch := NULL;
        v_contact_valid := NULL;
        v_location_valid := NULL;
        v_eta_seconds := 0;

        IF v_driver_id IS NULL OR v_driver_id = '' THEN
            v_results := v_results || jsonb_build_object(
                'driver_id', v_cand ->> 'driver_id',
                'claimed', false, 'reason_code', 'INVALID_EVIDENCE');
            CONTINUE;
        END IF;

        IF p_mode = 'automatic' THEN
            v_session_id := v_cand ->> 'session_id';
            -- Parse online_epoch
            BEGIN
                v_online_epoch := (v_cand ->> 'online_epoch')::bigint;
                IF v_online_epoch IS NULL OR v_online_epoch < 0
                   OR length(v_cand ->> 'online_epoch') > 19
                   OR (v_cand ->> 'online_epoch') !~ '^[0-9]{1,19}$' THEN
                    RAISE EXCEPTION 'invalid epoch';
                END IF;
            EXCEPTION WHEN OTHERS THEN
                v_results := v_results || jsonb_build_object(
                    'driver_id', v_driver_id, 'claimed', false, 'reason_code', 'INVALID_EVIDENCE');
                CONTINUE;
            END;
            -- Parse timestamps
            BEGIN
                v_contact_valid := (v_cand ->> 'contact_valid_until')::timestamptz;
                v_location_valid := (v_cand ->> 'location_valid_until')::timestamptz;
            EXCEPTION WHEN OTHERS THEN
                v_results := v_results || jsonb_build_object(
                    'driver_id', v_driver_id, 'claimed', false, 'reason_code', 'INVALID_EVIDENCE');
                CONTINUE;
            END;
        END IF;

        BEGIN
            v_eta_seconds := GREATEST(COALESCE((v_cand ->> 'eta_seconds')::int, 0), 0);
        EXCEPTION WHEN OTHERS THEN
            v_eta_seconds := 0;
        END;

        -- Lock the driver
        IF p_mode = 'automatic' THEN
            SELECT * INTO v_driver FROM public.drivers
              WHERE id = v_driver_id FOR UPDATE SKIP LOCKED;
        ELSE
            SELECT * INTO v_driver FROM public.drivers
              WHERE id = v_driver_id FOR UPDATE;
        END IF;

        IF NOT FOUND THEN
            v_results := v_results || jsonb_build_object(
                'driver_id', v_driver_id, 'claimed', false,
                'reason_code', CASE WHEN p_mode = 'automatic' THEN 'CLAIM_CONTENDED' ELSE 'DRIVER_NOT_FOUND' END);
            CONTINUE;
        END IF;

        v_now := clock_timestamp();
        v_reason := public.driver_offer_admission_reason(
            v_driver, v_session_id, v_online_epoch,
            v_contact_valid, v_location_valid, v_now, p_mode, p_ride_id, p_require_subscription);

        IF v_reason IS NOT NULL THEN
            v_results := v_results || jsonb_build_object(
                'driver_id', v_driver_id, 'claimed', false, 'reason_code', v_reason);
            CONTINUE;
        END IF;

        v_locked_drivers := array_append(v_locked_drivers, v_driver_id);
        v_locked_count := v_locked_count + 1;
    END LOOP;

    -- ── Step 3 (Phase 2): ride lock ─────────────────────────────────
    SELECT * INTO v_ride FROM public.rides WHERE id = p_ride_id FOR UPDATE;
    IF p_mode = 'automatic' THEN
        IF v_ride.status <> 'searching' OR v_ride.driver_id IS NOT NULL THEN
            -- Mark every locked candidate RIDE_STATE_CONFLICT
            FOR v_ord IN 1..array_length(v_locked_drivers, 1) LOOP
                v_results := v_results || jsonb_build_object(
                    'driver_id', v_locked_drivers[v_ord], 'claimed', false, 'reason_code', 'RIDE_STATE_CONFLICT');
            END LOOP;
            RETURN jsonb_build_object('code','RIDE_STATE_CONFLICT','ride_status',v_ride.status,'results',v_results);
        END IF;
    ELSE
        IF v_ride.status <> 'driver_assigned' THEN
            v_results := v_results || jsonb_build_object(
                'driver_id', v_locked_drivers[1], 'claimed', false, 'reason_code', 'RIDE_STATE_CONFLICT');
            RETURN jsonb_build_object('code','RIDE_STATE_CONFLICT','ride_status',v_ride.status,'results',v_results);
        END IF;
    END IF;

    -- ── Step 4: timestamps ──────────────────────────────────────────
    v_offer_now := clock_timestamp();
    v_expires := v_offer_now + make_interval(secs => p_offer_ttl_seconds);

    -- ── Step 5 (Phase 3): claim in rank order ───────────────────────
    FOR v_ord IN 1..COALESCE(array_length(v_locked_drivers, 1), 0) LOOP
        v_driver_id := v_locked_drivers[v_ord];

        -- Re-read driver after ride lock wait
        SELECT * INTO v_driver FROM public.drivers WHERE id = v_driver_id;

        -- Re-parse candidate evidence for this driver
        SELECT value INTO v_cand FROM jsonb_array_elements(p_candidates)
          WHERE value ->> 'driver_id' = v_driver_id LIMIT 1;

        v_session_id := NULL;
        v_online_epoch := NULL;
        v_contact_valid := NULL;
        v_location_valid := NULL;
        IF p_mode = 'automatic' THEN
            v_session_id := v_cand ->> 'session_id';
            BEGIN
                v_online_epoch := (v_cand ->> 'online_epoch')::bigint;
                v_contact_valid := (v_cand ->> 'contact_valid_until')::timestamptz;
                v_location_valid := (v_cand ->> 'location_valid_until')::timestamptz;
            EXCEPTION WHEN OTHERS THEN
                v_results := v_results || jsonb_build_object(
                    'driver_id', v_driver_id, 'claimed', false, 'reason_code', 'INVALID_EVIDENCE');
                CONTINUE;
            END;
        END IF;

        -- Re-run admission at v_offer_now
        v_reason := public.driver_offer_admission_reason(
            v_driver, v_session_id, v_online_epoch,
            v_contact_valid, v_location_valid, v_offer_now, p_mode, p_ride_id, p_require_subscription);

        IF v_reason IS NOT NULL THEN
            v_results := v_results || jsonb_build_object(
                'driver_id', v_driver_id, 'claimed', false, 'reason_code', v_reason);
            CONTINUE;
        END IF;

        -- RIDER_OWNED_DRIVER check
        IF v_ride.rider_id IS NOT NULL AND v_ride.rider_id = v_driver.user_id THEN
            v_results := v_results || jsonb_build_object(
                'driver_id', v_driver_id, 'claimed', false, 'reason_code', 'RIDER_OWNED_DRIVER');
            CONTINUE;
        END IF;

        -- ALREADY_OFFERED (automatic mode)
        IF p_mode = 'automatic' AND EXISTS (
            SELECT 1 FROM public.ride_offers ro WHERE ro.ride_id = p_ride_id AND ro.driver_id = v_driver_id
        ) THEN
            v_results := v_results || jsonb_build_object(
                'driver_id', v_driver_id, 'claimed', false, 'reason_code', 'ALREADY_OFFERED');
            CONTINUE;
        END IF;

        -- ── Claim ───────────────────────────────────────────────────
        v_claim_id := gen_random_uuid();
        UPDATE public.drivers SET
            is_available = false,
            availability_claim_id = v_claim_id,
            availability_claimed_at = v_offer_now
        WHERE id = v_driver_id
        RETURNING availability_claim_id INTO v_claim_id;

        IF p_mode = 'automatic' THEN
            INSERT INTO public.ride_offers (
                ride_id, driver_id, status, eta_seconds,
                offered_at, expires_at, claim_id,
                online_epoch, controller_session_id
            ) VALUES (
                p_ride_id, v_driver_id, 'pending', v_eta_seconds,
                v_offer_now, v_expires, v_claim_id,
                v_driver.online_epoch, v_driver.controller_session_id
            ) RETURNING id INTO v_offer_id;
        ELSE
            -- admin_direct: no offer row, update ride notified timestamp
            v_offer_id := NULL;
            UPDATE public.rides SET driver_notified_at = v_offer_now WHERE id = p_ride_id;
        END IF;

        -- Claim, offer and insurance Period 2 are one transaction. Never
        -- leave a dispatch-busy driver or live offer without its audit row.
        v_period_result := public.record_insurance_period_transition(v_driver_id, 2::smallint, p_ride_id);
        IF COALESCE(v_period_result->>'status','') NOT IN ('ok','noop') THEN
            RAISE EXCEPTION 'dispatch_claim_offers_v3: insurance Period-2 transition failed for driver % ride %: %',
                v_driver_id, p_ride_id, v_period_result USING ERRCODE = 'P0001';
        END IF;

        v_claimed_count := v_claimed_count + 1;
        v_results := v_results || jsonb_build_object(
            'driver_id', v_driver_id,
            'user_id', v_driver.user_id,
            'claimed', true,
            'offer_id', v_offer_id,
            'claim_id', v_claim_id,
            'online_epoch', v_driver.online_epoch::text,
            'eta_seconds', v_eta_seconds,
            'insurance_written', true
        );
    END LOOP;

    RETURN jsonb_build_object(
        'code', 'OK',
        'ride_id', p_ride_id,
        'ride_status', v_ride.status,
        'server_time', v_offer_now,
        'offered_at', v_offer_now,
        'expires_at', v_expires,
        'claimed_count', v_claimed_count,
        'results', v_results
    );
END;
$$;
REVOKE ALL ON FUNCTION public.dispatch_claim_offers_v3(text, jsonb, integer, integer, boolean, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.dispatch_claim_offers_v3(text, jsonb, integer, integer, boolean, text)
    TO service_role;

COMMIT;

NOTIFY pgrst, 'reload schema';
