-- 242_ride_distance_recomputes.sql
-- Append-only audit of measured-distance recomputes performed by the route
-- finalizer when a delayed offline GPS tail batch arrives after completion.
-- Records old -> new stats values (actual_distance_km, phase_distances) so a
-- rider/driver dispute or SGI review can reconstruct exactly when and why a
-- displayed distance changed. Fare columns are never recomputed; this table
-- audits stats/display values only.
--
-- Rollback plan:
-- Disable the finalizer's stats-recompute step, then drop the policies,
-- index, and table. No other table references it and no fare data depends
-- on it; dropping loses recompute audit history only.

CREATE TABLE IF NOT EXISTS public.ride_distance_recomputes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- RESTRICT, not CASCADE: this is a regulatory dispute/SGI audit trail.
    -- rides rows are anonymized under right-to-delete, never hard-deleted;
    -- any future deletion attempt must fail loudly rather than silently
    -- destroying the audit history this table exists to preserve.
    ride_id text NOT NULL REFERENCES public.rides(id) ON DELETE RESTRICT,
    route_revision integer NOT NULL,
    previous_actual_distance_km numeric(8, 3),
    new_actual_distance_km numeric(8, 3) NOT NULL,
    previous_phase_distances jsonb,
    new_phase_distances jsonb,
    trigger text NOT NULL DEFAULT 'late_tail_refinalization',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ride_distance_recomputes_new_distance_nonnegative
        CHECK (new_actual_distance_km >= 0)
);

CREATE INDEX IF NOT EXISTS idx_ride_distance_recomputes_ride
    ON public.ride_distance_recomputes(ride_id, created_at DESC);

ALTER TABLE public.ride_distance_recomputes ENABLE ROW LEVEL SECURITY;

-- Append-only, service-role only: the backend finalizer is the single writer
-- and admin endpoints are the only readers. The browser (even an admin's)
-- must never query or mutate this audit stream directly. No UPDATE/DELETE
-- policy exists on purpose — audit rows are immutable.
CREATE POLICY "ride_distance_recomputes_no_direct_select"
    ON public.ride_distance_recomputes FOR SELECT TO authenticated
    USING (false);
CREATE POLICY "ride_distance_recomputes_no_direct_insert"
    ON public.ride_distance_recomputes FOR INSERT TO authenticated
    WITH CHECK (false);
CREATE POLICY "ride_distance_recomputes_no_direct_update"
    ON public.ride_distance_recomputes FOR UPDATE TO authenticated
    USING (false) WITH CHECK (false);
CREATE POLICY "ride_distance_recomputes_no_direct_delete"
    ON public.ride_distance_recomputes FOR DELETE TO authenticated
    USING (false);

COMMENT ON TABLE public.ride_distance_recomputes IS
    'Append-only audit of finalizer stats-distance recomputes (late GPS tail). Never affects fare columns.';

NOTIFY pgrst, 'reload schema';
