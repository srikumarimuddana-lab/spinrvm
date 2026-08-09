-- 288_driver_stripe_connect_ledger.sql
-- Purpose: store a driver's Stripe **connected-account** money movement in our
--          own tables, so the app can show bank payouts and a full ledger
--          without calling Stripe on every request.
--
--          `payouts` already holds what the PLATFORM sent each driver (Stripe
--          Transfers, platform → connected account). These two tables hold the
--          other half, which lives on the connected account itself and was
--          never captured:
--
--            driver_stripe_payouts  — Stripe `Payout` objects
--                                     (connected account → the driver's bank)
--            driver_stripe_ledger   — Stripe `BalanceTransaction` objects
--                                     (every credit/debit incl. fees, refunds,
--                                      payout failures, adjustments)
--
-- ─────────────────────────────────────────────────────────────────────────
-- MONEY SAFETY — READ BEFORE USING THESE TABLES
--
-- Neither table is an income record, and neither may EVER be summed into a
-- driver's T4A total or payable balance.
--
-- A single dollar earned appears in ALL THREE places as it moves:
--   1. `payouts`               — platform sent it to the driver's account
--   2. `driver_stripe_payouts` — the account sent it on to their bank
--   3. `driver_stripe_ledger`  — one row per leg, plus fees
--
-- These are the same dollar, not three dollars. `routes/drivers/tax_exports.py`
-- computes T4A from completed rides + `payouts.payout_type='stripe_sync'`
-- ONLY, and `routes/drivers/earnings.py` derives payable balance from rides.
-- Adding either table to those sums would over-report a driver's income to the
-- CRA. They exist for display and reconciliation.
--
-- `driver_stripe_ledger.amount` is SIGNED (Stripe's convention: credits
-- positive, debits negative) precisely so it can never be naively summed as
-- income — a correct sum over a period nets to the account balance change.
-- ─────────────────────────────────────────────────────────────────────────
--
-- Rollback:
--   DROP TABLE IF EXISTS driver_stripe_ledger;
--   DROP TABLE IF EXISTS driver_stripe_payouts;
--
-- Notes:
-- - Primary key is the Stripe object id, so the sync is idempotent by
--   construction: re-running upserts the same row rather than duplicating.
--   Stripe object ids are globally unique, so rows sourced from a driver's
--   current AND superseded accounts cannot collide.
-- - `stripe_account_id` is recorded per row (not just joined off `drivers`)
--   because a driver's account can be superseded — the row must remember which
--   account it actually came from for reconciliation to be possible.
-- - Money is NUMERIC(12,2); the sync converts Stripe's integer cents with
--   utils.money.cents_to_dollars (Decimal only, never float).
-- - RLS enabled with no policies: the service role bypasses it and the anon
--   key must never read financial records directly (CLAUDE.md).
-- - No raw Stripe payload column: only the named fields below are stored, so
--   an unexpected Stripe field cannot silently land PII in our database.

CREATE TABLE IF NOT EXISTS driver_stripe_payouts (
    -- Stripe payout id (po_…)
    id                  TEXT PRIMARY KEY,
    driver_id           TEXT NOT NULL,
    -- The connected account this payout was read from — current or superseded.
    stripe_account_id   TEXT NOT NULL,
    amount              NUMERIC(12,2) NOT NULL,
    currency            TEXT NOT NULL DEFAULT 'cad',
    -- Stripe: paid | pending | in_transit | canceled | failed
    status              TEXT NOT NULL,
    -- standard | instant
    method              TEXT,
    -- When the money is expected to land in / did land in the bank.
    arrival_date        TIMESTAMPTZ,
    failure_code        TEXT,
    failure_message     TEXT,
    -- Last 4 of the destination bank account. Same data class as the existing
    -- payouts.account_last4; never the full account number.
    bank_last4          TEXT,
    -- Stripe's created timestamp, NOT our insert time — this is the ordering
    -- the driver sees, and it must survive a re-sync unchanged.
    created_at          TIMESTAMPTZ NOT NULL,
    synced_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS driver_stripe_ledger (
    -- Stripe balance transaction id (txn_…)
    id                  TEXT PRIMARY KEY,
    driver_id           TEXT NOT NULL,
    stripe_account_id   TEXT NOT NULL,
    -- Stripe: payout | transfer | payout_failure | payout_cancel | stripe_fee |
    -- adjustment | refund | … (open set — stored verbatim, never enumerated
    -- in a CHECK, so a new Stripe type cannot break the sync).
    type                TEXT NOT NULL,
    -- SIGNED gross, in dollars. Credits positive, debits negative.
    amount              NUMERIC(12,2) NOT NULL,
    fee                 NUMERIC(12,2) NOT NULL DEFAULT 0,
    -- SIGNED net (amount - fee, per Stripe).
    net                 NUMERIC(12,2) NOT NULL,
    currency            TEXT NOT NULL DEFAULT 'cad',
    status              TEXT,
    -- The object that caused this entry (tr_…, po_…, …), for joining back.
    source              TEXT,
    description         TEXT,
    available_on        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL,
    synced_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Query pattern: the driver's own history screen, newest first.
CREATE INDEX IF NOT EXISTS driver_stripe_payouts_driver_created_idx
    ON driver_stripe_payouts (driver_id, created_at DESC);

CREATE INDEX IF NOT EXISTS driver_stripe_ledger_driver_created_idx
    ON driver_stripe_ledger (driver_id, created_at DESC);

-- Query pattern: reconciliation against one connected account (incl. a
-- superseded one, after a platform migration or a key-mode retire).
CREATE INDEX IF NOT EXISTS driver_stripe_payouts_account_idx
    ON driver_stripe_payouts (stripe_account_id);

CREATE INDEX IF NOT EXISTS driver_stripe_ledger_account_idx
    ON driver_stripe_ledger (stripe_account_id);

-- Query pattern: "show me only real payouts / only fees" within a driver.
CREATE INDEX IF NOT EXISTS driver_stripe_ledger_driver_type_idx
    ON driver_stripe_ledger (driver_id, type);

ALTER TABLE driver_stripe_payouts ENABLE ROW LEVEL SECURITY;
ALTER TABLE driver_stripe_ledger  ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE driver_stripe_payouts IS
    'Stripe Payout objects read from each driver''s connected account (account → bank). '
    'Display/reconciliation only — NEVER sum into T4A income or payable balance; the same '
    'money is already counted as a Transfer in `payouts`. See migration 288 header.';

COMMENT ON TABLE driver_stripe_ledger IS
    'Stripe BalanceTransaction objects from each driver''s connected account — the full '
    'signed ledger (credits +, debits -) including fees, refunds and payout failures. '
    'Display/reconciliation only — NEVER sum into T4A income. See migration 288 header.';
