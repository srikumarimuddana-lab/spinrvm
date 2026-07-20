-- 235_trip_location_route_integrity.sql
--
-- Durable active-trip breadcrumbs and versioned, segmented route geometry.
-- New v2 identities are nullable on the legacy breadcrumb table so historical
-- rows coexist. Existing ride_routes default to complete so this migration
-- never queues historical rides for re-finalization.
--
-- Rollback plan:
--   Drop the indexes and constraints created below, then use ALTER TABLE ...
--   DROP COLUMN only for added columns after v2 writers and readers have been
--   disabled. Do not remove
--   already-persisted v2 route data before the retention policy permits it.

ALTER TABLE public.driver_location_history
    ADD COLUMN IF NOT EXISTS captured_at timestamptz,
    ADD COLUMN IF NOT EXISTS recording_session_id uuid,
    ADD COLUMN IF NOT EXISTS sequence_number bigint,
    ADD COLUMN IF NOT EXISTS monotonic_ms bigint,
    ADD COLUMN IF NOT EXISTS source text,
    ADD COLUMN IF NOT EXISTS mocked boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS is_completion_fix boolean NOT NULL DEFAULT false;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'dlh_sequence_number_nonnegative'
    ) THEN
        ALTER TABLE public.driver_location_history
            ADD CONSTRAINT dlh_sequence_number_nonnegative
            CHECK (sequence_number IS NULL OR sequence_number >= 0) NOT VALID;
    END IF;
END $$;

ALTER TABLE public.ride_routes
    ADD COLUMN IF NOT EXISTS route_schema_version integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS route_revision integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS processing_status text NOT NULL DEFAULT 'complete',
    ADD COLUMN IF NOT EXISTS observed_segments jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS road_matched_segments jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS completion_point jsonb,
    ADD COLUMN IF NOT EXISTS snapshot_revision integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS processing_claimed_at timestamptz,
    ADD COLUMN IF NOT EXISTS next_retry_at timestamptz,
    ADD COLUMN IF NOT EXISTS retry_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS finalized_at timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ride_routes_schema_version_positive') THEN
        ALTER TABLE public.ride_routes
            ADD CONSTRAINT ride_routes_schema_version_positive
            CHECK (route_schema_version >= 1) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ride_routes_revision_nonnegative') THEN
        ALTER TABLE public.ride_routes
            ADD CONSTRAINT ride_routes_revision_nonnegative
            CHECK (route_revision >= 0 AND snapshot_revision >= 0 AND retry_count >= 0) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ride_routes_processing_status_valid') THEN
        ALTER TABLE public.ride_routes
            ADD CONSTRAINT ride_routes_processing_status_valid
            CHECK (processing_status IN ('pending', 'processing', 'complete', 'incomplete', 'failed')) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ride_routes_observed_segments_array') THEN
        ALTER TABLE public.ride_routes
            ADD CONSTRAINT ride_routes_observed_segments_array
            CHECK (jsonb_typeof(observed_segments) = 'array') NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ride_routes_road_matched_segments_array') THEN
        ALTER TABLE public.ride_routes
            ADD CONSTRAINT ride_routes_road_matched_segments_array
            CHECK (jsonb_typeof(road_matched_segments) = 'array') NOT VALID;
    END IF;
END $$;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS route_integrity_v2_mode text NOT NULL DEFAULT 'shadow',
    ADD COLUMN IF NOT EXISTS route_finalize_grace_seconds integer NOT NULL DEFAULT 60,
    ADD COLUMN IF NOT EXISTS route_location_gap_alert_seconds integer NOT NULL DEFAULT 30;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'settings_route_integrity_v2_mode_valid') THEN
        ALTER TABLE public.settings
            ADD CONSTRAINT settings_route_integrity_v2_mode_valid
            CHECK (route_integrity_v2_mode IN ('off', 'shadow', 'on')) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'settings_route_integrity_intervals_nonnegative') THEN
        ALTER TABLE public.settings
            ADD CONSTRAINT settings_route_integrity_intervals_nonnegative
            CHECK (route_finalize_grace_seconds >= 0 AND route_location_gap_alert_seconds >= 0) NOT VALID;
    END IF;
END $$;

COMMENT ON COLUMN public.driver_location_history.captured_at IS
    'Immutable device capture time for v2 trip breadcrumbs; received_at remains server ingestion diagnostics.';
COMMENT ON COLUMN public.ride_routes.observed_segments IS
    'Timestamp-ordered continuous observed GPS segments; GPS-derived PII retained for three years.';
COMMENT ON COLUMN public.ride_routes.road_matched_segments IS
    'Per-observed-segment map-matching output; never a cross-gap concatenated line.';
COMMENT ON COLUMN public.settings.route_integrity_v2_mode IS
    'Route integrity rollout mode: off, shadow, or on. New deployments begin in shadow.';

NOTIFY pgrst, 'reload schema';
