-- Migration 314: Auto-payout batch tracking + instant payout kill switch
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
-- Rollback plan (SEQUENCING MATTERS for the table drop):
--   1. Revert/disable the auto-payout code first (set
--      app_settings.auto_payout_enabled=false, or deploy the code revert) —
--      auto_payout_loop queries auto_payout_batches every hour, so dropping
--      the table under live code produces a wall of ERROR logs every Sunday.
--   2. Then: DROP TABLE IF EXISTS auto_payout_batches;
--   The service_areas half is safe to drop at any time — the gate degrades
--   gracefully to "enabled" on a missing column, matching DEFAULT TRUE:
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
    status      TEXT NOT NULL DEFAULT 'running',  -- running | completed | partial | failed
                                         -- partial: some drivers paid, some failed/deferred —
                                         -- resumable by later Sunday ticks; reserved rows are
                                         -- retried by the hourly stale-reserved sweep
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    drivers_eligible  INT NOT NULL DEFAULT 0,
    drivers_paid      INT NOT NULL DEFAULT 0,
    drivers_failed    INT NOT NULL DEFAULT 0,
    total_amount      NUMERIC(12, 2) NOT NULL DEFAULT 0,
    error_summary     TEXT,             -- NULL on success; aggregated errors on partial failure
    -- Why drivers were skipped, and which ones had money waiting:
    --   {"counts": {"missing_gst": 4, ...},
    --    "drivers_with_balance": {"missing_gst": ["<driver_id>", ...]}}
    -- Driver IDs only — never names/phones/bank details (PIPEDA).
    skipped_summary   JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only one batch per ISO week — the auto-payout loop's replay-safety anchor.
CREATE UNIQUE INDEX IF NOT EXISTS idx_auto_payout_batches_week_key
    ON auto_payout_batches (week_key);

-- Index for admin dashboard listing (most recent first).
CREATE INDEX IF NOT EXISTS idx_auto_payout_batches_created
    ON auto_payout_batches (created_at DESC);

-- ── 3. Auto-payout retry bookkeeping on payouts ─────────────────────────
-- Attempt counter for the auto-payout retry taxonomy: retryable Stripe
-- failures (balance_insufficient, rate limit) re-attempt under a NEW
-- idempotency key (Stripe replays cached 4xx responses on key reuse), and
-- the key suffix is derived from this counter. Additive, constant default —
-- metadata-only on Postgres >= 11.
ALTER TABLE payouts
    ADD COLUMN IF NOT EXISTS auto_retry_count INT NOT NULL DEFAULT 0;

-- ── 4. RLS ──────────────────────────────────────────────────────────────
-- Backend-only ops/financial table (aggregate payout totals + error
-- summaries carrying driver IDs). Same pattern as migration 262
-- (data_transfer_export_jobs): explicit per-action service_role policies;
-- anon/authenticated get no policy, so RLS default-deny blocks them.
ALTER TABLE auto_payout_batches ENABLE ROW LEVEL SECURITY;

CREATE POLICY auto_payout_batches_service_select ON auto_payout_batches
    FOR SELECT TO service_role USING (true);
CREATE POLICY auto_payout_batches_service_insert ON auto_payout_batches
    FOR INSERT TO service_role WITH CHECK (true);
CREATE POLICY auto_payout_batches_service_update ON auto_payout_batches
    FOR UPDATE TO service_role USING (true) WITH CHECK (true);
CREATE POLICY auto_payout_batches_service_delete ON auto_payout_batches
    FOR DELETE TO service_role USING (true);
