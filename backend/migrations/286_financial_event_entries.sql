-- 286_financial_event_entries.sql
-- Double-entry leg table for the financial_events ledger.
--
-- Context: financial_events (migration 58) is a SINGLE-entry ledger — one row
-- per money movement carrying a signed delta_cents. It satisfies CRA/SK 7-year
-- record-keeping, but it cannot be balanced: there is no contra-account, so
-- you cannot prove the ledger nets to zero, and you cannot answer "how much do
-- we owe drivers vs. owe CRA vs. keep as platform revenue" without re-deriving
-- it from the rides table.
--
-- This migration adds the missing half as a CHILD table rather than by
-- altering financial_events, deliberately:
--
--   utils/reconciliation.py::_sum_financial_events SUMS delta_cents filtered
--   by event_type. Adding contra rows INTO financial_events would make that
--   sum cancel to ~zero and break the daily Stripe reconciliation. A separate
--   table has zero blast radius on every existing reader.
--
-- financial_events therefore becomes the journal HEADER (what happened, how
-- much moved, external ref) and financial_event_entries holds the balanced
-- DEBIT/CREDIT legs (which internal accounts moved).
--
-- Rollback: set app_settings.ledger_double_entry_enabled = false; if the table
-- must go, DROP VIEW financial_event_entries_unbalanced; DROP TABLE
-- financial_event_entries; DROP FUNCTION _financial_event_entries_immutable().
--
-- Rollback plan (no second deploy needed — writes are behind the
-- `ledger_double_entry_enabled` app_settings flag, default false):
--   1. Set app_settings.ledger_double_entry_enabled = false  (stops all writes)
--   2. If the table itself must go:
--        DROP VIEW  IF EXISTS financial_event_entries_unbalanced;
--        DROP TABLE IF EXISTS financial_event_entries;
--        DROP FUNCTION IF EXISTS _financial_event_entries_immutable();
--   Dropping is safe: nothing reads these rows to make a money decision.
--   financial_events is untouched by this migration.
--
-- Forward-compatible: new table + new view only. No existing table altered,
-- no existing column repurposed, no backfill. Safe to run against live traffic.

CREATE TABLE IF NOT EXISTS financial_event_entries (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Journal header. CASCADE so the 7-year DSAR hard delete
    -- (purge_pii_retention, migration 216) takes the legs with it rather than
    -- orphaning them or hitting an FK RESTRICT.
    event_id     uuid        NOT NULL REFERENCES financial_events(id) ON DELETE CASCADE,
    -- Chart of accounts. CHECK-constrained on purpose: adding an account is an
    -- accounting decision that should require a migration, not a typo in a
    -- string literal. The application validates the same set before insert.
    account      text        NOT NULL
                             CHECK (account IN (
                                 -- Asset: money in transit from the processor
                                 'stripe_receivable',
                                 -- Liability: owed to the driver (0% commission + tip)
                                 'driver_payable',
                                 -- Liability: GST/PST owed to CRA / SK
                                 'tax_payable',
                                 -- Revenue: booking fee, airport fee, residual
                                 'platform_revenue',
                                 -- Liability: rider stored value
                                 'rider_wallet',
                                 -- Liability: corporate stored value
                                 'corporate_wallet',
                                 -- Contra-revenue: promos / discounts absorbed
                                 'promo_expense'
                             )),
    side         text        NOT NULL CHECK (side IN ('debit', 'credit')),
    -- ALWAYS POSITIVE. Direction is carried by `side`, never by the sign —
    -- this is what makes SUM(debit) = SUM(credit) a meaningful assertion.
    -- Contrast with financial_events.delta_cents, which IS signed.
    amount_cents bigint      NOT NULL CHECK (amount_cents > 0),
    currency     text        NOT NULL DEFAULT 'CAD',
    created_at   timestamptz NOT NULL DEFAULT now(),

    -- Idempotency for the leg write. record_payment_event retries on failure;
    -- without this a partial-then-retried insert would duplicate legs and the
    -- event would stop balancing. One (account, side) pair per event — two
    -- amounts hitting the same account are aggregated by the caller.
    CONSTRAINT financial_event_entries_uniq UNIQUE (event_id, account, side)
);

-- Query patterns:
--   "All legs for this event" (journal expansion, dispute investigation) needs
--   NO dedicated index: the UNIQUE constraint above creates a B-tree on
--   (event_id, account, side), and event_id is its leftmost prefix. Adding a
--   second index on (event_id) alone would be pure write overhead on a table
--   that sits in the settlement path.
--
--   "Account balance over a date window" (trial balance, finance rollup)
CREATE INDEX IF NOT EXISTS financial_event_entries_account_created
    ON financial_event_entries (account, created_at DESC);

-- Trial-balance check surface. An event whose legs do not net to zero is an
-- accounting defect; the daily reconciliation loop alerts on any row here.
-- Events with NO legs at all are intentionally absent (that is the flag-off
-- case and the "legs write failed" case, both detected header-side instead).
CREATE OR REPLACE VIEW financial_event_entries_unbalanced AS
SELECT
    event_id,
    SUM(CASE WHEN side = 'debit'  THEN amount_cents ELSE 0 END) AS debit_cents,
    SUM(CASE WHEN side = 'credit' THEN amount_cents ELSE 0 END) AS credit_cents,
    SUM(CASE WHEN side = 'debit'  THEN amount_cents ELSE -amount_cents END) AS imbalance_cents,
    MIN(created_at) AS created_at
FROM financial_event_entries
GROUP BY event_id
HAVING SUM(CASE WHEN side = 'debit' THEN amount_cents ELSE -amount_cents END) <> 0;

-- Append-only RLS, mirroring financial_events (migration 58).
ALTER TABLE financial_event_entries ENABLE ROW LEVEL SECURITY;

-- Backend service role bypasses RLS by design; the anon key never writes here.
CREATE POLICY financial_event_entries_insert ON financial_event_entries
    FOR INSERT WITH CHECK (true);

-- Internal accounting detail — admin only. Riders/drivers see their own money
-- through financial_events, which already carries a per-user SELECT policy.
-- These legs have no user_id column and are not a customer-facing surface.
CREATE POLICY financial_event_entries_select ON financial_event_entries
    FOR SELECT USING (
        (SELECT role FROM users WHERE id = auth.uid()::text) = 'admin'
    );

-- No UPDATE or DELETE policies → denied by default.

-- RLS POLICIES ARE NOT ENOUGH — the table-level GRANTs must be revoked too.
-- Supabase grants anon/authenticated default CRUD on new public-schema tables,
-- and the INSERT policy above is WITH CHECK (true) with no TO clause, so it
-- applies to PUBLIC. Without these REVOKEs any holder of the publishable anon
-- key (shipped in rider-app/driver-app) could POST to
-- /rest/v1/financial_event_entries and inject arbitrary legs against any
-- event_id — including a self-balancing debit/credit pair, which would sail
-- straight past financial_event_entries_unbalanced, the very tamper-evidence
-- control this table exists to provide.
--
-- Exact pattern from migration 142 (which had to retrofit this onto disputes +
-- nine corporate money tables) and migration 151, whose comment states it
-- plainly: "any authenticated anon-key JWT could INSERT/UPDATE/DELETE payment
-- rows" without it. The backend writes through service_role, which bypasses
-- both RLS and these grants, so nothing legitimate is affected.
REVOKE ALL ON financial_event_entries FROM anon;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON financial_event_entries FROM authenticated;
GRANT  SELECT ON financial_event_entries TO authenticated;

-- The trial-balance view is admin-only by intent. A view executes with its
-- OWNER's privileges for RLS purposes unless created WITH (security_invoker),
-- and the migration runner owns it — so the base table's RLS does NOT protect
-- it. Revoke explicitly rather than relying on that.
REVOKE ALL ON financial_event_entries_unbalanced FROM anon, authenticated;

-- Tamper-evidence trigger, matching financial_events_no_mutate — but scoped to
-- UPDATE only, NOT DELETE.
--
-- Deliberate divergence from the parent: a BEFORE DELETE row trigger fires for
-- cascade deletes too, so blocking DELETE here would make ON DELETE CASCADE
-- raise and abort the 7-year DSAR purge. Legs must be removable alongside
-- their header. Direct DELETE is already denied by RLS (no DELETE policy).
CREATE OR REPLACE FUNCTION _financial_event_entries_immutable()
RETURNS trigger LANGUAGE plpgsql AS
$$
BEGIN
    RAISE EXCEPTION
        'financial_event_entries rows are append-only and cannot be modified';
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'financial_event_entries_no_update'
          AND tgrelid = 'financial_event_entries'::regclass
    ) THEN
        CREATE TRIGGER financial_event_entries_no_update
            BEFORE UPDATE ON financial_event_entries
            FOR EACH ROW EXECUTE FUNCTION _financial_event_entries_immutable();
    END IF;
END;
$$;

COMMENT ON TABLE financial_event_entries IS
    'Double-entry legs for financial_events. amount_cents is always positive; '
    'direction is carried by side (debit/credit). SUM(debit) must equal '
    'SUM(credit) per event_id — see financial_event_entries_unbalanced. '
    'Append-only (UPDATE blocked by trigger; DELETE only via parent CASCADE). '
    'Written behind the ledger_double_entry_enabled app_settings flag. '
    'NEVER AUTHORITATIVE FOR GST/PST REMITTANCE: rides.tax_breakdown is the '
    'source of truth (routes/admin/compliance.py, and the same field the rider '
    'receipt renders from). The tax_payable leg here is an internal bookkeeping '
    'view and reads as understated for any event the projection could not '
    'decompose (it books those whole to platform_revenue and flags '
    'spinr_alert=ledger_legs_degraded). Do not build a tax report on this table. '
    'Created in migration 286.';

COMMENT ON VIEW financial_event_entries_unbalanced IS
    'Events whose debit and credit legs do not net to zero. Should always be '
    'empty; the daily reconciliation loop alerts on any row.';
