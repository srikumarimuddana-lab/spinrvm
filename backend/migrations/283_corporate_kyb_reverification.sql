-- Migration 283: corporate_accounts.kyb_reverify_flagged_at
--
-- Rollback:
--   ALTER TABLE public.corporate_accounts DROP COLUMN IF EXISTS kyb_reverify_flagged_at;
--
-- Corporate + admin portal review round 2, "automated KYB re-verification"
-- (business decision: scheduled staleness reminder for admins — no
-- automatic re-scoring, no automatic status change, no third-party KYB
-- provider integration). KYB is verified once at onboarding
-- (kyb_reviewed_at / kyb_last_decision, migration 225) with no periodic
-- re-check today.
--
-- kyb_reverify_flagged_at is NOT what the admin-dashboard "needs
-- re-verification" filter reads — that filter is a live computation
-- (now() - kyb_reviewed_at > threshold), so it works correctly even for
-- rows this column has never touched. This column exists purely as the
-- background loop's OWN replay-safety claim flag (see
-- backend/.claude/skills/spinr-background-loop's "claim flag column"
-- pattern, same shape as corporate_wallets.low_balance_notified_at) — so
-- the loop's log line + Prometheus metric fire once per stale period per
-- company, not on every tick.
--
-- Forward-compatible: additive nullable column. Safe against production
-- traffic in flight.

ALTER TABLE public.corporate_accounts
    ADD COLUMN IF NOT EXISTS kyb_reverify_flagged_at TIMESTAMPTZ;

COMMENT ON COLUMN public.corporate_accounts.kyb_reverify_flagged_at IS
    'Background loop replay-safety claim flag only (utils/kyb_reverification.py) — NOT the source of truth for staleness, which the admin filter computes live from kyb_reviewed_at.';
