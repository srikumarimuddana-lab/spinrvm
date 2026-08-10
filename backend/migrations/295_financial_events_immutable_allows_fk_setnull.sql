-- 295_financial_events_immutable_allows_fk_setnull.sql
-- Corrects migration 294 (ACTION_ITEMS.md B17, PR #3510): the `ON DELETE SET
-- NULL` FK action it added does not actually work against this table, and
-- Step B would still abort -- just with a different error.
--
-- The bug: financial_events_no_mutate (migration 58, still current as of
-- 294 -- 294 only touched the FK constraint, not the trigger) is a BEFORE
-- UPDATE FOR EACH ROW trigger that unconditionally RAISEs on any UPDATE
-- (migration 289 only carved out an exception for DELETE, gated by
-- spinr.financial_events.allow_delete -- UPDATE stayed fully blocked).
--
-- PostgreSQL implements FK referential actions (CASCADE, SET NULL, SET
-- DEFAULT) by executing an ordinary UPDATE/DELETE against the referencing
-- table through the normal executor -- which fires that table's own
-- row-level BEFORE/AFTER triggers exactly as if the statement had been
-- issued directly. This is documented PostgreSQL behavior, not an edge
-- case: "a cascading action is propagated by triggers ... any additional
-- constraints, rules, or triggers [on the referencing table] apply to it
-- exactly as they would to a normal UPDATE/DELETE". There is no built-in
-- way to make a referential action bypass a user-defined trigger short of
-- disabling the trigger outright (ALTER TABLE ... DISABLE TRIGGER, which
-- would also disable the DELETE-gate from 289) or setting
-- session_replication_role = 'replica' for the whole session (which the
-- purge does not do, and should not do -- that would also silence the
-- DELETE gate's protection).
--
-- Net effect: when Step B's `DELETE FROM rides` reaches a ride still
-- referenced by a financial_events row, Postgres's own SET NULL action
-- tries to UPDATE that financial_events row's ride_id to NULL as part of
-- executing the DELETE -- and financial_events_no_mutate's BEFORE UPDATE
-- trigger fires for that internal update and unconditionally raises. Step B
-- still aborts. The failure mode changed (a P0001 raised by the trigger,
-- surfacing from inside the DELETE FROM rides statement, instead of a raw
-- 23503 foreign_key_violation) but the daily purge still stops at Step B
-- the first time a paid ride crosses 7 years -- B17 is not actually fixed
-- by 294 alone. Not caught by 294's own test
-- (test_financial_events_ride_id_fk_contract.py) because that suite is
-- purely textual (no live Postgres in CI, same constraint every migration
-- test in this repo works under) and only pins the FK's ON DELETE clause,
-- not its interaction with the immutability trigger.
--
-- Fix: extend _financial_events_immutable() with a narrow, unconditional
-- (no GUC needed -- see why below) UPDATE allowance: permit an UPDATE only
-- when every column except ride_id is unchanged and the new ride_id is
-- NULL. This is deliberately not gated behind a transaction-local GUC the
-- way migration 294's failed sibling attempt in a parallel session used
-- (spinr.financial_events.allow_ride_unlink) -- a GUC only helps when the
-- caller can set it immediately before the mutating statement, and here the
-- mutating UPDATE is issued internally by Postgres's own FK machinery as
-- part of executing `DELETE FROM rides`, with no opportunity for
-- application code (or this function) to set a session GUC first. The
-- column-pinning itself is the safety boundary instead: the allowed shape
-- (only ride_id changing, and only to NULL) cannot be used to tamper with
-- event_type, user_id, delta_cents, ref, metadata, or created_at -- the
-- fields that actually matter for the CRA/SOC2 ledger's integrity -- so
-- permitting it unconditionally does not reopen the append-only guarantee
-- this table exists for.
--
-- Blast radius: only the UPDATE branch of _financial_events_immutable()
-- changes. The DELETE branch (migration 289's GUC gate, used by Step H) is
-- untouched. financial_events_ride_id_fkey's ON DELETE SET NULL action
-- (294) is untouched -- this migration only makes it actually work.
--
-- Rollback: CREATE OR REPLACE _financial_events_immutable() back to
-- migration 289's verbatim body -- no data or schema to unwind. (Doing so
-- re-reintroduces the bug this migration fixes.)
--
-- Forward-compatible: CREATE OR REPLACE of an existing trigger function
-- only. No table lock beyond the function catalog swap; no window where
-- the append-only guarantee is weakened beyond this one narrow shape.
--
-- migration-override-ok: redefines _financial_events_immutable(), which
-- cannot be renamed (existing CREATE TRIGGER binding from migration 58 —
-- see migration 289's header for the same reasoning).

CREATE OR REPLACE FUNCTION _financial_events_immutable()
RETURNS trigger LANGUAGE plpgsql AS
$$
DECLARE
    v_allowed TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_allowed := current_setting('spinr.financial_events.allow_delete', true);
        IF v_allowed = 'true' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION
            'financial_events DELETE is reserved for purge_pii_retention() — direct DELETE is not permitted (attempted on row %)',
            OLD.id
            USING ERRCODE = 'check_violation';
    END IF;

    -- UPDATE: unconditionally permitted for exactly one shape -- nulling
    -- ride_id with every other column pinned unchanged. This is what
    -- financial_events_ride_id_fkey's ON DELETE SET NULL action (migration
    -- 294) needs to actually fire when Step B deletes an old ride; no GUC
    -- gate is possible here since Postgres's own FK machinery issues this
    -- UPDATE internally, not application code. Any other UPDATE shape still
    -- raises exactly as migration 58/289 shipped it.
    IF NEW.ride_id IS NULL
       AND OLD.ride_id IS NOT NULL
       AND NEW.id = OLD.id
       AND NEW.event_type = OLD.event_type
       AND NEW.user_id = OLD.user_id
       AND NEW.delta_cents = OLD.delta_cents
       AND NEW.ref IS NOT DISTINCT FROM OLD.ref
       AND NEW.metadata IS NOT DISTINCT FROM OLD.metadata
       AND NEW.created_at = OLD.created_at
    THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION
        'financial_events rows are append-only and cannot be modified';
END;
$$;

COMMENT ON TABLE financial_events IS
    'Append-only money ledger. UPDATE blocked by trigger financial_events_no_mutate '
    'except one narrow shape (migration 295) needed for financial_events_ride_id_fkey''s '
    'ON DELETE SET NULL action (migration 294, ACTION_ITEMS.md B17) to actually fire when '
    'Step B purges a 7-year-old ride: nulling ride_id with every other column pinned. '
    'DELETE blocked except inside purge_pii_retention() Step H (migration 289). Required '
    'by CRA record-keeping (7-year retention) and SOC2 CC9.1. Created in migration 58.';

NOTIFY pgrst, 'reload schema';
