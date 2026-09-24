-- T12 backend: driver readiness policy (backend design 4.4, Addendum X4).
-- Rollback: keep driver_readiness_policy_enabled false (readiness is then
-- never enforced), drop confirm_driver_ready, reconcile_driver_readiness,
-- claim_readiness_prompt, list_driver_availability_reconcile_candidates and
-- the two drivers_availability_*_due_idx indexes, restore the three readiness
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

-- ── Part B: reconciler candidates ───────────────────────────────────
-- Uses the DB clock. Each list is capped at p_limit. Rows carry
-- {driver_id, user_id, online_epoch, ready_until} (Addendum X9).
CREATE OR REPLACE FUNCTION public.list_driver_availability_reconcile_candidates(p_limit integer)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_now timestamptz := clock_timestamp();
    v_limit integer;
    v_enforced boolean := public.driver_readiness_enforced();
    v_lead interval := public.driver_readiness_prompt_lead();
BEGIN
    IF p_limit IS NULL OR p_limit < 1 OR p_limit > 500 THEN
        RAISE EXCEPTION 'p_limit must be 1..500' USING ERRCODE = '22023';
    END IF;
    v_limit := p_limit;
    RETURN jsonb_build_object(
        'server_time', v_now,
        'readiness_enforced', v_enforced,
        'contact_gap', COALESCE((
            SELECT jsonb_agg(jsonb_build_object('driver_id', c.id, 'user_id', c.user_id,
                                                'online_epoch', c.online_epoch::text,
                                                'ready_until', c.ready_until))
              FROM (SELECT d.id, d.user_id, d.online_epoch, d.ready_until
                      FROM public.drivers d
                     WHERE d.is_online AND d.controller_session_id IS NOT NULL
                       AND d.last_contact_at < v_now - interval '5 minutes'
                     ORDER BY d.last_contact_at LIMIT v_limit) c), '[]'::jsonb),
        'readiness_due', CASE WHEN v_enforced THEN COALESCE((
            SELECT jsonb_agg(jsonb_build_object('driver_id', c.id, 'user_id', c.user_id,
                                                'online_epoch', c.online_epoch::text,
                                                'ready_until', c.ready_until))
              FROM (SELECT d.id, d.user_id, d.online_epoch, d.ready_until
                      FROM public.drivers d
                     WHERE d.is_online AND d.accepting_requests AND d.is_available
                       AND d.ready_until <= v_now
                     ORDER BY d.ready_until LIMIT v_limit) c), '[]'::jsonb) ELSE '[]'::jsonb END,
        'prompt_due', CASE WHEN v_enforced THEN COALESCE((
            SELECT jsonb_agg(jsonb_build_object('driver_id', c.id, 'user_id', c.user_id,
                                                'online_epoch', c.online_epoch::text,
                                                'ready_until', c.ready_until))
              FROM (SELECT d.id, d.user_id, d.online_epoch, d.ready_until
                      FROM public.drivers d
                     WHERE d.is_online AND d.accepting_requests AND d.is_available
                       AND d.ready_until > v_now AND d.ready_until <= v_now + v_lead
                       AND d.readiness_prompt_sent_for IS DISTINCT FROM d.ready_until
                     ORDER BY d.ready_until LIMIT v_limit) c), '[]'::jsonb) ELSE '[]'::jsonb END
    );
END;
$$;
REVOKE ALL ON FUNCTION public.list_driver_availability_reconcile_candidates(integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.list_driver_availability_reconcile_candidates(integer) TO service_role;

-- ── Part B: reconcile one driver ────────────────────────────────────
-- SKIP LOCKED: a busy driver is left to the next tick (BUSY). The
-- predicate is re-checked on clock_timestamp() under the lock, and T1
-- keeps any trip and its insurance period.
CREATE OR REPLACE FUNCTION public.reconcile_driver_readiness(
    p_driver_id text,
    p_expected_epoch bigint,
    p_kind text,
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
    v_now timestamptz;
    v_due boolean;
    v_result jsonb;
BEGIN
    IF p_kind IS NULL OR p_kind NOT IN ('contact_gap','readiness') THEN
        RAISE EXCEPTION 'unsupported reconcile kind: %', p_kind USING ERRCODE = '22023';
    END IF;
    IF p_driver_id IS NULL OR p_expected_epoch IS NULL OR p_expected_epoch < 0
       OR p_request_id IS NULL OR length(btrim(p_request_id)) = 0 OR length(p_request_id) > 128 THEN
        RAISE EXCEPTION 'driver, epoch and request_id are required' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_driver FROM public.drivers WHERE id = p_driver_id FOR UPDATE SKIP LOCKED;
    IF NOT FOUND THEN
        IF EXISTS (SELECT 1 FROM public.drivers WHERE id = p_driver_id) THEN
            RETURN jsonb_build_object('code','BUSY');
        END IF;
        RETURN jsonb_build_object('code','DRIVER_NOT_FOUND');
    END IF;
    IF NOT COALESCE((SELECT driver_availability_v2_enabled FROM public.settings WHERE id='app_settings'), false) THEN
        RETURN jsonb_build_object('code','AVAILABILITY_V2_DISABLED');
    END IF;
    IF v_driver.online_epoch <> p_expected_epoch THEN
        RETURN jsonb_build_object('code','ONLINE_EPOCH_STALE','online_epoch',v_driver.online_epoch::text);
    END IF;

    v_now := clock_timestamp();
    IF p_kind = 'contact_gap' THEN
        v_due := v_driver.is_online AND v_driver.controller_session_id IS NOT NULL
                 AND v_driver.last_contact_at < v_now - interval '5 minutes';
    ELSE
        v_due := public.driver_readiness_enforced()
                 AND v_driver.is_online AND v_driver.accepting_requests AND v_driver.is_available
                 AND v_driver.ready_until <= v_now;
    END IF;
    IF NOT v_due THEN
        RETURN jsonb_build_object('code','NOT_DUE','online_epoch',v_driver.online_epoch::text);
    END IF;

    v_result := public.transition_driver_availability(
        p_driver_id, p_expected_epoch,
        CASE WHEN p_kind = 'contact_gap' THEN 'system:contact_gap' ELSE 'system:readiness' END,
        CASE WHEN p_kind = 'contact_gap' THEN 'pause_unreachable' ELSE 'pause_idle' END,
        p_request_id);
    RETURN v_result || jsonb_build_object('kind', p_kind, 'user_id', v_driver.user_id);
END;
$$;
REVOKE ALL ON FUNCTION public.reconcile_driver_readiness(text,bigint,text,text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reconcile_driver_readiness(text,bigint,text,text) TO service_role;

-- ── Part B: claim a readiness prompt (at most once per ready_until) ─
CREATE OR REPLACE FUNCTION public.claim_readiness_prompt(
    p_driver_id text,
    p_expected_epoch bigint,
    p_ready_until timestamptz
) RETURNS text
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
SET lock_timeout = '2s'
AS $$
    UPDATE public.drivers
       SET readiness_prompt_sent_for = p_ready_until
     WHERE id = p_driver_id
       AND online_epoch = p_expected_epoch
       AND ready_until = p_ready_until
       AND readiness_prompt_sent_for IS DISTINCT FROM p_ready_until
       AND is_online AND accepting_requests AND is_available
    RETURNING user_id
$$;
REVOKE ALL ON FUNCTION public.claim_readiness_prompt(text,bigint,timestamptz) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_readiness_prompt(text,bigint,timestamptz) TO service_role;

COMMIT;

-- Partial indexes for the reconciler lists; CONCURRENTLY runs outside the
-- transaction so the drivers table is never write-locked.
CREATE INDEX CONCURRENTLY IF NOT EXISTS drivers_availability_contact_due_idx
    ON public.drivers (last_contact_at)
    WHERE is_online AND controller_session_id IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS drivers_availability_ready_due_idx
    ON public.drivers (ready_until)
    WHERE is_online AND accepting_requests AND is_available;

NOTIFY pgrst, 'reload schema';
