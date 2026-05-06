-- migration: 63_driver_insurance_periods
-- purpose: audit log for TNC insurance period transitions
-- required by: Saskatchewan Transportation Act (commercial auto insurance audit)
--
-- rollback: DROP TABLE driver_insurance_periods;
--
-- Insurance periods (per CLAUDE.md):
--   0 = App off / offline              → personal auto only
--   1 = App on, available (no ride)    → TNC contingent liability
--   2 = En route to pickup             → TNC primary commercial
--   3 = Passenger aboard (in_progress) → TNC primary commercial (full coverage)
--
-- Rules:
--   - Append-only: never UPDATE or DELETE rows
--   - period 2 starts on driver_assigned (not driver_accepted)
--   - A driver cannot be in period 3 without a matching in_progress ride_id

CREATE TABLE IF NOT EXISTS driver_insurance_periods (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    driver_id      UUID        NOT NULL REFERENCES drivers(id) ON DELETE RESTRICT,
    period         SMALLINT    NOT NULL CHECK (period BETWEEN 0 AND 3),
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at       TIMESTAMPTZ,
    ride_id        UUID        REFERENCES rides(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- close the previous open period when a new one opens
CREATE INDEX IF NOT EXISTS idx_ins_periods_driver_open
    ON driver_insurance_periods (driver_id, started_at DESC)
    WHERE ended_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_ins_periods_driver_id
    ON driver_insurance_periods (driver_id);

CREATE INDEX IF NOT EXISTS idx_ins_periods_ride_id
    ON driver_insurance_periods (ride_id)
    WHERE ride_id IS NOT NULL;

-- RLS: backend service role writes; drivers can read their own rows; no client writes
ALTER TABLE driver_insurance_periods ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_all" ON driver_insurance_periods
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "driver_read_own" ON driver_insurance_periods
    FOR SELECT TO authenticated
    USING (
        driver_id IN (
            SELECT id FROM drivers WHERE user_id = auth.uid()
        )
    );
