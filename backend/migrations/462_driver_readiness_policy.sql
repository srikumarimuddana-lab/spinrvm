-- T12 backend: driver readiness policy (backend design 4.4, Addendum X4).
-- Rollback: keep driver_readiness_policy_enabled false (readiness is then
-- never enforced), drop confirm_driver_ready, restore the three readiness
-- seams from 457 (62 min window, 2 min lead, enforced=false), then drop
-- settings.driver_readiness_{policy_enabled,idle_minutes,prompt_minutes}
-- and drivers.readiness_prompt_sent_for.
-- migration-override-ok: replaces the 457 readiness seams (driver_ready_window, driver_readiness_prompt_lead, driver_readiness_enforced); 457-461 bodies are unchanged
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '20s';

-- ── Part A: settings and driver columns ─────────────────────────────
-- The 5-minute contact gap stays a constant in 457 (deliberate).
ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS driver_readiness_policy_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS driver_readiness_idle_minutes integer NOT NULL DEFAULT 60,
    ADD COLUMN IF NOT EXISTS driver_readiness_prompt_minutes integer NOT NULL DEFAULT 2;
COMMENT ON COLUMN public.settings.driver_readiness_policy_enabled IS
    'Dark gate: with driver_availability_v2_enabled, idle drivers must confirm readiness or are paused.';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'settings_driver_readiness_idle_minutes_check'
                      AND conrelid = 'public.settings'::regclass) THEN
        ALTER TABLE public.settings ADD CONSTRAINT settings_driver_readiness_idle_minutes_check
            CHECK (driver_readiness_idle_minutes BETWEEN 15 AND 240) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'settings_driver_readiness_prompt_minutes_check'
                      AND conrelid = 'public.settings'::regclass) THEN
        ALTER TABLE public.settings ADD CONSTRAINT settings_driver_readiness_prompt_minutes_check
            CHECK (driver_readiness_prompt_minutes BETWEEN 1 AND 10) NOT VALID;
    END IF;
END;
$$;

-- Claim flag: the ready_until value that has already been prompted.
ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS readiness_prompt_sent_for timestamptz;

-- ── Part A: readiness seams (bodies of 457-461 unchanged) ───────────
CREATE OR REPLACE FUNCTION public.driver_ready_window()
RETURNS interval
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
    SELECT COALESCE(
        (SELECT make_interval(mins => s.driver_readiness_idle_minutes + s.driver_readiness_prompt_minutes)
           FROM public.settings s WHERE s.id = 'app_settings'),
        interval '62 minutes')
$$;
REVOKE ALL ON FUNCTION public.driver_ready_window() FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.driver_readiness_prompt_lead()
RETURNS interval
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
    SELECT COALESCE(
        (SELECT make_interval(mins => s.driver_readiness_prompt_minutes)
           FROM public.settings s WHERE s.id = 'app_settings'),
        interval '2 minutes')
$$;
REVOKE ALL ON FUNCTION public.driver_readiness_prompt_lead() FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.driver_readiness_enforced()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
    SELECT COALESCE(
        (SELECT s.driver_availability_v2_enabled AND s.driver_readiness_policy_enabled
           FROM public.settings s WHERE s.id = 'app_settings'),
        false)
$$;
REVOKE ALL ON FUNCTION public.driver_readiness_enforced() FROM PUBLIC, anon, authenticated, service_role;

-- ── Part A: confirm_driver_ready ────────────────────────────────────
-- Refreshes ready_until only; never bumps the epoch or resets the miss
-- streak. Check order is Addendum X4's. A late still_ready confirmation
-- cannot revive readiness: it pauses the idle driver (READINESS_EXPIRED).
CREATE OR REPLACE FUNCTION public.confirm_driver_ready(
    p_driver_id text,
    p_expected_epoch bigint,
    p_authenticated_session_id text,
    p_request_id text,
    p_reason text DEFAULT 'still_ready'
) RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
SET lock_timeout = '2s'
AS $$
DECLARE
    v_driver public.drivers%ROWTYPE;
    v_saved public.driver_availability_requests%ROWTYPE;
    v_action text;
    v_epoch_key bigint;
    v_current_session text;
    v_now timestamptz;
    v_idle boolean;
    v_pause jsonb;
    v_result jsonb;
BEGIN
    IF p_reason IS NULL OR p_reason NOT IN ('still_ready','trip_completed') THEN
        RAISE EXCEPTION 'unsupported readiness reason: %', p_reason USING ERRCODE = '22023';
    END IF;
    IF p_driver_id IS NULL OR p_request_id IS NULL OR length(btrim(p_request_id)) = 0
       OR length(p_request_id) > 128
       OR p_authenticated_session_id IS NULL OR length(btrim(p_authenticated_session_id)) = 0
       OR (p_expected_epoch IS NULL AND p_reason <> 'trip_completed')
       OR p_expected_epoch < 0 THEN
        RAISE EXCEPTION 'driver, request_id, session and (for still_ready) epoch are required'
            USING ERRCODE = '22023';
    END IF;
    v_action := 'confirm_ready:' || p_reason;
    -- driver_availability_requests.expected_epoch is NOT NULL; -1 marks
    -- an epoch-free trip_completed confirmation.
    v_epoch_key := COALESCE(p_expected_epoch, -1);

    SELECT * INTO v_driver FROM public.drivers WHERE id = p_driver_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('code','DRIVER_NOT_FOUND'); END IF;

    SELECT * INTO v_saved FROM public.driver_availability_requests
     WHERE driver_id = p_driver_id AND request_id = p_request_id;
    IF FOUND THEN
        IF v_saved.expected_epoch <> v_epoch_key
           OR v_saved.authenticated_session_id <> p_authenticated_session_id
           OR v_saved.action <> v_action THEN
            RETURN jsonb_build_object('code','IDEMPOTENCY_KEY_CONFLICT');
        END IF;
        RETURN v_saved.result || jsonb_build_object('replayed', true);
    END IF;

    IF NOT COALESCE((SELECT driver_availability_v2_enabled FROM public.settings WHERE id='app_settings'), false) THEN
        RETURN jsonb_build_object('code','AVAILABILITY_V2_DISABLED');
    END IF;

    -- 1. Only the current, non-system session may confirm.
    SELECT u.current_session_id INTO v_current_session FROM public.users u WHERE u.id = v_driver.user_id;
    IF p_authenticated_session_id LIKE 'system:%'
       OR v_current_session IS DISTINCT FROM p_authenticated_session_id THEN
        RETURN jsonb_build_object('code','SESSION_SUPERSEDED','online_epoch',v_driver.online_epoch::text);
    END IF;
    -- 2. Offline.
    IF NOT v_driver.is_online THEN
        RETURN jsonb_build_object('code','DRIVER_OFFLINE','online_epoch',v_driver.online_epoch::text);
    END IF;
    -- 3. A stale controller: the newest login must press Go (C7), not confirm.
    IF v_driver.controller_session_id IS DISTINCT FROM p_authenticated_session_id THEN
        RETURN jsonb_build_object('code','REQUESTS_PAUSED','reason_code','CONTROLLER_SESSION_MISMATCH',
                                  'online_epoch',v_driver.online_epoch::text);
    END IF;
    -- 4. Epoch (trip_completed may omit it).
    IF p_expected_epoch IS NOT NULL AND v_driver.online_epoch <> p_expected_epoch THEN
        RETURN jsonb_build_object('code','ONLINE_EPOCH_STALE','online_epoch',v_driver.online_epoch::text,
                                  'state_version',v_driver.state_version::text);
    END IF;
    -- 5. Not accepting requests.
    IF NOT v_driver.accepting_requests THEN
        RETURN jsonb_build_object('code','REQUESTS_PAUSED','online_epoch',v_driver.online_epoch::text);
    END IF;

    v_now := clock_timestamp();
    -- 6. A late still_ready on an idle driver pauses instead of refreshing.
    v_idle := v_driver.is_available AND NOT EXISTS (
        SELECT 1 FROM public.rides r
         WHERE r.driver_id = p_driver_id
           AND r.status IN ('driver_assigned','driver_accepted','driver_arrived','in_progress'));
    IF p_reason = 'still_ready' AND public.driver_readiness_enforced() AND v_idle
       AND (v_driver.ready_until IS NULL OR v_driver.ready_until <= v_now) THEN
        v_pause := public.transition_driver_availability(
            p_driver_id, v_driver.online_epoch, p_authenticated_session_id,
            'pause_idle', 'readiness-expired:' || v_driver.online_epoch::text);
        IF v_pause->>'code' <> 'OK' THEN
            RETURN jsonb_build_object('code','READINESS_RECONCILIATION_FAILED',
                                      'online_epoch',v_driver.online_epoch::text);
        END IF;
        v_result := jsonb_build_object(
            'code','READINESS_EXPIRED','reason_code','READINESS_EXPIRED',
            'online_epoch',v_pause->>'online_epoch','state_version',v_pause->>'state_version',
            'is_online',(v_pause->>'is_online')::boolean,'availability_reason',v_pause->>'availability_reason',
            'server_time',v_pause->'server_time');
    ELSE
        -- 7. Refresh.
        UPDATE public.drivers
           SET ready_until = v_now + public.driver_ready_window(),
               readiness_prompt_sent_for = NULL,
               state_version = state_version + 1
         WHERE id = p_driver_id
         RETURNING * INTO v_driver;
        v_result := jsonb_build_object(
            'code','OK','reason',p_reason,'ready_until',v_driver.ready_until,
            'online_epoch',v_driver.online_epoch::text,'state_version',v_driver.state_version::text,
            'server_time',v_now);
    END IF;

    INSERT INTO public.driver_availability_requests
        (request_id,driver_id,expected_epoch,authenticated_session_id,action,result)
    VALUES (p_request_id,p_driver_id,v_epoch_key,p_authenticated_session_id,v_action,v_result);
    RETURN v_result;
END;
$$;
REVOKE ALL ON FUNCTION public.confirm_driver_ready(text,bigint,text,text,text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.confirm_driver_ready(text,bigint,text,text,text) TO service_role;
COMMENT ON FUNCTION public.confirm_driver_ready(text,bigint,text,text,text) IS
    'Backend-only readiness confirmation (still_ready / trip_completed); refreshes ready_until without bumping the epoch.';

COMMIT;
NOTIFY pgrst, 'reload schema';
