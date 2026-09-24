-- T6: offer delivery receipts and fair missed-offer counting.
-- Rollback: DROP FUNCTION public.record_offer_receipt(uuid,uuid,text,text,text,text,text,integer);
--           DROP TABLE public.driver_offer_receipts;
--           then re-apply 460 to restore offer_expiry_counts_as_miss and 458
--           to restore get_driver_availability_snapshot.
-- migration-override-ok: replaces offer_expiry_counts_as_miss (460) and get_driver_availability_snapshot (458)
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '20s';

-- ── Part A: receipts table ──────────────────────────────────────────
-- Append-only evidence that a v2 offer reached the addressed session.
-- One row per (offer, session, event); duplicates across channels collapse.
CREATE TABLE IF NOT EXISTS public.driver_offer_receipts (
    offer_id uuid NOT NULL REFERENCES public.ride_offers(id) ON DELETE CASCADE,
    session_id text NOT NULL,
    event text NOT NULL CHECK (event IN ('received','presented')),
    driver_id text NOT NULL REFERENCES public.drivers(id) ON DELETE CASCADE,
    channel text NOT NULL CHECK (channel IN ('ws','push','stored','android_auto')),
    app_state text NOT NULL CHECK (app_state IN ('active','background','inactive','unknown')),
    remaining_ms integer CHECK (remaining_ms BETWEEN -3600000 AND 3600000),
    received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (offer_id, session_id, event)
);
CREATE INDEX IF NOT EXISTS driver_offer_receipts_driver_received_idx
    ON public.driver_offer_receipts (driver_id, received_at DESC);

ALTER TABLE public.driver_offer_receipts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.driver_offer_receipts FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.driver_offer_receipts TO authenticated;
GRANT SELECT, INSERT ON public.driver_offer_receipts TO service_role;

DROP POLICY IF EXISTS driver_offer_receipts_driver_select ON public.driver_offer_receipts;
CREATE POLICY driver_offer_receipts_driver_select ON public.driver_offer_receipts
    FOR SELECT TO authenticated
    USING (driver_id IN (SELECT d.id FROM public.drivers d WHERE d.user_id = auth.uid()::text));

-- ── Part A: record_offer_receipt RPC ────────────────────────────────
-- Takes no row locks: a plain insert with ON CONFLICT DO NOTHING.
CREATE OR REPLACE FUNCTION public.record_offer_receipt(
    p_offer_id uuid,
    p_claim_id uuid,
    p_user_id text,
    p_session_id text,
    p_event text,
    p_channel text,
    p_app_state text,
    p_remaining_ms integer
) RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
SET lock_timeout = '2s'
AS $$
DECLARE
    v_offer public.ride_offers%ROWTYPE;
    v_driver_user_id text;
    v_current_session text;
    v_now timestamptz;
    v_rows integer;
BEGIN
    IF p_offer_id IS NULL OR p_user_id IS NULL OR length(btrim(p_user_id)) = 0
       OR p_session_id IS NULL OR length(btrim(p_session_id)) = 0
       OR p_event IS NULL OR p_event NOT IN ('received','presented')
       OR p_channel IS NULL OR p_channel NOT IN ('ws','push','stored','android_auto')
       OR p_app_state IS NULL OR p_app_state NOT IN ('active','background','inactive','unknown')
       OR (p_remaining_ms IS NOT NULL AND p_remaining_ms NOT BETWEEN -3600000 AND 3600000)
       OR (p_event = 'presented' AND p_app_state <> 'active') THEN
        RETURN jsonb_build_object('code','INVALID_RECEIPT');
    END IF;
    IF p_session_id LIKE 'system:%' THEN
        RETURN jsonb_build_object('code','SESSION_SUPERSEDED');
    END IF;

    SELECT ro.* INTO v_offer FROM public.ride_offers ro WHERE ro.id = p_offer_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('code','OFFER_NOT_FOUND');
    END IF;
    SELECT d.user_id INTO v_driver_user_id FROM public.drivers d WHERE d.id = v_offer.driver_id;
    -- Wrong driver and legacy (non-v2) offers are indistinguishable from absent.
    IF v_driver_user_id IS DISTINCT FROM p_user_id OR v_offer.online_epoch IS NULL THEN
        RETURN jsonb_build_object('code','OFFER_NOT_FOUND');
    END IF;
    IF v_offer.claim_id IS DISTINCT FROM p_claim_id THEN
        RETURN jsonb_build_object('code','CLAIM_MISMATCH');
    END IF;
    -- SESSION_SUPERSEDED only ever reaches an old, non-current session
    -- (Addendum A3). The current session receiving an offer addressed to an
    -- earlier session simply has no offer to acknowledge.
    SELECT u.current_session_id INTO v_current_session FROM public.users u WHERE u.id = p_user_id;
    IF v_current_session IS DISTINCT FROM p_session_id THEN
        RETURN jsonb_build_object('code','SESSION_SUPERSEDED');
    END IF;
    IF v_offer.controller_session_id IS NULL OR v_offer.controller_session_id <> p_session_id THEN
        RETURN jsonb_build_object('code','OFFER_NOT_FOUND','reason_code','OFFER_SESSION_ENDED');
    END IF;

    INSERT INTO public.driver_offer_receipts
        (offer_id, session_id, event, driver_id, channel, app_state, remaining_ms)
    VALUES (p_offer_id, p_session_id, p_event, v_offer.driver_id, p_channel, p_app_state, p_remaining_ms)
    ON CONFLICT (offer_id, session_id, event) DO NOTHING;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    v_now := clock_timestamp();

    RETURN jsonb_build_object(
        'code','OK',
        'recorded', v_rows > 0,
        'offer_id', v_offer.id,
        'offer_status', v_offer.status,
        'offered_at', v_offer.offered_at,
        'expires_at', v_offer.expires_at,
        'server_time', v_now,
        'late', v_offer.expires_at IS NOT NULL AND v_now > v_offer.expires_at
    );
END;
$$;
REVOKE ALL ON FUNCTION public.record_offer_receipt(uuid,uuid,text,text,text,text,text,integer)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_offer_receipt(uuid,uuid,text,text,text,text,text,integer)
    TO service_role;
COMMENT ON FUNCTION public.record_offer_receipt(uuid,uuid,text,text,text,text,text,integer) IS
    'Backend-only, lock-free, idempotent delivery receipt for a v2 offer addressed to the caller session.';

-- ── Part B: receipt-based missed-offer seam ─────────────────────────
-- Replaces 460's legacy seam. An expiry counts as a miss only when the
-- driver's epoch and controller are unchanged AND the addressed session
-- recorded a foreground 'presented' receipt no later than expires_at.
-- No receipt means delivery unknown, never a miss.
CREATE OR REPLACE FUNCTION public.offer_expiry_counts_as_miss(
    p_offer public.ride_offers,
    p_driver public.drivers
) RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
    SELECT p_offer.online_epoch IS NOT NULL
       AND p_offer.controller_session_id IS NOT NULL
       AND p_driver.online_epoch = p_offer.online_epoch
       AND p_driver.controller_session_id IS NOT DISTINCT FROM p_offer.controller_session_id
       AND EXISTS (
           SELECT 1 FROM public.driver_offer_receipts r
            WHERE r.offer_id = p_offer.id
              AND r.session_id = p_offer.controller_session_id
              AND r.event = 'presented'
              AND r.app_state = 'active'
              AND p_offer.expires_at IS NOT NULL
              AND r.received_at <= p_offer.expires_at
       )
$$;
REVOKE ALL ON FUNCTION public.offer_expiry_counts_as_miss(public.ride_offers, public.drivers)
    FROM PUBLIC, anon, authenticated, service_role;

-- ── Part B: snapshot adds the v2 offer envelope fields ──────────────
-- Same body as 458 plus offer_id (alias of id), claim_id and online_epoch
-- (decimal text) on live_offer; readiness fields unchanged.
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
        SELECT ro.id, ro.id AS offer_id, ro.ride_id, ro.offered_at, ro.expires_at,
               ro.claim_id, ro.online_epoch::text AS online_epoch,
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
