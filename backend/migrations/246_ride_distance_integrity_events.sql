-- 246_ride_distance_integrity_events.sql
-- Append-only stream of distance/route integrity signals raised across the ride
-- lifecycle: a suspiciously small booked distance, a completion whose measured
-- distance fell back to the booked estimate, a measured distance below the
-- physical straight-line floor, a compressed trip window, and a nightly
-- quote-vs-measured divergence. These are DETECTION signals for ops + the
-- reconciliation job — they never change a fare or a displayed distance.
--
-- Rollback:
-- Stop the writers (booking guard, completion milestone check, reconciliation
-- loop), then drop the policies, indexes, and table. No other table references
-- it and no fare/display data depends on it; dropping loses integrity-signal
-- history only.

CREATE TABLE IF NOT EXISTS public.ride_distance_integrity_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- RESTRICT, not CASCADE: rides rows are anonymized under right-to-delete,
    -- never hard-deleted, so a deletion attempt must fail loudly rather than
    -- silently destroying this signal history.
    ride_id text NOT NULL REFERENCES public.rides(id) ON DELETE RESTRICT,
    kind text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ride_distance_integrity_events_kind_known CHECK (
        kind IN (
            'booked_distance_suspect',
            'coverage_fallback',
            'measured_below_straight_line',
            'trip_window_compressed',
            'quote_measured_divergence'
        )
    )
);

-- (ride_id, created_at) for per-ride lookups (incident tooling); (kind,
-- created_at) for the ops query pattern "recent events of type X".
CREATE INDEX IF NOT EXISTS idx_ride_distance_integrity_events_ride
    ON public.ride_distance_integrity_events(ride_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ride_distance_integrity_events_kind
    ON public.ride_distance_integrity_events(kind, created_at DESC);

ALTER TABLE public.ride_distance_integrity_events ENABLE ROW LEVEL SECURITY;

-- Append-only, service-role only: the backend is the single writer and admin
-- endpoints are the only readers. The browser (even an admin's) must never
-- query or mutate this stream directly. No UPDATE/DELETE policy exists on
-- purpose — event rows are immutable.
CREATE POLICY "ride_distance_integrity_events_no_direct_select"
    ON public.ride_distance_integrity_events FOR SELECT TO authenticated
    USING (false);
CREATE POLICY "ride_distance_integrity_events_no_direct_insert"
    ON public.ride_distance_integrity_events FOR INSERT TO authenticated
    WITH CHECK (false);
CREATE POLICY "ride_distance_integrity_events_no_direct_update"
    ON public.ride_distance_integrity_events FOR UPDATE TO authenticated
    USING (false) WITH CHECK (false);
CREATE POLICY "ride_distance_integrity_events_no_direct_delete"
    ON public.ride_distance_integrity_events FOR DELETE TO authenticated
    USING (false);

COMMENT ON TABLE public.ride_distance_integrity_events IS
    'Append-only distance/route integrity signals (detection only; never affects fare or displayed distance).';

NOTIFY pgrst, 'reload schema';
