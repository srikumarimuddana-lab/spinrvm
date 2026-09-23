-- Rollback: keep the dark flag false, drain all users of the RPC, then drop
-- transition_driver_availability(), driver_availability_requests, the seven
-- driver availability columns, settings.driver_availability_v2_enabled, and indexes.
-- Do not change driver session generation here; availability is bound to the
-- already-authenticated session supplied by the backend.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '20s';

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS driver_availability_v2_enabled boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN public.settings.driver_availability_v2_enabled IS
    'Dark rollout gate for backend-only epoch-based driver availability transitions.';

ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS online_epoch bigint NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS controller_session_id text,
    ADD COLUMN IF NOT EXISTS accepting_requests boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS last_contact_at timestamptz,
    ADD COLUMN IF NOT EXISTS ready_until timestamptz,
    ADD COLUMN IF NOT EXISTS availability_reason text NOT NULL DEFAULT 'legacy',
    ADD COLUMN IF NOT EXISTS state_version bigint NOT NULL DEFAULT 0;

-- Column defaults backfill existing rows conservatively: legacy online flags
-- remain intact, admission stays closed, and no synthetic contact time is set.

-- service-role-only table: RPC owns command authorization and clients never
-- read or write idempotency records directly, so no client RLS policy applies.
CREATE TABLE IF NOT EXISTS public.driver_availability_requests (
    -- Request IDs are scoped to driver identity, so independent drivers may
    -- legitimately submit the same opaque client-generated key.
    driver_id text NOT NULL REFERENCES public.drivers(id) ON DELETE CASCADE,
    request_id text NOT NULL,
    expected_epoch bigint NOT NULL,
    authenticated_session_id text NOT NULL,
    action text NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (driver_id, request_id)
);
ALTER TABLE public.driver_availability_requests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.driver_availability_requests FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.driver_availability_requests TO service_role;

CREATE INDEX IF NOT EXISTS driver_availability_requests_driver_created_idx
    ON public.driver_availability_requests (driver_id, created_at DESC);

CREATE OR REPLACE FUNCTION public.transition_driver_availability(
    p_driver_id text,
    p_expected_epoch bigint,
    p_authenticated_session_id text,
    p_action text,
    p_request_id text
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_driver public.drivers%ROWTYPE;
    v_saved public.driver_availability_requests%ROWTYPE;
    v_has_obligation boolean := false;
    v_has_trip boolean := false;
    v_has_offer boolean := false;
    v_next_online boolean;
    v_next_accepting boolean;
    v_reason text;
    v_result jsonb;
    v_period_result jsonb;
BEGIN
    IF p_request_id IS NULL OR length(btrim(p_request_id)) = 0
       OR p_authenticated_session_id IS NULL OR length(btrim(p_authenticated_session_id)) = 0
       OR p_expected_epoch IS NULL OR p_expected_epoch < 0 THEN
        RAISE EXCEPTION 'request_id, authenticated session, and non-negative expected epoch are required' USING ERRCODE = '22023';
    END IF;
    IF p_action IS NULL OR p_action NOT IN ('go_online','go_offline','stop_requests','pause_unreachable',
                        'pause_idle','pause_misses','displace_controller') THEN
        RAISE EXCEPTION 'unsupported availability action: %', p_action USING ERRCODE = '22023';
    END IF;

    -- Global order: driver -> assigned ride rows -> offer rows -> insurance period.
    SELECT * INTO v_driver FROM public.drivers WHERE id = p_driver_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('code','DRIVER_NOT_FOUND'); END IF;

    -- Idempotency is checked after taking the driver lock, serializing duplicate
    -- request IDs for this driver. Reuse with different command bytes is rejected.
    SELECT * INTO v_saved FROM public.driver_availability_requests
     WHERE driver_id = p_driver_id AND request_id = p_request_id;
    IF FOUND THEN
        IF v_saved.expected_epoch <> p_expected_epoch
           OR v_saved.authenticated_session_id <> p_authenticated_session_id OR v_saved.action <> p_action THEN
            RETURN jsonb_build_object('code','IDEMPOTENCY_KEY_CONFLICT');
        END IF;
        RETURN v_saved.result;
    END IF;

    IF NOT COALESCE((SELECT driver_availability_v2_enabled FROM public.settings WHERE id='app_settings'), false) THEN
        RETURN jsonb_build_object('code','AVAILABILITY_V2_DISABLED');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM public.users u WHERE u.id = v_driver.user_id
                   AND u.current_session_id = p_authenticated_session_id) THEN
        RETURN jsonb_build_object('code','UNAUTHORIZED_SESSION');
    END IF;
    IF v_driver.online_epoch <> p_expected_epoch THEN
        RETURN jsonb_build_object('code','ONLINE_EPOCH_STALE',
                                  'online_epoch',v_driver.online_epoch::text,
                                  'state_version',v_driver.state_version::text);
    END IF;
    IF v_driver.controller_session_id IS NOT NULL
       AND v_driver.controller_session_id <> p_authenticated_session_id
       AND p_action <> 'displace_controller' THEN
        RETURN jsonb_build_object('code','CONTROLLER_SESSION_MISMATCH',
                                  'online_epoch',v_driver.online_epoch::text,
                                  'state_version',v_driver.state_version::text);
    END IF;

    PERFORM 1 FROM public.rides r
     WHERE r.status IN ('searching','driver_assigned','driver_accepted','driver_arrived','in_progress')
       AND (r.driver_id = p_driver_id OR EXISTS (
           SELECT 1 FROM public.ride_offers candidate
            WHERE candidate.driver_id=p_driver_id AND candidate.ride_id=r.id
              AND candidate.status IN ('pending','accepted')))
     ORDER BY r.id FOR UPDATE;
    SELECT EXISTS (
        SELECT 1 FROM public.rides r
         WHERE r.driver_id = p_driver_id
           AND r.status IN ('driver_assigned','driver_accepted','driver_arrived','in_progress')
    ) INTO v_has_trip;
    PERFORM 1 FROM public.ride_offers ro
      JOIN public.rides r ON r.id = ro.ride_id
     WHERE ro.driver_id = p_driver_id AND ro.status IN ('pending','accepted')
       AND r.status IN ('searching','driver_assigned','driver_accepted','driver_arrived','in_progress')
     ORDER BY ro.ride_id FOR UPDATE OF ro;
    v_has_offer := FOUND;
    v_has_obligation := v_has_trip OR v_has_offer;

    IF p_action = 'go_offline' AND v_has_obligation THEN
        RETURN jsonb_build_object('code','OBLIGATION_ACTIVE','online_epoch',v_driver.online_epoch::text,
                                  'state_version',v_driver.state_version::text,
                                  'has_trip',v_has_trip,'has_pending_offer',v_has_offer);
    END IF;
    IF p_action = 'go_online' AND v_has_obligation THEN
        RETURN jsonb_build_object('code','OBLIGATION_ACTIVE','online_epoch',v_driver.online_epoch::text,
                                  'state_version',v_driver.state_version::text,
                                  'has_trip',v_has_trip,'has_pending_offer',v_has_offer);
    END IF;

    v_next_online := v_driver.is_online;
    v_next_accepting := v_driver.accepting_requests;
    v_reason := p_action;
    IF p_action = 'go_online' THEN
        v_next_online := true; v_next_accepting := true;
    ELSIF p_action = 'go_offline' THEN
        v_next_online := false; v_next_accepting := false;
    ELSIF p_action = 'stop_requests' OR p_action IN ('pause_unreachable','pause_idle','pause_misses') THEN
        v_next_accepting := false;
        IF NOT v_has_obligation THEN v_next_online := false; END IF;
    ELSIF p_action = 'displace_controller' THEN
        v_next_accepting := false;
        v_reason := 'controller_displaced';
    END IF;

    IF p_action = 'go_online' AND v_driver.controller_session_id IS NULL THEN
        v_driver.controller_session_id := p_authenticated_session_id;
    ELSIF p_action = 'displace_controller' THEN
        v_driver.controller_session_id := p_authenticated_session_id;
    END IF;

    -- Preserve Period 2/3 and its ride identity while any assigned work remains.
    -- No-obligation offline transitions close the current interval into Period 0.
    IF NOT v_next_online AND NOT v_has_obligation THEN
        PERFORM 1 FROM public.driver_insurance_periods dip
         WHERE dip.driver_id = p_driver_id AND dip.ended_at IS NULL FOR UPDATE;
        v_period_result := public.record_insurance_period_transition(p_driver_id,0::smallint,NULL);
        IF COALESCE(v_period_result->>'status','') NOT IN ('ok','noop') THEN
            RAISE EXCEPTION 'availability Period-0 transition failed driver_id=% result=%',
                p_driver_id, v_period_result USING ERRCODE = 'P0001';
        END IF;
    ELSIF p_action = 'go_online' THEN
        PERFORM 1 FROM public.driver_insurance_periods dip
         WHERE dip.driver_id = p_driver_id AND dip.ended_at IS NULL FOR UPDATE;
        v_period_result := public.record_insurance_period_transition(p_driver_id,1::smallint,NULL);
        IF COALESCE(v_period_result->>'status','') NOT IN ('ok','noop') THEN
            RAISE EXCEPTION 'availability Period-1 transition failed driver_id=% result=%',
                p_driver_id, v_period_result USING ERRCODE = 'P0001';
        END IF;
    END IF;

    UPDATE public.drivers
       SET is_online = v_next_online,
           is_available = v_next_accepting AND v_next_online AND NOT v_has_obligation,
           online_epoch = online_epoch + 1,
           state_version = state_version + 1,
           controller_session_id = v_driver.controller_session_id,
           accepting_requests = v_next_accepting,
           updated_at = now(),
           last_status_changed_at = CASE WHEN is_online IS DISTINCT FROM v_next_online THEN now() ELSE last_status_changed_at END,
           went_online_at = CASE WHEN is_online IS DISTINCT FROM v_next_online AND v_next_online THEN now() ELSE went_online_at END,
           went_offline_at = CASE WHEN is_online IS DISTINCT FROM v_next_online AND NOT v_next_online THEN now() ELSE went_offline_at END,
           last_contact_at = CASE WHEN p_action IN ('go_online','go_offline','stop_requests','displace_controller') THEN now() ELSE last_contact_at END,
           ready_until = CASE WHEN p_action = 'go_online' THEN clock_timestamp() + interval '62 minutes'
                              WHEN NOT v_next_accepting THEN NULL ELSE ready_until END,
           availability_reason = v_reason
     WHERE id = p_driver_id
     RETURNING * INTO v_driver;

    v_result := jsonb_build_object(
        'code','OK','driver_id',v_driver.id,'online_epoch',v_driver.online_epoch::text,
        'controller_session_id',v_driver.controller_session_id,'is_online',v_driver.is_online,
        'accepting_requests',v_driver.accepting_requests,'is_available',v_driver.is_available,
        'last_contact_at',v_driver.last_contact_at,'ready_until',v_driver.ready_until,
        'availability_reason',v_driver.availability_reason,'state_version',v_driver.state_version::text,
        'has_trip',v_has_trip,'has_pending_offer',v_has_offer,
        'pending_reason',CASE WHEN v_has_trip THEN 'active_trip' WHEN v_has_offer THEN 'offer_obligation' ELSE NULL END
    );
    INSERT INTO public.driver_availability_requests
        (request_id,driver_id,expected_epoch,authenticated_session_id,action,result)
    VALUES (p_request_id,p_driver_id,p_expected_epoch,p_authenticated_session_id,p_action,v_result);
    RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION public.transition_driver_availability(text,bigint,text,text,text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.transition_driver_availability(text,bigint,text,text,text) TO service_role;
COMMENT ON FUNCTION public.transition_driver_availability(text,bigint,text,text,text) IS
    'Backend-only atomic availability state transition. Lock order: driver, assigned rides, active offers, insurance period.';

-- One MVCC statement returns driver state, active trip, live pending offer,
-- rollout flag, and database clock. This keeps obligations and epoch aligned
-- across replicas. Expiry uses the authoritative clock captured here.
CREATE OR REPLACE FUNCTION public.get_driver_availability_snapshot(p_user_id text)
RETURNS jsonb
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    WITH db_clock AS MATERIALIZED (
        SELECT clock_timestamp() AS server_time
    ), driver_row AS MATERIALIZED (
        SELECT d.* FROM public.drivers d WHERE d.user_id = p_user_id
        ORDER BY d.id LIMIT 1
    ), active_trip AS MATERIALIZED (
        SELECT r.id, r.status, r.updated_at
        FROM public.rides r CROSS JOIN driver_row d
        WHERE r.driver_id = d.id
          AND r.status IN ('driver_assigned','driver_accepted','driver_arrived','in_progress')
        ORDER BY r.updated_at DESC NULLS LAST, r.id LIMIT 1
    ), live_offer AS MATERIALIZED (
        SELECT ro.id, ro.ride_id, ro.offered_at, ro.expires_at,
               r.status AS ride_status
        FROM public.ride_offers ro
        JOIN public.rides r ON r.id = ro.ride_id
        CROSS JOIN driver_row d CROSS JOIN db_clock c
        WHERE ro.driver_id = d.id AND ro.status = 'pending'
          AND r.status IN ('searching','driver_assigned','driver_accepted','driver_arrived','in_progress')
          AND ro.expires_at IS NOT NULL AND ro.expires_at > c.server_time
        ORDER BY ro.offered_at DESC, ro.id LIMIT 1
    )
    SELECT jsonb_build_object(
        'protocol_enabled', COALESCE((SELECT s.driver_availability_v2_enabled
                                      FROM public.settings s WHERE s.id='app_settings'), false),
        'server_time', c.server_time,
        'driver_count', (SELECT count(*) FROM public.drivers d WHERE d.user_id = p_user_id),
        'driver', (SELECT to_jsonb(d) FROM driver_row d),
        'active_ride', (SELECT to_jsonb(a) FROM active_trip a),
        'pending_offer', (SELECT to_jsonb(o) FROM live_offer o),
        'offer_reconciliation_required', EXISTS (
            SELECT 1 FROM public.ride_offers ro
            JOIN public.rides r ON r.id = ro.ride_id
            CROSS JOIN driver_row d CROSS JOIN db_clock expiry_clock
            WHERE ro.driver_id=d.id AND ro.status='pending'
              AND r.status IN ('searching','driver_assigned','driver_accepted','driver_arrived','in_progress')
              AND (ro.expires_at IS NULL OR ro.expires_at <= expiry_clock.server_time)
        )
    )
    FROM db_clock c
$$;
REVOKE ALL ON FUNCTION public.get_driver_availability_snapshot(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_driver_availability_snapshot(text) TO service_role;
COMMENT ON FUNCTION public.get_driver_availability_snapshot(text) IS
    'Single-statement availability snapshot using one MVCC view and database clock; backend service-role only.';
COMMIT;
