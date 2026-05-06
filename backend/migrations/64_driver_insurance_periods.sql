-- 64_driver_insurance_periods.sql
-- M-5a: Audit table for driver insurance-period transitions.
--
-- Required by SGI / Saskatchewan Transportation Act: every moment a driver
-- spends in the app maps to one of four insurance periods. Misclassification
-- is a regulatory and insurance liability. CLAUDE.md spells out the period
-- mapping and the "append-only, never mutate" rule.
--
-- Period 0: app off / offline (personal auto only)
-- Period 1: app on, available, no ride (TNC contingent liability)
-- Period 2: assigned/accepted/arrived, no passenger (TNC primary commercial)
-- Period 3: in_progress, passenger aboard (TNC primary commercial, full)
--
-- Append-only contract:
--   * INSERT allowed.
--   * UPDATE allowed ONLY to flip ended_at from NULL → timestamp (close).
--     All other column changes raise.
--   * DELETE blocked unconditionally.
--
-- Rollback plan:
--   DROP TABLE IF EXISTS driver_insurance_periods;
--   DROP FUNCTION IF EXISTS _driver_insurance_periods_immutable();
--
-- Forward-compatible: new table, no changes to existing tables.
-- Retention: 7 years per Saskatchewan Transportation Act (aligns with
-- migration 50 PII purge — that purge intentionally leaves this table alone).

CREATE TABLE IF NOT EXISTS driver_insurance_periods (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    driver_id   text        NOT NULL REFERENCES drivers(id),
    period      smallint    NOT NULL CHECK (period IN (0, 1, 2, 3)),
    started_at  timestamptz NOT NULL DEFAULT now(),
    -- ended_at is NULL while the period is open; set when the driver
    -- transitions to a different period or goes offline.
    ended_at    timestamptz,
    -- Period 3 (and most period-2 rows) carry a ride_id. Period 0/1 do not.
    ride_id     text        REFERENCES rides(id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    -- A row is "open" while ended_at IS NULL. Period 3 must always have a
    -- ride_id; we enforce that at the row level rather than via trigger so
    -- that PostgREST returns a clean 23514 to the application.
    CONSTRAINT period_3_requires_ride
        CHECK (period <> 3 OR ride_id IS NOT NULL)
);

-- Query patterns:
--   "Find the currently-open period for a driver" — used by every transition.
CREATE UNIQUE INDEX IF NOT EXISTS driver_insurance_periods_open
    ON driver_insurance_periods (driver_id)
    WHERE ended_at IS NULL;

--   "All periods for a driver, newest first" — admin / regulator export.
CREATE INDEX IF NOT EXISTS driver_insurance_periods_driver_started
    ON driver_insurance_periods (driver_id, started_at DESC);

--   "All periods touching a ride" — incident / claim investigation.
CREATE INDEX IF NOT EXISTS driver_insurance_periods_ride
    ON driver_insurance_periods (ride_id)
    WHERE ride_id IS NOT NULL;

-- Append-only-ish RLS: backend service role bypasses; nobody else writes.
ALTER TABLE driver_insurance_periods ENABLE ROW LEVEL SECURITY;

-- Writes are backend-only. The Supabase service role bypasses RLS, so we
-- intentionally do NOT grant INSERT/UPDATE policies to authenticated/anon
-- clients — those keys must not reach this regulatory audit table.
-- SELECT is allowed for the driver themselves and for admins; riders never
-- see this table.
CREATE POLICY driver_insurance_periods_select ON driver_insurance_periods
    FOR SELECT USING (
        driver_id = (
            SELECT id FROM drivers WHERE user_id = auth.uid()::text
        )
        OR (SELECT role FROM users WHERE id = auth.uid()::text) IN ('admin', 'super_admin')
    );

-- No INSERT, UPDATE, or DELETE policies → those operations are denied for
-- authenticated/anon by default. Only the service role can write.

-- Tamper-evidence trigger: only the close transition is allowed.
-- Anything else raises so a superuser typo can't rewrite history.
CREATE OR REPLACE FUNCTION _driver_insurance_periods_immutable()
RETURNS trigger LANGUAGE plpgsql
SET search_path = public, pg_catalog
AS
$$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'driver_insurance_periods rows are append-only and cannot be deleted';
    END IF;

    -- UPDATE: allow only the close transition (ended_at NULL -> non-NULL).
    -- Every other column must be unchanged.
    IF OLD.ended_at IS NOT NULL THEN
        RAISE EXCEPTION
            'driver_insurance_periods row % is already closed and cannot be modified', OLD.id;
    END IF;

    IF NEW.ended_at IS NULL THEN
        RAISE EXCEPTION
            'driver_insurance_periods UPDATE must set ended_at to a non-NULL timestamp';
    END IF;

    IF NEW.id          IS DISTINCT FROM OLD.id
       OR NEW.driver_id IS DISTINCT FROM OLD.driver_id
       OR NEW.period    IS DISTINCT FROM OLD.period
       OR NEW.started_at IS DISTINCT FROM OLD.started_at
       OR NEW.ride_id   IS DISTINCT FROM OLD.ride_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION
            'driver_insurance_periods UPDATE may only set ended_at; other columns are immutable';
    END IF;

    RETURN NEW;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'driver_insurance_periods_no_mutate'
          AND tgrelid = 'driver_insurance_periods'::regclass
    ) THEN
        CREATE TRIGGER driver_insurance_periods_no_mutate
            BEFORE UPDATE OR DELETE ON driver_insurance_periods
            FOR EACH ROW EXECUTE FUNCTION _driver_insurance_periods_immutable();
    END IF;
END;
$$;

COMMENT ON TABLE driver_insurance_periods IS
    'Append-only audit of driver insurance-period transitions (0=offline, '
    '1=available, 2=en-route, 3=passenger). Required by SGI / Saskatchewan '
    'Transportation Act. UPDATE limited to setting ended_at; DELETE blocked. '
    '7-year retention. Created in migration 64.';
