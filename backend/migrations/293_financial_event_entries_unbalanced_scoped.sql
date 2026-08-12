-- 293_financial_event_entries_unbalanced_scoped.sql
--
-- Date-scoped trial-balance check for the double-entry legs.
--
-- Context: financial_event_entries_unbalanced (migration 286) is the
-- tamper-evidence surface — an event whose debit and credit legs do not net to
-- zero is an accounting defect, and the daily reconciliation loop alerts on any
-- row. It is expected to be permanently empty.
--
-- The problem: the view exposes MIN(created_at) AS created_at. That is an
-- AGGREGATE OUTPUT, not a grouping key, so a caller filtering
--     SELECT ... FROM financial_event_entries_unbalanced
--      WHERE created_at >= :day_start AND created_at < :day_end
-- cannot have that predicate pushed below the GROUP BY. Postgres must compute
-- the full GROUP BY event_id + HAVING over the ENTIRE table and only then
-- discard everything outside the day. At the projected volume (~19k events/day
-- at ~3 legs each) that reaches ~20M rows within a year — re-aggregated every
-- night to produce the zero rows it is expected to produce.
--
-- (event_id IS a grouping key, so a caller filtering the view on event_id
-- WOULD get pushdown. That is not the shape the daily check needs.)
--
-- This function moves the date predicate inside the aggregate, where it can be
-- pushed down, and supports it with migration 292's created_at index.
--
-- Why the IN-subquery rather than a plain WHERE on the outer aggregate:
-- write_legs stamps every leg of one event with a single Python-computed
-- timestamp and inserts them in ONE batch, so an event's legs always share an
-- exact created_at and can never straddle a day boundary today. A bare
--     WHERE created_at >= p_start AND created_at < p_end
--     GROUP BY event_id HAVING ...
-- would therefore be correct — but it silently becomes WRONG if that ever
-- changes: a split batch would be aggregated in halves, and each half would
-- look unbalanced. That is a FALSE ALARM on the one control that is supposed
-- to mean "something is genuinely broken". Selecting the event ids from the
-- window and then aggregating ALL of each event's legs costs one extra index
-- lookup per event and is correct either way.
--
-- RETURNS SETOF the view (same idiom as migration 287's SETOF
-- financial_events) so the scoped and unscoped paths are column-identical:
-- an operator running an ad-hoc query against the view and the nightly job
-- running this function see exactly the same shape. Note SUM(bigint) yields
-- numeric in Postgres, which is why the columns are not cast to bigint — they
-- must match the view's types exactly.
--
-- Rollback: DROP FUNCTION IF EXISTS
--   financial_event_entries_unbalanced_between(timestamptz, timestamptz);
--
-- Rollback plan (no deploy needed):
--   1. DROP FUNCTION IF EXISTS financial_event_entries_unbalanced_between(timestamptz, timestamptz);
--   2. utils/reconciliation.py::_check_entry_balance degrades to a logged skip
--      when the function is absent (partial-deploy guard in the caller), so the
--      daily Stripe-vs-ledger reconciliation is unaffected. The view itself is
--      untouched and remains queryable by hand.
--
-- Forward-compatible: new function only. No table altered, no view redefined,
-- no backfill.

-- CREATE OR REPLACE cannot change a signature and a same-name overload with a
-- different parameter list would silently coexist (migration 111 incident) —
-- drop explicitly first.
DROP FUNCTION IF EXISTS financial_event_entries_unbalanced_between(timestamptz, timestamptz);

CREATE OR REPLACE FUNCTION financial_event_entries_unbalanced_between(
    p_start timestamptz,
    p_end   timestamptz
)
RETURNS SETOF financial_event_entries_unbalanced
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT
        e.event_id,
        SUM(CASE WHEN e.side = 'debit'  THEN e.amount_cents ELSE 0 END),
        SUM(CASE WHEN e.side = 'credit' THEN e.amount_cents ELSE 0 END),
        SUM(CASE WHEN e.side = 'debit'  THEN e.amount_cents ELSE -e.amount_cents END),
        MIN(e.created_at)
    FROM financial_event_entries e
    WHERE e.event_id IN (
              SELECT w.event_id
              FROM financial_event_entries w
              WHERE w.created_at >= p_start
                AND w.created_at <  p_end
          )
    GROUP BY e.event_id
    HAVING SUM(CASE WHEN e.side = 'debit' THEN e.amount_cents ELSE -e.amount_cents END) <> 0;
$$;

-- Read-only, but SECURITY DEFINER bypasses financial_event_entries' RLS, so
-- the backend service role must be its only caller — the same posture as the
-- view itself, whose grants migration 286 revoked from anon and authenticated.
-- Migration-205 grant form: revoking PUBLIC also strips service_role's
-- inherited EXECUTE, so it must be granted back explicitly.
REVOKE EXECUTE ON FUNCTION financial_event_entries_unbalanced_between(timestamptz, timestamptz)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION financial_event_entries_unbalanced_between(timestamptz, timestamptz)
    TO service_role;

COMMENT ON FUNCTION financial_event_entries_unbalanced_between(timestamptz, timestamptz) IS
    'Date-scoped financial_event_entries_unbalanced: journals whose debit and '
    'credit legs do not net to zero, restricted to events with legs written in '
    '[p_start, p_end). Exists because the view exposes MIN(created_at), an '
    'aggregate output that cannot be pushed below its GROUP BY — filtering the '
    'view by date aggregates the entire table. Should always return zero rows. '
    'Service-role only. Migration 293.';
