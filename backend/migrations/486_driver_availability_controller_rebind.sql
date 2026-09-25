-- 486_driver_availability_controller_rebind.sql
--
-- CR-2026-094 (#5768): restore the controller rebind path and the replay flag
-- to public.transition_driver_availability.
--
-- Both were designed and implemented (architect addendum items C7/C8 and 8)
-- by editing unmerged migration 457 in place (commits 6b97b28cb, 95aca1d50).
-- Commit 392120698 then restored 457 to its production-applied state on the
-- premise that 458+ already carried those edits; they did not, and 464
-- (the current definition) was written from the restored 457. The result is
-- 12 permanently failing direct_pool tests and a driver who re-logs in on a
-- new phone while the old phone left them online can never go online again
-- (CONTROLLER_SESSION_MISMATCH forever, until an admin intervenes).
--
-- Owner decision 2026-09-25: YES -- a Go from the driver's CURRENT session
-- (users.current_session_id, already required of every non-system caller)
-- takes over an idle controller. A takeover while the driver has an active
-- trip or a pending/accepted offer is still refused (OBLIGATION_ACTIVE, which
-- is checked before any controller write). 464's return-a-code behaviour for
-- system-actor misuse (UNAUTHORIZED_SESSION, not a 22023 raise) is kept.
--
-- Changes vs 464, and nothing else (signature, SECURITY DEFINER, search_path,
-- system-actor allowlist, readiness window, insurance-period handling,
-- REVOKE/GRANT and COMMENT are byte-for-byte 464's):
--   1. go_online joins displace_controller/pause_policy outside the
--      CONTROLLER_SESSION_MISMATCH guard; go_online now binds the caller as
--      controller (464 bound only when the controller was NULL). The
--      unconditional online_epoch bump fences the old controller's leases.
--      Every other command stays fenced to the controller.
--   2. The OK result adds controller_rebound (true only when an existing,
--      different controller was replaced by go_online/displace_controller)
--      and server_time (clock_timestamp()), as in the lost 95aca1d50 edit.
--   3. An idempotent replay returns the stored result plus replayed: true so
--      callers can skip side effects. The stored row itself is unchanged.
-- All three are additive to the JSON contract; backend callers
-- (services/driver_availability_service.py, driver_session_end_service.py,
-- utils/stale_intent_reconciler.py) branch only on result.code.
--
-- Live effect: none at apply time. Production has
-- settings.driver_availability_v2_enabled = false and 0 rows in
-- driver_availability_requests / 0 drivers transitioned via this RPC (as
-- reported on CR-2026-094, 2026-09-25; not re-queried by this migration), so
-- every call returns AVAILABILITY_V2_DISABLED before reaching any changed line
-- (the replay branch precedes the flag check, but no saved request exists).
--
-- Change log: docs/change-log/2026-09-25-cr-2026-094-availability-rpc.md
--
-- Rollback: keep driver_availability_v2_enabled=false (the behavioural
-- rollback, no deploy), then re-apply the CREATE OR REPLACE FUNCTION block
-- (and its REVOKE/GRANT/COMMENT) from
-- backend/migrations/464_driver_availability_transition_hardening.sql, which
-- restores the NULL-only bind and drops controller_rebound/server_time/replayed.
-- Callers ignore those fields, so rolling back needs no code change.
-- migration-override-ok: replaces 464's transition_driver_availability body (CR-2026-094 controller rebind + replay flag); 457/464 files are not edited
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '20s';

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
    v_is_system boolean;
    v_rebound boolean;
BEGIN
    IF p_request_id IS NULL OR length(btrim(p_request_id)) = 0
       OR p_authenticated_session_id IS NULL OR length(btrim(p_authenticated_session_id)) = 0
       OR p_expected_epoch IS NULL OR p_expected_epoch < 0 THEN
        RAISE EXCEPTION 'request_id, authenticated session, and non-negative expected epoch are required' USING ERRCODE = '22023';
    END IF;
    IF p_action IS NULL OR p_action NOT IN ('go_online','go_offline','stop_requests','pause_policy','pause_unreachable',
                        'pause_idle','pause_misses','displace_controller') THEN
        RAISE EXCEPTION 'unsupported availability action: %', p_action USING ERRCODE = '22023';
    END IF;

    v_is_system := left(p_authenticated_session_id, 7) = 'system:';
    IF v_is_system AND NOT (
        (p_authenticated_session_id = 'system:policy' AND p_action = 'pause_policy') OR
        (p_authenticated_session_id = 'system:contact_gap' AND p_action = 'pause_unreachable') OR
        (p_authenticated_session_id = 'system:readiness' AND p_action = 'pause_idle') OR
        (p_authenticated_session_id = 'system:missed_offers' AND p_action = 'pause_misses') OR
        (p_authenticated_session_id = 'system:finalize' AND p_action IN (
            'stop_requests','pause_policy','pause_unreachable','pause_idle','pause_misses')) OR
        (p_authenticated_session_id = 'system:stale_intent' AND p_action = 'pause_unreachable') OR
        (p_authenticated_session_id = 'system:logout' AND p_action = 'stop_requests')
    ) THEN
        RETURN jsonb_build_object('code','UNAUTHORIZED_SESSION');
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
        -- Callers must not repeat side effects (events, pushes) for a replay.
        RETURN v_saved.result || jsonb_build_object('replayed', true);
    END IF;

    IF NOT COALESCE((SELECT driver_availability_v2_enabled FROM public.settings WHERE id='app_settings'), false) THEN
        RETURN jsonb_build_object('code','AVAILABILITY_V2_DISABLED');
    END IF;

    IF NOT v_is_system AND NOT EXISTS (SELECT 1 FROM public.users u WHERE u.id = v_driver.user_id
                                       AND u.current_session_id = p_authenticated_session_id) THEN
        RETURN jsonb_build_object('code','UNAUTHORIZED_SESSION');
    END IF;
    -- Eligibility is checked by the API before this RPC too, but a trusted
    -- policy status update can race that read. Recheck under the driver lock
    -- so an earlier Go request cannot restore availability after suspension.
    IF p_action = 'go_online' AND COALESCE(v_driver.status,'') <> 'active' THEN
        RETURN jsonb_build_object('code','ELIGIBILITY_BLOCKED','reason_code','ACCOUNT_INELIGIBLE');
    END IF;
    IF p_action = 'pause_policy' AND COALESCE(v_driver.status,'') NOT IN ('rejected','suspended','banned','needs_review') THEN
        RETURN jsonb_build_object('code','POLICY_STATE_CHANGED');
    END IF;
    IF v_driver.online_epoch <> p_expected_epoch THEN
        RETURN jsonb_build_object('code','ONLINE_EPOCH_STALE',
                                  'online_epoch',v_driver.online_epoch::text,
                                  'state_version',v_driver.state_version::text);
    END IF;
    -- The caller is already users.current_session_id, so a controller that
    -- differs from it is stale by definition: Go (like displacement) rebinds,
    -- and the epoch bump below fences the old controller's leases.
    -- OBLIGATION_ACTIVE still refuses Go during a trip or a pending offer.
    -- Every other command stays fenced to the controller.
    IF NOT v_is_system AND v_driver.controller_session_id IS NOT NULL
       AND v_driver.controller_session_id <> p_authenticated_session_id
       AND p_action NOT IN ('go_online','displace_controller','pause_policy') THEN
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
    ELSIF p_action = 'stop_requests' OR p_action IN ('pause_policy','pause_unreachable','pause_idle','pause_misses') THEN
        v_next_accepting := false;
        IF NOT v_has_obligation THEN v_next_online := false; END IF;
    ELSIF p_action = 'displace_controller' THEN
        v_next_accepting := false;
        v_reason := 'controller_displaced';
    END IF;

    -- controller_rebound: an existing, different controller was replaced.
    v_rebound := p_action IN ('go_online','displace_controller')
                 AND v_driver.controller_session_id IS NOT NULL
                 AND v_driver.controller_session_id <> p_authenticated_session_id;
    IF p_action IN ('go_online','displace_controller') THEN
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
           last_contact_at = CASE WHEN NOT v_is_system AND p_action IN ('go_online','go_offline','stop_requests','displace_controller') THEN clock_timestamp() ELSE last_contact_at END,
           ready_until = CASE WHEN p_action = 'go_online' THEN clock_timestamp() + public.driver_ready_window()
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
        'pending_reason',CASE WHEN v_has_trip THEN 'active_trip' WHEN v_has_offer THEN 'offer_obligation' ELSE NULL END,
        'controller_rebound',v_rebound,'server_time',clock_timestamp()
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
    'Backend-only atomic availability transition; system actors use a strict source/action allowlist.';

COMMIT;
NOTIFY pgrst, 'reload schema';
