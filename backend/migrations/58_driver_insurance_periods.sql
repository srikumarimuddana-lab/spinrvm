-- Migration 58: driver_insurance_periods table for TNC insurance audit logging.
-- =============================================================================
-- Rollback plan: DROP TABLE driver_insurance_periods CASCADE;
--   No application data is lost; rows are audit records only. Service-role
--   callers that reference the table will need to handle the missing table
--   gracefully (the Python helper already wraps in try/except).
--
-- Purpose: every moment a driver spends in the app maps to one of four TNC
--   insurance periods (CLAUDE.md "Insurance periods" section). Transitions
--   must be logged here for regulatory audit under the Saskatchewan
--   Transportation Act and SGI commercial-insurance endorsement.
--
-- Period mapping:
--   0 = App off / offline              → Personal auto only
--   1 = App on, available              → TNC contingent liability
--   2 = En route to pickup             → TNC primary commercial
--   3 = Passenger aboard (in_progress) → TNC primary commercial (full)
--
-- Retention: 7 years (regulatory — cannot be overridden by PIPEDA deletion).

CREATE TABLE IF NOT EXISTS driver_insurance_periods (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    driver_id       uuid        NOT NULL,
    period          smallint    NOT NULL CHECK (period BETWEEN 0 AND 3),
    started_at      timestamptz NOT NULL DEFAULT now(),
    ended_at        timestamptz,
    ride_id         uuid,  -- NULL for period 0/1; required for period 2/3
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Append-only: no delete, no update of immutable audit columns.
-- Service role bypasses RLS for inserts/updates (ended_at close-out).
ALTER TABLE driver_insurance_periods ENABLE ROW LEVEL SECURITY;

-- Riders and drivers cannot read other users' period rows.
CREATE POLICY "driver_insurance_periods_select_own"
    ON driver_insurance_periods FOR SELECT
    USING (auth.uid() = driver_id);

-- No client-side INSERT / UPDATE / DELETE — service role only.
-- (RLS with no permissive INSERT policy = deny for anon/auth roles.)

-- Fast audit query: all periods for a driver in chronological order.
CREATE INDEX IF NOT EXISTS idx_driver_insurance_periods_driver_started
    ON driver_insurance_periods (driver_id, started_at);

-- Support ride-level lookup (insurance audit per trip).
CREATE INDEX IF NOT EXISTS idx_driver_insurance_periods_ride
    ON driver_insurance_periods (ride_id)
    WHERE ride_id IS NOT NULL;
