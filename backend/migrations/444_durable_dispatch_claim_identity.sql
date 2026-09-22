-- 444: Durable claim identity and transactional orphan recovery.
-- Rollback: set settings.dispatch_claim_identity_enabled=false to stop new
-- UUID-bound claims; leave columns and v2 RPCs in place until all current
-- claim IDs have drained. Do not drop identity columns or rewrite audit rows.
-- The existing dispatch_direct_pool_enabled remains an independent gate.
-- Existing 442/443 files are immutable; this migration supersedes their
-- timestamp-based ownership check with claim identity.
-- migration-override-ok: intentionally replaces the release RPC body created
-- by 442 only under a new v2 name, and adds dispatch_claim_batch_v2 alongside
-- 403's legacy function. The v2 signatures are intentional new RPCs.

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS dispatch_claim_identity_enabled BOOLEAN NOT NULL DEFAULT FALSE;
COMMENT ON COLUMN public.settings.dispatch_claim_identity_enabled IS
    'Dark-launch switch for UUID-bound driver dispatch claims. Enable only after all backend pods run the v2-aware release/reaper code.';

ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS availability_claim_id uuid;
ALTER TABLE public.ride_offers
    ADD COLUMN IF NOT EXISTS claim_id uuid;
ALTER TABLE public.driver_insurance_periods
    ADD COLUMN IF NOT EXISTS claim_id uuid;

COMMENT ON COLUMN public.drivers.availability_claim_id IS
    'Identity of the current v2 availability claim; NULL for available and legacy claims. Set with DB clock.';
COMMENT ON COLUMN public.ride_offers.claim_id IS
    'Driver availability claim identity that created this offer; immutable association for safe release.';
COMMENT ON COLUMN public.driver_insurance_periods.claim_id IS
    'Availability claim identity associated with this Period-2 interval; historical rows may be NULL.';

-- claim_id stays nullable for rolling deploy compatibility. New writers pass
-- their claim UUID explicitly; old writers remain operational, and their
-- NULL identity remains on the guarded legacy release/reaper path until the
-- UUID-bound feature is enabled after every backend pod is upgraded.

-- Period-2 carries the exact identity from its pending/accepted offer. The
-- generic transition RPC remains compatible with old callers; no identity is
-- fabricated from timestamps or the driver's latest claim.
CREATE OR REPLACE FUNCTION public.stamp_period_claim_identity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.period = 2 AND NEW.claim_id IS NULL AND NEW.ride_id IS NOT NULL THEN
        SELECT ro.claim_id INTO NEW.claim_id
          FROM public.ride_offers AS ro
         WHERE ro.driver_id = NEW.driver_id
           AND ro.ride_id = NEW.ride_id
           AND ro.status IN ('pending', 'accepted')
         ORDER BY ro.offered_at DESC
         LIMIT 1;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS driver_insurance_periods_stamp_claim_id ON public.driver_insurance_periods;
CREATE TRIGGER driver_insurance_periods_stamp_claim_id
    BEFORE INSERT ON public.driver_insurance_periods
    FOR EACH ROW EXECUTE FUNCTION public.stamp_period_claim_identity();

-- V2 offers are bound to their producer's claim while holding the driver
-- row lock. This closes the PostgREST gap where a delayed insert could arrive
-- after claim A was released and claim B acquired. NULL remains allowed for
-- legacy writers during the dark rollout.
CREATE OR REPLACE FUNCTION public.validate_offer_claim_identity_v2()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_available boolean;
    v_claim_id uuid;
BEGIN
    IF NEW.status <> 'pending' OR NEW.claim_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT is_available, availability_claim_id INTO v_available, v_claim_id
      FROM public.drivers WHERE id = NEW.driver_id FOR UPDATE;
    IF NOT FOUND OR COALESCE(v_available, true) OR v_claim_id IS DISTINCT FROM NEW.claim_id THEN
        RAISE EXCEPTION 'pending offer claim identity is stale (driver_id=% ride_id=%)', NEW.driver_id, NEW.ride_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS ride_offers_0_validate_claim_identity ON public.ride_offers;
CREATE TRIGGER ride_offers_0_validate_claim_identity
    BEFORE INSERT OR UPDATE OF status, claim_id ON public.ride_offers
    FOR EACH ROW EXECUTE FUNCTION public.validate_offer_claim_identity_v2();

CREATE INDEX IF NOT EXISTS ride_offers_claim_id_idx ON public.ride_offers (claim_id);
CREATE INDEX IF NOT EXISTS driver_insurance_periods_claim_id_idx
    ON public.driver_insurance_periods (claim_id) WHERE claim_id IS NOT NULL;

-- Every release writer clears identity/stamp, including legacy app paths.
-- This trigger never creates a UUID; only the flag-gated v2 claim RPCs do.
CREATE OR REPLACE FUNCTION public.clear_driver_availability_claim_on_release()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.is_available IS TRUE THEN
        NEW.availability_claim_id := NULL;
        NEW.availability_claimed_at := NULL;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS drivers_clear_availability_claim_on_release ON public.drivers;
CREATE TRIGGER drivers_clear_availability_claim_on_release
    BEFORE UPDATE OF is_available ON public.drivers
    FOR EACH ROW EXECUTE FUNCTION public.clear_driver_availability_claim_on_release();

-- Replaces migration 442's app-clock-vs-DB-clock timestamp ordering. Locks the
-- driver and period, and only releases the exact claim that made this offer.
CREATE OR REPLACE FUNCTION public.release_batch_offer_driver_and_close_period_v2(
    p_driver_id text,
    p_ride_id text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_is_online boolean;
    v_user_id text;
    v_driver_claim_id uuid;
    v_offer_claim_id uuid;
    v_period smallint;
    v_period_ride_id text;
    v_period_claim_id uuid;
    v_new_period smallint;
    v_transition jsonb;
    v_period_missing boolean := false;
BEGIN
    SELECT is_online, user_id, availability_claim_id
      INTO v_is_online, v_user_id, v_driver_claim_id
      FROM drivers WHERE id = p_driver_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('status', 'driver_missing'); END IF;

    IF NOT EXISTS (SELECT 1 FROM rides WHERE id = p_ride_id AND status = 'cancelled') THEN
        RETURN jsonb_build_object('status', 'target_ride_not_cancelled');
    END IF;

    SELECT claim_id INTO v_offer_claim_id FROM ride_offers
     WHERE driver_id = p_driver_id AND ride_id = p_ride_id AND status = 'cancelled'
     ORDER BY responded_at DESC LIMIT 1;
    IF NOT FOUND OR v_offer_claim_id IS NULL THEN
        RETURN jsonb_build_object('status', 'cancelled_offer_missing_or_unidentified');
    END IF;
    IF v_driver_claim_id IS NULL OR v_driver_claim_id <> v_offer_claim_id THEN
        RETURN jsonb_build_object('status', 'claim_identity_mismatch');
    END IF;
    IF EXISTS (SELECT 1 FROM ride_offers ro JOIN rides r ON r.id = ro.ride_id
                  WHERE ro.driver_id = p_driver_id AND ro.status = 'pending'
                    AND r.status IN ('searching', 'driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress'))
       OR EXISTS (SELECT 1 FROM ride_offers ro JOIN rides r ON r.id = ro.ride_id
                  WHERE ro.driver_id = p_driver_id AND ro.status = 'accepted'
                    AND r.status IN ('driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress')) THEN
        RETURN jsonb_build_object('status', 'other_offer_active');
    END IF;
    IF EXISTS (SELECT 1 FROM rides WHERE driver_id = p_driver_id
               AND status IN ('driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress')) THEN
        RETURN jsonb_build_object('status', 'other_ride_active');
    END IF;

    SELECT period, ride_id, claim_id INTO v_period, v_period_ride_id, v_period_claim_id
      FROM driver_insurance_periods WHERE driver_id = p_driver_id AND ended_at IS NULL FOR UPDATE;
    IF FOUND AND NOT COALESCE((
        (v_period = 2 AND v_period_ride_id = p_ride_id AND v_period_claim_id = v_driver_claim_id)
        OR (v_period = 1 AND v_period_ride_id IS NULL AND v_period_claim_id IS NULL)
    ), false) THEN
        RETURN jsonb_build_object('status', 'ownership_mismatch');
    END IF;
    IF NOT FOUND THEN v_period_missing := true; END IF;

    v_new_period := CASE WHEN COALESCE(v_is_online, false) THEN 1 ELSE 0 END;
    UPDATE drivers SET is_available = COALESCE(v_is_online, false),
                       availability_claim_id = NULL, availability_claimed_at = NULL
     WHERE id = p_driver_id;
    v_transition := record_insurance_period_transition(p_driver_id, v_new_period, NULL);
    IF COALESCE(v_transition->>'status', '') NOT IN ('ok', 'noop') THEN
        RAISE EXCEPTION 'cancelled offer release period transition failed driver_id=% ride_id=% result=%',
            p_driver_id, p_ride_id, v_transition USING ERRCODE = 'P0001';
    END IF;
    RETURN jsonb_build_object('status', 'released', 'period', v_new_period, 'user_id', v_user_id,
                              'period_missing', v_period_missing);
END;
$$;

REVOKE ALL ON FUNCTION public.release_batch_offer_driver_and_close_period_v2(text, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.release_batch_offer_driver_and_close_period_v2(text, text) TO service_role;

-- Legacy NULL-ID cleanup is guarded atomically against a current V2 claim.
CREATE OR REPLACE FUNCTION public.release_batch_offer_driver_legacy_guard_v2(
    p_driver_id text, p_ride_id text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_claim_id uuid;
BEGIN
    SELECT availability_claim_id INTO v_claim_id FROM public.drivers
     WHERE id = p_driver_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('status', 'driver_missing'); END IF;
    IF v_claim_id IS NOT NULL THEN
        RETURN jsonb_build_object('status', 'newer_identity_claim');
    END IF;
    RETURN public.release_batch_offer_driver_and_close_period(p_driver_id, p_ride_id);
END;
$$;
REVOKE ALL ON FUNCTION public.release_batch_offer_driver_legacy_guard_v2(text, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.release_batch_offer_driver_legacy_guard_v2(text, text) TO service_role;

-- Database-clock reaper: identity, staleness, availability release, and the
-- Period-2 close/Period-1-or-0 open are one transaction. A missing period is
-- not backfilled with a synthetic Period 2; the current period is opened and
-- an explicit error log records the historical gap.
CREATE OR REPLACE FUNCTION public.reap_stale_driver_claim_v2(p_driver_id text, p_expected_claim_id uuid)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_online boolean;
    v_available boolean;
    v_claim_id uuid;
    v_claimed_at timestamptz;
    v_user_id text;
    v_period smallint;
    v_period_ride_id text;
    v_period_claim_id uuid;
    v_ride_id text;
    v_new_period smallint;
    v_transition jsonb;
    v_period_missing boolean := false;
BEGIN
    SELECT is_online, is_available, availability_claim_id, availability_claimed_at, user_id
      INTO v_online, v_available, v_claim_id, v_claimed_at, v_user_id
      FROM drivers WHERE id = p_driver_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('status', 'driver_missing'); END IF;
    IF NOT COALESCE(v_online, false) OR COALESCE(v_available, true) THEN
        RETURN jsonb_build_object('status', 'not_claimed');
    END IF;
    IF v_claim_id IS DISTINCT FROM p_expected_claim_id THEN
        RETURN jsonb_build_object('status', 'claim_identity_changed');
    END IF;
    IF v_claim_id IS NULL OR v_claimed_at IS NULL THEN
        RETURN jsonb_build_object('status', 'claim_identity_or_stamp_missing');
    END IF;
    IF v_claimed_at > clock_timestamp() - interval '90 seconds' THEN
        RETURN jsonb_build_object('status', 'claim_too_recent');
    END IF;
    IF EXISTS (SELECT 1 FROM ride_offers ro JOIN rides r ON r.id = ro.ride_id
                  WHERE ro.driver_id = p_driver_id AND ro.status = 'pending'
                    AND r.status IN ('searching', 'driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress'))
       OR EXISTS (SELECT 1 FROM ride_offers ro JOIN rides r ON r.id = ro.ride_id
                  WHERE ro.driver_id = p_driver_id
                    AND ro.status = 'accepted'
                    AND r.status IN ('driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress')) THEN
        RETURN jsonb_build_object('status', 'offer_active');
    END IF;
    IF EXISTS (SELECT 1 FROM rides WHERE driver_id = p_driver_id
               AND status IN ('driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress')) THEN
        RETURN jsonb_build_object('status', 'ride_active');
    END IF;

    SELECT period, ride_id, claim_id INTO v_period, v_period_ride_id, v_period_claim_id
      FROM driver_insurance_periods WHERE driver_id = p_driver_id AND ended_at IS NULL FOR UPDATE;
    IF FOUND THEN
        IF NOT COALESCE((
            (v_period = 2 AND v_period_claim_id = v_claim_id)
            OR (v_period = 1 AND v_period_ride_id IS NULL AND v_period_claim_id IS NULL)
        ), false) THEN
            RETURN jsonb_build_object('status', 'period_ownership_mismatch');
        END IF;
        IF v_period = 2 AND NOT EXISTS (
            SELECT 1 FROM ride_offers ro
             WHERE ro.driver_id = p_driver_id AND ro.claim_id = v_claim_id
               AND ro.ride_id = v_period_ride_id
        ) THEN
            RETURN jsonb_build_object('status', 'period_offer_identity_mismatch');
        END IF;
    ELSE
        v_period_missing := true;
    END IF;

    v_new_period := CASE WHEN COALESCE(v_online, false) THEN 1 ELSE 0 END;
    UPDATE drivers SET is_available = true, availability_claim_id = NULL,
                       availability_claimed_at = NULL WHERE id = p_driver_id;
    v_transition := record_insurance_period_transition(p_driver_id, v_new_period, NULL);
    IF COALESCE(v_transition->>'status', '') NOT IN ('ok', 'noop') THEN
        RAISE EXCEPTION 'stale claim recovery period transition failed driver_id=% result=%',
            p_driver_id, v_transition USING ERRCODE = 'P0001';
    END IF;
    RETURN jsonb_build_object('status', 'released', 'period', v_new_period, 'user_id', v_user_id,
                              'period_missing', v_period_missing);
END;
$$;

REVOKE ALL ON FUNCTION public.reap_stale_driver_claim_v2(text, uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reap_stale_driver_claim_v2(text, uuid) TO service_role;

-- Preserve recovery for old NULL-identity claims while guarding the final
-- release with the observed stamp and a locked row. This compatibility path
-- is timestamp-aged only; the v2 path above never uses timestamps for owner
-- identity. It closes a matching legacy Period 2 before opening Period 1.
CREATE OR REPLACE FUNCTION public.reap_stale_legacy_driver_claim_v2(
    p_driver_id text, p_expected_claimed_at timestamptz, p_stale_before timestamptz
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_online boolean;
    v_available boolean;
    v_claim_id uuid;
    v_claimed_at timestamptz;
    v_user_id text;
    v_period smallint;
    v_period_ride_id text;
    v_period_claim_id uuid;
    v_transition jsonb;
    v_period_missing boolean := false;
BEGIN
    SELECT is_online, is_available, availability_claim_id, availability_claimed_at, user_id
      INTO v_online, v_available, v_claim_id, v_claimed_at, v_user_id
      FROM public.drivers WHERE id = p_driver_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('status', 'driver_missing'); END IF;
    IF v_claim_id IS NOT NULL OR v_claimed_at IS DISTINCT FROM p_expected_claimed_at THEN
        RETURN jsonb_build_object('status', 'legacy_claim_changed');
    END IF;
    IF NOT COALESCE(v_online, false) OR COALESCE(v_available, true) THEN
        RETURN jsonb_build_object('status', 'not_claimed');
    END IF;
    IF p_expected_claimed_at IS NULL OR p_stale_before IS NULL OR p_expected_claimed_at > p_stale_before THEN
        RETURN jsonb_build_object('status', 'claim_too_recent');
    END IF;
    IF EXISTS (SELECT 1 FROM public.ride_offers ro JOIN public.rides r ON r.id = ro.ride_id
               WHERE ro.driver_id = p_driver_id AND ro.status = 'pending'
                 AND r.status IN ('searching', 'driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress'))
       OR EXISTS (SELECT 1 FROM public.ride_offers ro JOIN public.rides r ON r.id = ro.ride_id
               WHERE ro.driver_id = p_driver_id AND ro.status = 'accepted'
                 AND r.status IN ('driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress'))
       OR EXISTS (SELECT 1 FROM public.rides WHERE driver_id = p_driver_id
               AND status IN ('driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress')) THEN
        RETURN jsonb_build_object('status', 'offer_or_ride_active');
    END IF;

    SELECT period, ride_id, claim_id INTO v_period, v_period_ride_id, v_period_claim_id
      FROM public.driver_insurance_periods WHERE driver_id = p_driver_id AND ended_at IS NULL FOR UPDATE;
    IF FOUND THEN
        IF NOT COALESCE(
            (v_period = 2 AND v_period_claim_id IS NULL AND v_period_ride_id IS NOT NULL
             AND EXISTS (SELECT 1 FROM public.ride_offers ro WHERE ro.driver_id = p_driver_id
                         AND ro.ride_id = v_period_ride_id AND ro.status IN ('expired', 'cancelled')))
            OR (v_period = 1 AND v_period_ride_id IS NULL AND v_period_claim_id IS NULL), false
        ) THEN
            RETURN jsonb_build_object('status', 'period_ownership_mismatch');
        END IF;
    ELSE
        v_period_missing := true;
    END IF;
    UPDATE public.drivers SET is_available = true WHERE id = p_driver_id;
    v_transition := public.record_insurance_period_transition(p_driver_id, 1::smallint, NULL);
    IF COALESCE(v_transition->>'status', '') NOT IN ('ok', 'noop') THEN
        RAISE EXCEPTION 'legacy stale claim recovery period transition failed driver_id=% result=%',
            p_driver_id, v_transition USING ERRCODE = 'P0001';
    END IF;
    RETURN jsonb_build_object('status', 'released', 'period', 1, 'user_id', v_user_id,
                              'period_missing', v_period_missing);
END;
$$;
REVOKE ALL ON FUNCTION public.reap_stale_legacy_driver_claim_v2(text, timestamptz, timestamptz)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reap_stale_legacy_driver_claim_v2(text, timestamptz, timestamptz) TO service_role;

CREATE OR REPLACE FUNCTION public.claim_driver_with_identity_v2(p_driver_id text)
RETURNS SETOF public.drivers
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NOT COALESCE((SELECT dispatch_claim_identity_enabled FROM public.settings WHERE id = 'app_settings'), false) THEN
        RAISE EXCEPTION 'durable dispatch claim identity is disabled';
    END IF;
    RETURN QUERY
        UPDATE public.drivers AS d
           SET is_available = false,
               availability_claim_id = gen_random_uuid(),
               availability_claimed_at = clock_timestamp()
         WHERE d.id = p_driver_id AND d.is_available = true
        RETURNING d.*;
END;
$$;
REVOKE ALL ON FUNCTION public.claim_driver_with_identity_v2(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_driver_with_identity_v2(text) TO service_role;
-- Keep the dispatch claim SQL compact and scoped here: the 403 legacy body
-- remains intact; this v2 function differs only by claim identity stamping
-- and binding that UUID to the offer.
-- V2 direct-pool claim body: same result contract as 403, with explicit
-- UUID + DB-clock stamp, offer binding, and unchanged period trigger flow.
CREATE OR REPLACE FUNCTION public.dispatch_claim_batch_v2(
    p_ride_id       text,
    p_driver_ids    text[],
    p_eta_seconds   int[],
    p_max_offers    int,
    p_offered_at    timestamptz,
    p_expires_at    timestamptz
)
RETURNS TABLE(
    driver_id         text,
    claimed           boolean,
    driver_row        jsonb,
    ride_offer_id     uuid,
    insurance_written boolean
)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_n                 int;
    v_i                 int;
    v_driver_id         text;
    v_eta               int;
    v_driver_row        drivers%ROWTYPE;
    v_offer_id          uuid;
    v_claimed_count     int := 0;
    v_insurance_written boolean;
BEGIN
    IF NOT COALESCE((SELECT dispatch_claim_identity_enabled FROM public.settings WHERE id = 'app_settings'), false) THEN
        RAISE EXCEPTION 'durable dispatch claim identity is disabled';
    END IF;
    -- Argument validation (399-style): fail loud on anything that would
    -- otherwise widen the batch or write an un-reapable offer.
    IF p_ride_id IS NULL OR p_ride_id = '' THEN
        RAISE EXCEPTION 'dispatch_claim_batch_v2: p_ride_id is required';
    END IF;
    IF p_max_offers IS NULL OR p_max_offers < 1 OR p_max_offers > 50 THEN
        RAISE EXCEPTION 'dispatch_claim_batch_v2: p_max_offers must be between 1 and 50 (got %)', p_max_offers;
    END IF;
    IF p_offered_at IS NULL OR p_expires_at IS NULL OR p_expires_at <= p_offered_at THEN
        -- A NULL expires_at would produce a pending offer the durable
        -- offer-expiry reaper (WHERE expires_at < now()) can never expire.
        RAISE EXCEPTION 'dispatch_claim_batch_v2: p_offered_at/p_expires_at must be non-NULL with expires_at > offered_at';
    END IF;

    v_n := COALESCE(array_length(p_driver_ids, 1), 0);

    -- Defensive parity check: a misaligned parallel array would silently
    -- attach the wrong ETA to the wrong driver's ride_offers row. Fail
    -- loud rather than guess, per CLAUDE.md's "do not silently swallow
    -- errors" rule — doubly so on a path that writes regulatory audit
    -- rows.
    IF COALESCE(array_length(p_eta_seconds, 1), 0) <> v_n THEN
        RAISE EXCEPTION
            'dispatch_claim_batch_v2: p_driver_ids (%) and p_eta_seconds (%) length mismatch',
            v_n, COALESCE(array_length(p_eta_seconds, 1), 0);
    END IF;

    IF v_n = 0 THEN
        RETURN;
    END IF;

    FOR v_i IN 1..v_n LOOP
        -- Same stopping condition as matching.py:860-861
        -- (`if len(claimed_drivers) >= max_offers: break`), checked BEFORE
        -- attempting the next candidate — a driver never reached this way
        -- is correctly not "attempted" (no cache invalidation owed for it).
        EXIT WHEN v_claimed_count >= p_max_offers;

        v_driver_id := p_driver_ids[v_i];
        v_eta       := p_eta_seconds[v_i];

        IF v_driver_id IS NULL THEN
            -- A NULL element cannot be claimed or cache-invalidated; skip it
            -- without emitting a row (nothing to invalidate).
            CONTINUE;
        END IF;

        -- Step 1: atomic claim — identical predicate to
        -- driver_repo.py:claim_driver_atomic. The row is locked with
        -- FOR UPDATE SKIP LOCKED first so a row a concurrent batch already
        -- holds reads as "lost the race" instead of blocking (see the
        -- Locking section above). A concurrent caller racing the same
        -- driver_id either wins this UPDATE or gets 0 rows here.
        UPDATE drivers AS d
        SET is_available = false,
            availability_claim_id = gen_random_uuid(),
            availability_claimed_at = clock_timestamp()
        FROM (
            SELECT c.id
            FROM drivers AS c
            WHERE c.id = v_driver_id
              AND c.is_available = true
            FOR UPDATE OF c SKIP LOCKED
        ) AS locked
        WHERE d.id = locked.id
        RETURNING d.* INTO v_driver_row;

        IF NOT FOUND THEN
            -- Already claimed by someone else, locked by a concurrent
            -- batch, or never available.
            driver_id         := v_driver_id;
            claimed           := false;
            driver_row        := NULL;
            ride_offer_id     := NULL;
            insurance_written := NULL;
            RETURN NEXT;
            CONTINUE;
        END IF;

        -- Step 2: revalidate the FULL eligibility set on the freshly
        -- claimed row (matching.py:875) — is_available alone (checked by
        -- the UPDATE's WHERE clause) is not sufficient; an admin could
        -- have suspended/unverified the driver between the candidate
        -- read and this claim.
        IF NOT (
            COALESCE(v_driver_row.is_online, false)
            AND COALESCE(v_driver_row.is_verified, false)
            AND v_driver_row.status = 'active'
        ) THEN
            -- Release — mirrors set_driver_available(driver_id, True)
            -- exactly, including clearing availability_claimed_at so the
            -- orphan-claim reaper (migration 157) doesn't later treat
            -- this as a stuck claim. The is_available => is_online clamp
            -- (driver_repo.py:157-171) is applied: an offline driver is
            -- never marked available.
            UPDATE drivers
            SET is_available = COALESCE(v_driver_row.is_online, false),
                availability_claim_id = NULL,
                availability_claimed_at = NULL
            WHERE id = v_driver_row.id;

            driver_id         := v_driver_row.id;
            claimed           := false;
            driver_row        := NULL;
            ride_offer_id     := NULL;
            insurance_written := NULL;
            RETURN NEXT;
            CONTINUE;
        END IF;

        -- Step 3: ride_offers insert — mirrors _build_offer_rows
        -- (matching.py:132-150) column-for-column. ON CONFLICT: this
        -- driver already holds a ride_offers row for this ride (a re-offer
        -- after decline/expiry that the Redis offer_skip guard did not
        -- catch). Do not abort the batch; release the driver and report
        -- them unclaimed, exactly as a failed revalidation does.
        v_offer_id := NULL;
        INSERT INTO ride_offers (
            ride_id, driver_id, status, eta_seconds, offered_at, expires_at, claim_id
        ) VALUES (
            p_ride_id, v_driver_row.id, 'pending', v_eta, p_offered_at, p_expires_at, v_driver_row.availability_claim_id
        )
        -- Named constraint, not a column list: inside plpgsql the output
        -- column `driver_id` is also a variable, so `ON CONFLICT (ride_id,
        -- driver_id)` is rejected as ambiguous (caught by the local psql run).
        ON CONFLICT ON CONSTRAINT ride_offers_ride_driver_uq DO NOTHING
        RETURNING id INTO v_offer_id;

        IF NOT FOUND OR v_offer_id IS NULL THEN
            UPDATE drivers
            SET is_available = true,
                availability_claim_id = NULL,
                availability_claimed_at = NULL
            WHERE id = v_driver_row.id;

            driver_id         := v_driver_row.id;
            claimed           := false;
            driver_row        := NULL;
            ride_offer_id     := NULL;
            insurance_written := NULL;
            RETURN NEXT;
            CONTINUE;
        END IF;

        -- Step 4: insurance Period 2 transition, same transaction as the
        -- claim + offer insert (T12's atomicity requirement — see the
        -- RESOLVED note above on started_at granularity). Not gated on the
        -- return value, matching matching.py:923-924.
        --
        -- FIX (Surya, C50 Phase 2 T15 adversarial review, 2026-09-02):
        -- the Python compliance-write wrapper (utils/insurance_periods.py's
        -- record_period_transition) deliberately swallows ANY exception
        -- from this RPC ("a missed audit row is preferable to blocking the
        -- driver state machine" — its own module docstring) so a hiccup on
        -- the insurance write never affects the claim or the offer. A bare
        -- `PERFORM` here would NOT have that property: any error other than
        -- the unique_violation record_insurance_period_transition already
        -- catches internally (deadlock, statement timeout, a future schema
        -- change) would propagate out of the PERFORM and abort this WHOLE
        -- dispatch_claim_batch transaction — rolling back every claim and
        -- every ride_offers insert in the batch, not just this driver's,
        -- over a best-effort compliance write. That is a materially worse
        -- failure mode than today's (an insurance-table blip could block
        -- ALL dispatch for a ride instead of zero), so it is wrapped in its
        -- own sub-transaction via a nested block. On failure the claim and
        -- ride_offers row for this driver stand regardless, and the outcome
        -- is reported to the caller via insurance_written = false so the
        -- application logs at ERROR and increments
        -- spinr_insurance_period_write_failed_total exactly as the
        -- PostgREST path does (review fix, 2026-09-03 — a RAISE WARNING
        -- alone reaches only the Postgres server log). The WARNING is kept
        -- as a secondary trace in the server log.
        v_insurance_written := true;
        BEGIN
            PERFORM record_insurance_period_transition(v_driver_row.id, 2::smallint, p_ride_id);
        EXCEPTION WHEN OTHERS THEN
            v_insurance_written := false;
            RAISE WARNING
                'dispatch_claim_batch: insurance-period-2 write failed for driver % ride % (claim and offer stand) — %',
                v_driver_row.id, p_ride_id, SQLERRM;
        END;

        v_claimed_count := v_claimed_count + 1;

        driver_id         := v_driver_row.id;
        claimed           := true;
        driver_row        := to_jsonb(v_driver_row);
        ride_offer_id     := v_offer_id;
        insurance_written := v_insurance_written;
        RETURN NEXT;
    END LOOP;

    RETURN;
END;
$$;

COMMENT ON FUNCTION public.dispatch_claim_batch_v2(text, text[], int[], int, timestamptz, timestamptz) IS
    'C50 Phase 2 (T12): atomic batch driver-claim + ride_offers insert + '
    'insurance-period-2 transition for the direct-pool dispatch path. '
    'Returns one row per ATTEMPTED driver (claimed=true/false), not just '
    'successes, so the Python caller can invalidate_driver_cache for every '
    'attempted driver (see the migration header for why); insurance_written '
    'reports the best-effort Period-2 write per claimed driver. Claims with '
    'FOR UPDATE SKIP LOCKED so concurrent batches cannot deadlock. Dark until '
    'dispatch_direct_pool_enabled (migration 401) is true AND matching.py '
    '(T13) calls it. Supersedes dead code match_and_claim_driver '
    '(migrations 77/80). Created in migration 402; body superseded by 403.';

-- Lock down EXECUTE. 354's sweep covers only SECURITY DEFINER functions that
-- existed when it ran; this INVOKER function must carry its own block (same
-- three-statement form as 399). This is a safety/compliance-adjacent RPC
-- (claims drivers, writes ride_offers, writes the regulatory
-- insurance-period audit table) with no internal auth guard of its own —
-- the grant model IS the access control, exactly the class of gap 354 was
-- written to close.
REVOKE ALL ON FUNCTION public.dispatch_claim_batch_v2(text, text[], int[], int, timestamptz, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.dispatch_claim_batch_v2(text, text[], int[], int, timestamptz, timestamptz) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.dispatch_claim_batch_v2(text, text[], int[], int, timestamptz, timestamptz) TO service_role;

NOTIFY pgrst, 'reload schema';
