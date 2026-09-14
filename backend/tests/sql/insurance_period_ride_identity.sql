-- Run ONLY in a disposable database with migration 253 or 421 installed.
-- psql -v ON_ERROR_STOP=1 -f backend/tests/sql/insurance_period_ride_identity.sql
-- Synthetic fixture table; the transaction always rolls back on success.
BEGIN;
CREATE TABLE IF NOT EXISTS public.driver_insurance_periods (
    driver_id text NOT NULL, period smallint NOT NULL,
    started_at timestamptz NOT NULL, ended_at timestamptz, ride_id text
);
CREATE UNIQUE INDEX IF NOT EXISTS driver_insurance_periods_open
    ON public.driver_insurance_periods(driver_id) WHERE ended_at IS NULL;

DO $$
DECLARE result jsonb; original_start timestamptz;
BEGIN
    PERFORM record_insurance_period_transition('fixture-driver', 2::smallint, 'ride-a');
    SELECT started_at INTO original_start FROM driver_insurance_periods
        WHERE driver_id = 'fixture-driver' AND ended_at IS NULL;
    result := record_insurance_period_transition('fixture-driver', 2::smallint, 'ride-a');
    ASSERT result->>'status' = 'noop', 'same period and ride must be idempotent';
    result := record_insurance_period_transition('fixture-driver', 2::smallint, 'ride-b');
    ASSERT result->>'status' = 'ok', 'new ride must open its own Period 2';
    ASSERT (SELECT count(*) = 1 FROM driver_insurance_periods
        WHERE driver_id = 'fixture-driver' AND period = 2 AND ride_id = 'ride-a'
        AND started_at = original_start AND ended_at IS NOT NULL), 'preserve prior attribution';
    ASSERT (SELECT count(*) = 1 FROM driver_insurance_periods
        WHERE driver_id = 'fixture-driver' AND period = 2 AND ride_id = 'ride-b'
        AND ended_at IS NULL), 'new ride owns the open row';
    PERFORM record_insurance_period_transition('fixture-driver', 3::smallint, 'ride-b');
    result := record_insurance_period_transition('fixture-driver', 3::smallint, 'ride-b');
    ASSERT result->>'status' = 'noop', 'same Period 3 retry must be idempotent';
    PERFORM record_insurance_period_transition('fixture-driver', 1::smallint);
    result := record_insurance_period_transition('fixture-driver', 1::smallint);
    ASSERT result->>'status' = 'noop', 'NULL ride identity must compare equal';
    PERFORM record_insurance_period_transition('fixture-null', 2::smallint);
    result := record_insurance_period_transition('fixture-null', 2::smallint, 'ride-c');
    ASSERT result->>'status' = 'ok', 'NULL to concrete ride must transition';
    result := record_insurance_period_transition('fixture-null', 2::smallint);
    ASSERT result->>'status' = 'ok', 'concrete to NULL ride must transition';
    BEGIN
        PERFORM record_insurance_period_transition('fixture-driver', 3::smallint);
        RAISE EXCEPTION 'Period 3 accepted a missing ride';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM NOT LIKE 'ride_id is required%' THEN RAISE; END IF;
    END;
    ASSERT (SELECT count(*) = 1 FROM driver_insurance_periods
        WHERE driver_id = 'fixture-driver' AND ended_at IS NULL), 'one open period';
END $$;
ROLLBACK;
