-- F2 fixes for driver availability v2 (457 has been applied to prod).
-- Also creates the three readiness seam functions that 457's earlier
-- branch edits added but which were reverted when 457 was restored to
-- its merged state.  These seams must exist before the rewritten
-- renew_driver_presence and snapshot below reference them.

-- ── Readiness seams (stubs until 462 replaces the bodies) ──────────
CREATE OR REPLACE FUNCTION public.driver_ready_window()
RETURNS interval
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$ SELECT interval '62 minutes' $$;
REVOKE ALL ON FUNCTION public.driver_ready_window() FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.driver_readiness_prompt_lead()
RETURNS interval
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$ SELECT interval '2 minutes' $$;
REVOKE ALL ON FUNCTION public.driver_readiness_prompt_lead() FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.driver_readiness_enforced()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$ SELECT false $$;
REVOKE ALL ON FUNCTION public.driver_readiness_enforced() FROM PUBLIC, anon, authenticated, service_role;
-- F2-7: reorder renew_driver_presence checks to fix the F2(e) new-login loop.
-- F2-5: add readiness-expired branch to renew_driver_presence.
-- F2-7: add current_session_id to the snapshot for server-side session logic.
-- Rollback: DROP FUNCTION renew_driver_presence(text,text,bigint,timestamptz);
--           DROP FUNCTION get_driver_availability_snapshot(text);
--           Then re-apply 457 to restore the originals.
-- migration-override-ok: replaces functions from 457

BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '20s';

-- F2-7 + F2-5: replace renew_driver_presence with corrected check order
-- and readiness-expired branch.
-- Check order becomes: flag, current session, epoch, offline, controller,
-- then contact gap, then readiness.
CREATE OR REPLACE FUNCTION public.renew_driver_presence(
    p_driver_id text,
    p_authenticated_session_id text,
    p_online_epoch bigint,
    p_location_captured_at timestamptz DEFAULT NULL
) RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_driver public.drivers%ROWTYPE;
    v_now timestamptz;
    v_location_valid_until timestamptz;
    v_location_error text;
    v_gap_result jsonb;
    v_readiness_result jsonb;
BEGIN
    IF p_driver_id IS NULL OR p_authenticated_session_id IS NULL
       OR length(btrim(p_authenticated_session_id)) = 0
       OR p_online_epoch IS NULL OR p_online_epoch < 0 THEN
        RAISE EXCEPTION 'driver, authenticated session, and non-negative epoch are required' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_driver FROM public.drivers WHERE id=p_driver_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('status','offline','code','DRIVER_NOT_FOUND'); END IF;
    -- Start all contact and lease calculations after serialization on the
    -- authoritative driver row, so lock wait time cannot extend a deadline.
    v_now := clock_timestamp();

    -- 1. Flag check
    IF NOT COALESCE((SELECT driver_availability_v2_enabled FROM public.settings WHERE id='app_settings'),false) THEN
        RETURN jsonb_build_object('status','unavailable','code','AVAILABILITY_V2_DISABLED');
    END IF;

    -- 2. Current session check (before controller — a mismatch here means the
    --    caller has been superseded by a newer login)
    IF NOT EXISTS (SELECT 1 FROM public.users u WHERE u.id=v_driver.user_id
                   AND u.current_session_id=p_authenticated_session_id) THEN
        RETURN jsonb_build_object('status','stale_epoch','code','UNAUTHORIZED_SESSION',
                                  'online_epoch',v_driver.online_epoch::text);
    END IF;

    -- 3. Epoch check
    IF v_driver.online_epoch <> p_online_epoch THEN
        RETURN jsonb_build_object('status','stale_epoch','code','ONLINE_EPOCH_STALE',
                                  'online_epoch',v_driver.online_epoch::text);
    END IF;

    -- 4. Offline check (moved BEFORE controller — F2(e) fix: the newest login
    --    must not be told SESSION_SUPERSEDED when the driver is simply offline)
    IF NOT v_driver.is_online THEN
        RETURN jsonb_build_object('status','offline','code','OFFLINE',
                                  'online_epoch',v_driver.online_epoch::text);
    END IF;

    -- 5. Controller check (only reached when online — a mismatch here means
    --    the stored controller is stale, not the caller)
    IF v_driver.controller_session_id IS DISTINCT FROM p_authenticated_session_id THEN
        RETURN jsonb_build_object('status','stale_epoch','code','CONTROLLER_SESSION_MISMATCH',
                                  'online_epoch',v_driver.online_epoch::text);
    END IF;

    -- Enforce the long-gap fence on the request path too; a delayed worker is
    -- not allowed to leave an old controller renewable after five minutes.
    IF v_driver.last_contact_at IS NULL OR v_driver.last_contact_at < v_now - interval '5 minutes' THEN
        v_gap_result := public.transition_driver_availability(
            p_driver_id, p_online_epoch, p_authenticated_session_id,
            'pause_unreachable', 'presence-gap:' || p_online_epoch::text
        );
        IF v_gap_result->>'code' <> 'OK' THEN
            RETURN jsonb_build_object('status','unavailable','code','CONTACT_RECONCILIATION_FAILED',
                                      'online_epoch',v_driver.online_epoch::text);
        END IF;
        -- A current authenticated contact during an active obligation must
        -- remain upload-capable after the fence moves. It still cannot accept
        -- new requests; idle drivers remain offline and must explicitly Go.
        IF COALESCE((v_gap_result->>'has_trip')::boolean,false)
           OR COALESCE((v_gap_result->>'has_pending_offer')::boolean,false) THEN
            v_now := clock_timestamp();
            UPDATE public.drivers SET last_contact_at=v_now WHERE id=p_driver_id;
        END IF;
        RETURN jsonb_build_object('status','stale_epoch','code','CONTACT_GAP',
                                  'online_epoch',v_gap_result->>'online_epoch',
                                  'state_version',v_gap_result->>'state_version');
    END IF;

    -- F2-5: readiness-expired branch. When readiness is enforced and the
    -- driver is idle (accepting, available) and ready_until has passed, pause
    -- them as idle and report the new epoch.
    IF public.driver_readiness_enforced()
       AND v_driver.accepting_requests AND v_driver.is_available
       AND (v_driver.ready_until IS NULL OR v_driver.ready_until <= v_now) THEN
        v_readiness_result := public.transition_driver_availability(
            p_driver_id, p_online_epoch, p_authenticated_session_id,
            'pause_idle', 'readiness-expired:' || p_online_epoch::text
        );
        IF v_readiness_result->>'code' = 'OK' THEN
            RETURN jsonb_build_object('status','stale_epoch','code','READINESS_EXPIRED',
                                      'online_epoch',v_readiness_result->>'online_epoch',
                                      'state_version',v_readiness_result->>'state_version');
        ELSE
            RETURN jsonb_build_object('status','unavailable','code','READINESS_RECONCILIATION_FAILED',
                                      'online_epoch',v_driver.online_epoch::text);
        END IF;
    END IF;

    IF p_location_captured_at IS NOT NULL THEN
        IF p_location_captured_at < v_now - interval '60 seconds'
           OR p_location_captured_at > v_now + interval '5 seconds' THEN
            v_location_error := 'INVALID_LOCATION_TIME';
        ELSE
            -- Captured time, not arrival time, anchors the existing 60s age cap.
            v_location_valid_until := p_location_captured_at + interval '60 seconds';
        END IF;
    END IF;

    -- Keep heartbeat writes bounded to one durable update per 30 seconds. This
    -- timestamp is dedicated contact evidence; generic updated_at is untouched.
    UPDATE public.drivers
       SET last_contact_at = v_now
     WHERE id=p_driver_id
       AND (last_contact_at IS NULL OR last_contact_at <= v_now - interval '30 seconds');

    RETURN jsonb_build_object(
        'status','renewed',
        'code',COALESCE(v_location_error,'renewed'),
        'driver_id',p_driver_id,
        'online_epoch',v_driver.online_epoch::text,
        'controller_session_id',v_driver.controller_session_id,
        'contact_received_at',v_now,
        'contact_valid_until',v_now + interval '90 seconds',
        'location_valid_until',v_location_valid_until
    );
END;
$$;
REVOKE ALL ON FUNCTION public.renew_driver_presence(text,text,bigint,timestamptz) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.renew_driver_presence(text,text,bigint,timestamptz) TO service_role;
COMMENT ON FUNCTION public.renew_driver_presence(text,text,bigint,timestamptz) IS
    'Backend-only contact renewal fenced by token session and online epoch; GPS deadline is capture-time based and separately nullable.';

-- F2-7: replace snapshot to add current_session_id for server-side session logic.
-- The key is NOT sent to the client; the Python service uses it internally.
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
    ), current_session AS MATERIALIZED (
        SELECT u.current_session_id
        FROM public.users u WHERE u.id = p_user_id
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
        'readiness_enforced', public.driver_readiness_enforced(),
        'readiness_prompt_at', (SELECT d.ready_until - public.driver_readiness_prompt_lead() FROM driver_row d),
        'current_session_id', (SELECT cs.current_session_id FROM current_session cs),
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
NOTIFY pgrst, 'reload schema';
