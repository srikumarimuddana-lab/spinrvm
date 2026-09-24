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

COMMIT;
NOTIFY pgrst, 'reload schema';
