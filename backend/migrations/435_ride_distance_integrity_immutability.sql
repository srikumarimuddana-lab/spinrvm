-- 435_ride_distance_integrity_immutability.sql
-- ACTION_ITEMS.md C118: `ride_distance_integrity_events` (migration 246) and
-- `ride_distance_recomputes` (242) both claim "event/audit rows are
-- immutable" in their own migration comments, but neither table has a
-- DB-level trigger enforcing it. RLS's explicit `USING (false)` policies
-- already block UPDATE/DELETE for anon/authenticated, but `service_role` —
-- the only role able to write to either table at all — bypasses RLS by
-- design (see root CLAUDE.md's "Query filters" / service-role convention)
-- and can currently UPDATE or DELETE a row directly at the DB layer. This
-- was proven, not assumed, by
-- backend/tests/rls/test_ride_distance_integrity_rls.py's
-- `test_integrity_event_service_role_can_mutate_despite_immutable_comment`
-- and `test_recompute_service_role_can_mutate_despite_immutable_comment`
-- (both updated by this same change to assert the now-blocked behavior).
--
-- Confirmed via grep across backend/ before writing this: no production code
-- path (routes, services, background loops, retention_purge.py) ever issues
-- an UPDATE or DELETE against either table — both are pure INSERT-and-read
-- streams. This is therefore unlike `audit_logs` (migrations 51/56), which
-- needed a session-flag-gated DELETE trigger because it has a legitimate 7y
-- retention-purge DELETE path: no such path exists here, so an unconditional
-- block on both UPDATE and DELETE is correct with no carve-out needed.
--
-- One shared trigger function backs all four triggers (UPDATE+DELETE across
-- both tables) rather than four near-identical per-table functions —
-- TG_TABLE_NAME/TG_OP already give the specifics for the error message, so a
-- dedicated function per table (the audit_logs precedent) would be pure
-- duplication here.
--
-- Rollback: DROP TRIGGER on each of the 4 triggers below, then
-- DROP FUNCTION public.block_mutation_on_immutable_table(). No data is
-- touched by rollback — this migration adds enforcement only, no columns,
-- no backfill.
--
-- Forward-compatible: adding a BEFORE trigger takes a brief ACCESS EXCLUSIVE
-- catalog lock per table (not a row rewrite/scan), and neither table is on a
-- hot request path (write-and-forget signal/audit inserts only) — safe to
-- run against production traffic in flight.

CREATE OR REPLACE FUNCTION public.block_mutation_on_immutable_table()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        '% is append-only — % is not permitted (attempted on row %)',
        TG_TABLE_NAME, TG_OP, OLD.id
        USING ERRCODE = 'check_violation';
END;
$$;

COMMENT ON FUNCTION public.block_mutation_on_immutable_table() IS
    'Generic BEFORE UPDATE/DELETE guard for tables whose own migration comment claims immutability. Fires regardless of role (including service_role, which bypasses RLS) — see migration 435.';

DROP TRIGGER IF EXISTS ride_distance_integrity_events_no_update ON public.ride_distance_integrity_events;
CREATE TRIGGER ride_distance_integrity_events_no_update
    BEFORE UPDATE ON public.ride_distance_integrity_events
    FOR EACH ROW
    EXECUTE FUNCTION public.block_mutation_on_immutable_table();

DROP TRIGGER IF EXISTS ride_distance_integrity_events_no_delete ON public.ride_distance_integrity_events;
CREATE TRIGGER ride_distance_integrity_events_no_delete
    BEFORE DELETE ON public.ride_distance_integrity_events
    FOR EACH ROW
    EXECUTE FUNCTION public.block_mutation_on_immutable_table();

DROP TRIGGER IF EXISTS ride_distance_recomputes_no_update ON public.ride_distance_recomputes;
CREATE TRIGGER ride_distance_recomputes_no_update
    BEFORE UPDATE ON public.ride_distance_recomputes
    FOR EACH ROW
    EXECUTE FUNCTION public.block_mutation_on_immutable_table();

DROP TRIGGER IF EXISTS ride_distance_recomputes_no_delete ON public.ride_distance_recomputes;
CREATE TRIGGER ride_distance_recomputes_no_delete
    BEFORE DELETE ON public.ride_distance_recomputes
    FOR EACH ROW
    EXECUTE FUNCTION public.block_mutation_on_immutable_table();

COMMENT ON TABLE public.ride_distance_integrity_events IS
    'Append-only distance/route integrity signals (detection only; never affects fare or displayed distance). UPDATE/DELETE blocked for every role by triggers ride_distance_integrity_events_no_update/_no_delete (migration 435).';

COMMENT ON TABLE public.ride_distance_recomputes IS
    'Append-only audit of finalizer stats-distance recomputes (late GPS tail). Never affects fare columns. UPDATE/DELETE blocked for every role by triggers ride_distance_recomputes_no_update/_no_delete (migration 435).';

NOTIFY pgrst, 'reload schema';
