-- 237_ride_location_gap_events.sql
-- Timestamp-only alerts for active-trip GPS capture gaps.
--
-- Rollback plan:
-- Stop the route-gap monitor, then drop its policies, indexes, and table.
-- This table deliberately contains no coordinate columns; removing it loses
-- operational audit history but does not require scrubbing location geometry.

CREATE TABLE IF NOT EXISTS public.ride_location_gap_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ride_id uuid NOT NULL REFERENCES public.rides(id) ON DELETE CASCADE,
    driver_id uuid REFERENCES public.drivers(id) ON DELETE SET NULL,
    gap_started_at timestamptz NOT NULL,
    detected_at timestamptz NOT NULL DEFAULT now(),
    gap_resolved_at timestamptz,
    threshold_seconds integer NOT NULL,
    gap_seconds integer NOT NULL,
    status text NOT NULL DEFAULT 'open',
    source text NOT NULL DEFAULT 'active_trip_monitor',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ride_location_gap_events_status_valid
        CHECK (status IN ('open', 'resolved')) NOT VALID,
    CONSTRAINT ride_location_gap_events_threshold_positive
        CHECK (threshold_seconds > 0) NOT VALID,
    CONSTRAINT ride_location_gap_events_duration_nonnegative
        CHECK (gap_seconds >= 0) NOT VALID
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ride_location_gap_events_ride_start
    ON public.ride_location_gap_events(ride_id, gap_started_at);

CREATE INDEX IF NOT EXISTS idx_ride_location_gap_events_open_detected
    ON public.ride_location_gap_events(status, detected_at DESC);

ALTER TABLE public.ride_location_gap_events ENABLE ROW LEVEL SECURITY;

-- All reads and mutations travel through authenticated backend endpoints with
-- the service role. The browser must never query or mutate this audit stream
-- directly, even for an administrator.
CREATE POLICY "ride_location_gap_events_no_direct_select"
    ON public.ride_location_gap_events FOR SELECT TO authenticated
    USING (false);
CREATE POLICY "ride_location_gap_events_no_direct_insert"
    ON public.ride_location_gap_events FOR INSERT TO authenticated
    WITH CHECK (false);
CREATE POLICY "ride_location_gap_events_no_direct_update"
    ON public.ride_location_gap_events FOR UPDATE TO authenticated
    USING (false) WITH CHECK (false);
CREATE POLICY "ride_location_gap_events_no_direct_delete"
    ON public.ride_location_gap_events FOR DELETE TO authenticated
    USING (false);

COMMENT ON TABLE public.ride_location_gap_events IS
    'Timestamp-only active-trip GPS capture-gap audit. Contains no latitude or longitude.';

NOTIFY pgrst, 'reload schema';
