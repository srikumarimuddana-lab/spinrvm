-- Migration 272: driver_statements — periodic earnings-statement ledger
--
-- Rollback:
--   DROP TABLE IF EXISTS public.driver_statements;
--
-- One row per (driver, period) statement produced by the weekly/monthly
-- statement job (utils/driver_statement_job.py). The row is ALSO the job's
-- replay-safety claim: the job INSERTs the row (deterministic id
-- stmt-{period_type}-{period_start}-{driver_id}) BEFORE building/emailing,
-- so with the unique index below a second replica's insert for the same
-- (driver, period) violates and that replica skips — no duplicate emails.
-- (Background-loop recipe: claim-flag via insert idempotency.)
--
-- status lifecycle (terminal after the tick that claimed it):
--   claimed            row inserted, work in progress (crash leaves this;
--                      surfaced by admin listing, never auto-retried so a
--                      half-sent email can't double-send)
--   sent               email delivered to the driver (email_sent_at set)
--   failed             build/render/send raised (failure_reason set)
--   skipped_no_email   driver has no email address on file
--   skipped_inactive   no rides/bonuses/payouts in the period (no email sent;
--                      row still written so the period is complete and the
--                      job never rescans this driver+period)
--
-- totals holds the rendered statement's summary JSON (earnings breakdown +
-- payout totals) for the admin listing — the PDF itself is regenerated on
-- demand from live data, never stored.

CREATE TABLE IF NOT EXISTS public.driver_statements (
    id              TEXT PRIMARY KEY,
    driver_id       TEXT NOT NULL REFERENCES public.drivers(id),
    period_type     TEXT NOT NULL CHECK (period_type IN ('weekly', 'monthly')),
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    status          TEXT NOT NULL DEFAULT 'claimed',
    totals          JSONB,
    email_sent_at   TIMESTAMPTZ,
    failure_reason  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

-- The replay-safety claim: at most one statement per driver per period.
CREATE UNIQUE INDEX IF NOT EXISTS idx_driver_statements_claim
    ON public.driver_statements (driver_id, period_type, period_start);

-- Admin listing: newest statements for one driver.
CREATE INDEX IF NOT EXISTS idx_driver_statements_driver_created
    ON public.driver_statements (driver_id, created_at DESC);

-- Job fast-path: fetch the already-claimed driver set for one period.
CREATE INDEX IF NOT EXISTS idx_driver_statements_period
    ON public.driver_statements (period_type, period_start);

-- Backend-only table (service role bypasses RLS); no anon/user policies —
-- drivers receive statements by email and admins read via the backend API.
ALTER TABLE public.driver_statements ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE public.driver_statements IS
    'Weekly/monthly driver earnings-statement ledger; row insert is the statement job''s replay-safety claim.';
COMMENT ON COLUMN public.driver_statements.status IS
    'claimed | sent | failed | skipped_no_email | skipped_inactive (terminal after the claiming tick; never auto-retried).';
