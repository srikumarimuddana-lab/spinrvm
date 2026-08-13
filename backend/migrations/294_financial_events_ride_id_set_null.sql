-- Migration 294: financial_events.ride_id → ON DELETE SET NULL
--
-- Rollback:
--   ALTER TABLE public.financial_events
--       DROP CONSTRAINT IF EXISTS financial_events_ride_id_fkey;
--   ALTER TABLE public.financial_events
--       ADD CONSTRAINT financial_events_ride_id_fkey
--       FOREIGN KEY (ride_id) REFERENCES public.rides(id);
--
-- Closes ACTION_ITEMS.md B17. Migration 58 created financial_events.ride_id
-- as a plain (NO ACTION) FK to rides(id). purge_pii_retention() Step B runs
-- `DELETE FROM rides WHERE created_at < now() - 7y` with NO exception handler
-- at all (contrast Step H, migration 216/289, which isolates per-account with
-- `EXCEPTION WHEN foreign_key_violation`). Every paid ride has a retained
-- financial_events row pointing at it via ride_id, and no purge step ever
-- deletes non-DSAR financial_events rows — so the first ride to cross 7 years
-- raises foreign_key_violation, aborts the whole transaction, and rolls back
-- every earlier step in the same run (Step A's GPS anonymization included),
-- repeating on every subsequent daily run. Dormant only because no ride has
-- reached 7 years old yet; certain to fire on the passage of time alone, no
-- user action required.
--
-- Same shape of bug as migration 273 (driver_statements.driver_id), same
-- fix shape: give the referencing FK an ON DELETE action instead of adding
-- exception handling around Step B, so the purge continues to converge
-- automatically for every future 7-year rollover, not just the ones an
-- exception handler happens to catch.
--
-- Why SET NULL and not CASCADE (the driver_statements precedent) or per-row
-- exception isolation (the Step H precedent):
--   - financial_events is the CRA/SOC2 7-year money ledger (migration 58's
--     own header: "Required by CRA record-keeping and SOC2 CC9.1"). It must
--     independently survive its ride being purged — CASCADE would delete the
--     tax record itself, which is the one thing this table exists to retain.
--   - The CRA retention requirement is for the transaction record (amount,
--     tax split, event type, date) — not for the ride linkage. By the time a
--     ride is 7 years old it has already had Step A's GPS anonymization (3y)
--     applied for 4 years; losing the ride_id back-link on financial_events
--     at the same 7-year mark is a materially smaller loss than losing the
--     transaction record entirely, and matches the existing precedent of
--     Step L anonymizing (not deleting) price_searches.user_id rather than
--     blocking on it.
--   - Per-row exception isolation (Step H's pattern) was considered and
--     rejected: it would leave every paid ride's row permanently un-purgeable
--     (violation-skip forever, same failure Step H itself had before 289
--     fixed it), silently exempting exactly the rides with payment history
--     from the 7-year deletion guarantee — the opposite of what B17 needs.
--
-- Blast radius checked (grep across backend/ for every reader of
-- financial_events.ride_id): utils/ledger_projection.py is the only reader
-- that joins on it (`{e["ride_id"] for e in events if e.get("ride_id")}`,
-- `rides_by_id.get(event.get("ride_id"))`) — already None-safe, since
-- non-ride event types (wallet_topup, driver_payout) never had a ride_id in
-- the first place and _decompose already has to handle that case. In
-- practice this FK only fires on financial_events rows attached to rides
-- that are themselves 7+ years old, which are long past any active
-- projection window. services/ledger_service.py reads ride_id only for a log
-- message at write time, never reads it back. No other module queries
-- financial_events filtered or joined by ride_id.
--
-- Written as DROP + ADD, resolving the constraint by column rather than
-- assuming Postgres' default name (mirrors migration 273 exactly), so this
-- converges regardless of what a given environment's constraint happens to
-- be named.

DO $$
DECLARE
    v_constraint_name text;
BEGIN
    SELECT con.conname INTO v_constraint_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
    WHERE nsp.nspname = 'public'
      AND rel.relname = 'financial_events'
      AND con.contype = 'f'
      AND con.conkey = ARRAY[
          (SELECT attnum FROM pg_attribute
           WHERE attrelid = rel.oid AND attname = 'ride_id')
      ]::smallint[]
    LIMIT 1;

    IF v_constraint_name IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE public.financial_events DROP CONSTRAINT %I',
            v_constraint_name
        );
    END IF;

    ALTER TABLE public.financial_events
        ADD CONSTRAINT financial_events_ride_id_fkey
        FOREIGN KEY (ride_id) REFERENCES public.rides(id) ON DELETE SET NULL;
END $$;

COMMENT ON CONSTRAINT financial_events_ride_id_fkey ON public.financial_events IS
    'ON DELETE SET NULL: financial_events is the 7-year CRA/SOC2 money ledger and must survive its ride being purged (migration 216/289 Step B, ACTION_ITEMS.md B17). Only the ride_id back-link is lost, never the transaction record itself.';

NOTIFY pgrst, 'reload schema';
