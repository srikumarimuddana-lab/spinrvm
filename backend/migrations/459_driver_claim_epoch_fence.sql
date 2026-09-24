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

COMMIT;

NOTIFY pgrst, 'reload schema';
