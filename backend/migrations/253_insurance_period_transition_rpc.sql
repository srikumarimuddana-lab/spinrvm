-- Atomic RPC for insurance-period transitions (WS-12, C3).
--
-- The two-step close+open in Python (insurance_periods.py) left a window
-- where a crash between close and insert produced a driver with no open
-- period row — a regulatory audit gap. This function does both in one
-- transaction, with the partial unique index serializing concurrent callers.
--
-- Returns: JSON with { "status": "ok"|"noop"|"race", "closed": int, "opened": bool }
--   - "ok": transition applied (close + open)
--   - "noop": driver already has an open row for the requested period
--   - "race": unique-violation on insert (concurrent caller won)
--
-- rollback: DROP FUNCTION IF EXISTS record_insurance_period_transition(text, smallint, text);

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
BEGIN
    -- Period 3 requires a ride_id (passenger aboard).
    IF p_new_period = 3 AND p_ride_id IS NULL THEN
        RAISE EXCEPTION 'ride_id is required when new_period = 3 (passenger aboard)';
    END IF;

    IF p_new_period NOT IN (0, 1, 2, 3) THEN
        RAISE EXCEPTION 'new_period must be 0, 1, 2, or 3; got %', p_new_period;
    END IF;

    -- Check if the driver already has an open row for the same period.
    SELECT period INTO v_current_period
    FROM driver_insurance_periods
    WHERE driver_id = p_driver_id AND ended_at IS NULL;

    IF v_current_period IS NOT NULL AND v_current_period = p_new_period THEN
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

COMMENT ON FUNCTION record_insurance_period_transition IS
    'Atomic close+open for driver insurance-period transitions. '
    'Serialized by the partial unique index driver_insurance_periods_open. '
    'Created in migration 253.';
