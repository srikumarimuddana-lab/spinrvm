-- 249_driver_period_distances.sql
-- Append-only audit of distance driven per driver insurance period.
--
-- Billing is a separate concern: the rider is charged the road distance quoted
-- before the ride (fare_distance_basis='road', quote-locked). THIS table is NOT
-- for billing — it records how far the driver ACTUALLY drove, per insurance
-- period, computed from the driver's GPS coordinates + timestamps, for the SGI /
-- Saskatchewan Transportation Act commercial-coverage audit.
--
-- Insurance period mapping (see CLAUDE.md and migration 64):
--   Period 0: app off / offline        (personal auto)      — not GPS-tracked
--   Period 1: online, available, no ride (TNC contingent)   — between-rides deadhead
--   Period 2: assigned/accepted/arrived  (TNC primary)      — breadcrumb phase navigating_to_pickup
--   Period 3: in_progress, passenger     (TNC primary full) — breadcrumb phase trip_in_progress
--
-- This migration populates the ride-scoped periods (2 and 3) at ride
-- completion. Period 0/1 are not ride-scoped and are out of scope here (they
-- would be recorded by a between-rides tracker); the CHECK still permits them
-- so a future writer needs no schema change.
--
-- Append-only contract (mirrors driver_insurance_periods, migration 64):
--   * INSERT allowed (service role only).
--   * UPDATE / DELETE blocked unconditionally — a distance audit row is final.
--
-- Retention: 7 years (commercial-coverage audit; aligns with the PII purge
-- exclusion that already protects driver_insurance_periods).
--
-- Rollback:
--   DROP TABLE IF EXISTS driver_period_distances;
--   DROP FUNCTION IF EXISTS _driver_period_distances_immutable();
--
-- Forward-compatible: new table, no changes to existing tables.

CREATE TABLE IF NOT EXISTS driver_period_distances (
    id           uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    driver_id    text          NOT NULL REFERENCES drivers(id),
    -- Present for the ride-scoped periods (2, 3); NULL for a future Period 1 row.
    ride_id      text          REFERENCES rides(id),
    period       smallint      NOT NULL CHECK (period IN (0, 1, 2, 3)),
    -- Distance the driver actually drove in this period, from GPS coordinates +
    -- timestamps (haversine of the ordered breadcrumbs for the phase). Never a
    -- billing figure.
    distance_km  numeric(8, 3) NOT NULL CHECK (distance_km >= 0),
    started_at   timestamptz,
    ended_at     timestamptz,
    -- Provenance of distance_km (e.g. 'gps_measured'); lets a future filtered or
    -- reconstructed source be distinguished in the audit trail.
    source       text          NOT NULL DEFAULT 'gps_measured',
    created_at   timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT period_2_3_require_ride
        CHECK (period NOT IN (2, 3) OR ride_id IS NOT NULL)
);

-- Replay-safe: one distance row per (ride, period). The completion path uses
-- ON CONFLICT DO NOTHING so a re-run / retry never double-writes.
CREATE UNIQUE INDEX IF NOT EXISTS uq_driver_period_distances_ride_period
    ON driver_period_distances (ride_id, period)
    WHERE ride_id IS NOT NULL;

-- "All period distances for a driver, newest first" — regulator / admin export.
CREATE INDEX IF NOT EXISTS driver_period_distances_driver_created
    ON driver_period_distances (driver_id, created_at DESC);

-- "All period distances for a ride" — incident / claim investigation.
CREATE INDEX IF NOT EXISTS driver_period_distances_ride
    ON driver_period_distances (ride_id)
    WHERE ride_id IS NOT NULL;

ALTER TABLE driver_period_distances ENABLE ROW LEVEL SECURITY;

-- Writes are backend-only (service role bypasses RLS). No INSERT/UPDATE/DELETE
-- policy is granted to authenticated/anon, so those keys can never write to this
-- regulatory audit table. SELECT is allowed for the driver themselves and admins.
CREATE POLICY driver_period_distances_select ON driver_period_distances
    FOR SELECT USING (
        driver_id = (
            SELECT id FROM drivers WHERE user_id = auth.uid()::text
        )
        OR (SELECT role FROM users WHERE id = auth.uid()::text) IN ('admin', 'super_admin')
    );

-- Tamper-evidence: this table is strictly append-only. Unlike
-- driver_insurance_periods (which allows a single close transition), a distance
-- row is computed once and final — no UPDATE is ever legitimate.
CREATE OR REPLACE FUNCTION _driver_period_distances_immutable()
RETURNS trigger LANGUAGE plpgsql
SET search_path = public, pg_catalog
AS
$$
BEGIN
    RAISE EXCEPTION
        'driver_period_distances rows are append-only and cannot be modified or deleted';
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'driver_period_distances_no_mutate'
          AND tgrelid = 'driver_period_distances'::regclass
    ) THEN
        CREATE TRIGGER driver_period_distances_no_mutate
            BEFORE UPDATE OR DELETE ON driver_period_distances
            FOR EACH ROW EXECUTE FUNCTION _driver_period_distances_immutable();
    END IF;
END;
$$;

COMMENT ON TABLE driver_period_distances IS
    'Append-only per-insurance-period distance audit (GPS-measured, not billing). '
    'Period 2 = en-route (navigating_to_pickup), Period 3 = passenger (trip_in_progress). '
    'Required by SGI / Saskatchewan Transportation Act. INSERT-only; UPDATE/DELETE '
    'blocked. 7-year retention. Created in migration 249.';

NOTIFY pgrst, 'reload schema';
