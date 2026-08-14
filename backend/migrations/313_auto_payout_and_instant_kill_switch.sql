-- Migration 313: Auto-payout batch tracking + instant payout kill switch
--
-- Why:
--   Spinr moves from driver-initiated cashouts to Spinr-controlled weekly
--   auto-payouts (every Sunday). Drivers no longer tap "Cash out" — the
--   platform calculates each driver's payable_balance and sends a
--   stripe.Transfer for everyone with >= $10 payable.
--
--   Instant payouts (fee-bearing, driver-initiated) remain available but
--   are now gated per service area via a kill switch so ops can disable
--   them in specific markets without a code deploy.
--
--   auto_payout_batches: tracks each weekly run — when it started,
--   how many drivers were paid, total amount, and outcome. One row per
--   Sunday run; the batch's week_key (ISO week string like "2026-W33")
--   is the idempotency anchor — a replay on the same week is a no-op.
--
-- Rollback plan:
--   DROP TABLE IF EXISTS auto_payout_batches;
--   ALTER TABLE service_areas DROP COLUMN IF EXISTS instant_payout_enabled;

-- ── 1. Instant payout kill switch on service_areas ──────────────────────
ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS instant_payout_enabled BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN service_areas.instant_payout_enabled IS
    'When FALSE, drivers in this service area cannot use instant (fee-bearing) payouts. '
    'Standard weekly auto-payouts are unaffected. Toggled by admin via service-area settings.';

-- ── 2. Auto-payout batch tracking table ─────────────────────────────────
CREATE TABLE IF NOT EXISTS auto_payout_batches (
    id          TEXT PRIMARY KEY,
    week_key    TEXT NOT NULL,           -- e.g. "2026-W33" (ISO week)
    status      TEXT NOT NULL DEFAULT 'running',  -- running | completed | failed
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    drivers_eligible  INT NOT NULL DEFAULT 0,
    drivers_paid      INT NOT NULL DEFAULT 0,
    drivers_failed    INT NOT NULL DEFAULT 0,
    total_amount      NUMERIC(12, 2) NOT NULL DEFAULT 0,
    error_summary     TEXT,             -- NULL on success; aggregated errors on partial failure
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only one batch per ISO week — the auto-payout loop's replay-safety anchor.
CREATE UNIQUE INDEX IF NOT EXISTS idx_auto_payout_batches_week_key
    ON auto_payout_batches (week_key);

-- Index for admin dashboard listing (most recent first).
CREATE INDEX IF NOT EXISTS idx_auto_payout_batches_created
    ON auto_payout_batches (created_at DESC);
