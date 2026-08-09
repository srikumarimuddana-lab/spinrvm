-- 287_financial_events_missing_legs_rpc.sql
-- Work queue for the double-entry leg projection loop (utils/ledger_projection.py).
--
-- Context: financial_event_entries (migration 286) holds balanced debit/credit
-- legs for financial_events headers. Leg-writing is moving OUT of the payment
-- request path into a background projection loop, which needs "headers that
-- have no legs yet, oldest first". PostgREST cannot express an anti-join, and
-- the obvious alternative — a claim column on financial_events — is impossible:
-- migration 58's financial_events_no_mutate trigger raises on ANY UPDATE.
-- Hence this SECURITY DEFINER read-only RPC.
--
-- Two WHERE clauses are load-bearing, not optimizations:
--
--   * event_type IN ('stripe_charge', 'stripe_refund') — these are the only
--     types the projection knows how to decompose. Without the filter, every
--     wallet_topup / driver_payout / tax_adjust row would enter the queue and
--     sit at its head forever (the loop is oldest-first + LIMIT), starving all
--     newer projectable events. Extending projection to a new type means
--     adding it HERE and in utils/ledger_projection.py in the same change.
--
--   * created_at < now() - interval '30 minutes' — the settlement paths write
--     the financial_events header BEFORE update_ride applies the tip delta to
--     rides.driver_earnings (services/payment_service.py, both the capture-
--     hold and fresh-charge paths). Projecting inside that window would
--     decompose from a stale ride row and book the tip into platform_revenue.
--     30 minutes is generous headroom over that write gap (milliseconds in
--     practice, but crash-recovery paths can stretch it).
--
-- Rollback: DROP FUNCTION IF EXISTS financial_events_missing_legs(int);
--
-- Rollback plan:
--   DROP FUNCTION IF EXISTS financial_events_missing_legs(int);
--   Nothing else references it; the projection loop degrades to a logged
--   skip when the function is absent (partial-deploy guard in the caller).
--
-- Forward-compatible: new function only; no table altered, no backfill.

-- CREATE OR REPLACE cannot change a function's signature, and a same-name
-- overload with a different parameter list would silently coexist (see
-- migration 111's header for the incident writeup) — drop explicitly first.
DROP FUNCTION IF EXISTS financial_events_missing_legs(int);

CREATE OR REPLACE FUNCTION financial_events_missing_legs(p_limit int DEFAULT 200)
RETURNS SETOF financial_events
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT fe.*
    FROM financial_events fe
    WHERE NOT EXISTS (
              SELECT 1
              FROM financial_event_entries e
              WHERE e.event_id = fe.id
          )
      AND fe.event_type IN ('stripe_charge', 'stripe_refund')
      -- $0 headers (comp / fully-covered rides) have no money movement to
      -- decompose and the legs table CHECKs amount_cents > 0 — without this
      -- filter they would sit at the head of the oldest-first queue forever.
      AND fe.delta_cents <> 0
      AND fe.created_at < now() - interval '30 minutes'
    ORDER BY fe.created_at
    LIMIT LEAST(GREATEST(p_limit, 1), 500);
$$;

-- Read-only, but it bypasses financial_events RLS (SECURITY DEFINER), so the
-- backend service role must be its only caller. Migration-205 grant form:
-- revoking PUBLIC also strips service_role's inherited EXECUTE, so it must be
-- granted back explicitly.
REVOKE EXECUTE ON FUNCTION financial_events_missing_legs(int) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION financial_events_missing_legs(int) TO service_role;

COMMENT ON FUNCTION financial_events_missing_legs(int) IS
    'Oldest-first batch of financial_events headers with no double-entry legs '
    'yet, restricted to projectable event types and rows older than 30 min '
    '(the header-before-tip-delta write gap). Work queue for the '
    'ledger_projection background loop. Service-role only. Migration 287.';
