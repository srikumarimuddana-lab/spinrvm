-- Fix F4: a new ride needs its own interval even when the period is unchanged.
-- Rollback: restore the function body from migration 253 using CREATE OR REPLACE;
-- keep the service-role-only grants below. Do not rewrite existing audit rows.
-- This replaces code only: no historical backfill, deletion, or ride reassignment.
-- Closing an open interval (ended_at) retains the established 253 lifecycle;
-- completed interval attribution and timestamps remain untouched.
-- Signature, query pattern, partial unique index, and caller contract unchanged.
--
-- migration-override-ok: intentionally redefines record_insurance_period_transition,
-- which migration 253_insurance_period_transition_rpc.sql already defines. Redefining
-- it here is the whole point of this migration — 253's no-op check compares `period`
-- alone (253:44), so a driver already open on Period 2 for ride A returned
-- {"status":"noop"} when claimed for ride B, and ride B never got its own Period 2
-- interval (F4, docs/audit/2026-09-13-driver-app-mid-ride-process-death.md). The
-- append-only rule forbids editing 253 in place, so the corrected body ships as a
-- new file per backend/migrations/CLAUDE.md. Signature is byte-identical, so no
-- caller changes; the REVOKE/GRANT tail below re-asserts service-role-only execute
-- so the final ACL matches 354's intent regardless of apply order.

CREATE OR REPLACE FUNCTION record_insurance_period_transition(
    p_driver_id text,
    p_new_period smallint,
    p_ride_id text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    v_now timestamptz := now();
    v_closed int;
    v_current_period smallint;
    v_current_ride_id text;
BEGIN
    -- Period 3 requires a ride_id (passenger aboard).
    IF p_new_period = 3 AND p_ride_id IS NULL THEN
        RAISE EXCEPTION 'ride_id is required when new_period = 3 (passenger aboard)';
    END IF;

    IF p_new_period NOT IN (0, 1, 2, 3) THEN
        RAISE EXCEPTION 'new_period must be 0, 1, 2, or 3; got %', p_new_period;
    END IF;

    -- Check if the driver already has an open row for the same period AND ride (including NULL).
    SELECT period, ride_id INTO v_current_period, v_current_ride_id
    FROM driver_insurance_periods
    WHERE driver_id = p_driver_id AND ended_at IS NULL;

    IF v_current_period IS NOT NULL AND v_current_period = p_new_period
       AND v_current_ride_id IS NOT DISTINCT FROM p_ride_id THEN
        RETURN jsonb_build_object('status', 'noop', 'closed', 0, 'opened', false);
    END IF;

    -- Step 1: close the currently-open row (if any).
    UPDATE driver_insurance_periods
    SET ended_at = v_now
    WHERE driver_id = p_driver_id AND ended_at IS NULL;

    GET DIAGNOSTICS v_closed = ROW_COUNT;

    -- Step 2: insert the new row.
    BEGIN
        INSERT INTO driver_insurance_periods (driver_id, period, started_at, ride_id)
        VALUES (p_driver_id, p_new_period, v_now, p_ride_id);
    EXCEPTION WHEN unique_violation THEN
        -- Concurrent caller won the race on the partial unique index.
        RETURN jsonb_build_object('status', 'race', 'closed', v_closed, 'opened', false);
    END;

    RETURN jsonb_build_object('status', 'ok', 'closed', v_closed, 'opened', true);
END;
$$;

REVOKE ALL ON FUNCTION record_insurance_period_transition(text, smallint, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION record_insurance_period_transition(text, smallint, text)
    TO service_role;

COMMENT ON FUNCTION record_insurance_period_transition(text, smallint, text) IS
    'Atomic insurance close+open; no-op only for the same period and ride identity. Migration 419.';
