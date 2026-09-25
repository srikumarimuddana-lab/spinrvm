-- 475: per-admin daily money-action cap + single-action alert threshold
-- (ROADMAP N23 / finding ADMIN-OPS-001).
--
-- Purpose: admins can credit/debit rider and driver wallets
-- (routes/admin/wallet.py) and issue dispute refunds (routes/disputes.py
-- admin_resolve_dispute) with no amount limit and no alert. This is an interim
-- control until a real second-approver queue exists:
--   admin_money_daily_cap_per_admin -- max total (absolute value) one admin may
--       move per UTC day across wallet credits, wallet debits and dispute
--       refunds. Over the cap -> 403, nothing moves.
--   admin_money_alert_threshold     -- a single action at or above this amount
--       is still allowed (subject to the cap) but raises a warning log, an
--       audit_logs row and a Sentry event tagged domain=admin.
--   admin_dispute_refunds_enabled   -- kill switch for real Stripe refunds from
--       the admin dispute resolve endpoint (routes/disputes.py). FALSE (default)
--       = an approved/partial refund is recorded as resolved but NO refund is
--       issued; the admin is told to refund manually. TRUE = Stripe refund,
--       subject to the cap above.
-- Enforced by services/admin_money_caps.py, which sums the admin's own
-- audit_logs rows for today. No new table. All three are super_admin-only to
-- change (routes/admin/settings.py).
--
-- Semantics: cap/threshold NULL = disabled; both ship NULL, so nothing changes
-- until the owner sets values (founder decision E-F7) via PUT
-- /api/admin/settings. admin_dispute_refunds_enabled ships FALSE.
--
-- Additive + idempotent (ADD COLUMN IF NOT EXISTS; two nullable columns with
-- no default, one BOOLEAN with a constant default, which PG11+ stores as
-- metadata) -- no table rewrite, single-row table, safe under live traffic. The backend
-- reads these keys with settings.get(), so code deployed before this migration
-- sees them as absent (= disabled). Saving either field from the admin
-- dashboard before this migration is applied would fail with PGRST204, so
-- apply the migration first.
--
-- Operational rollback (no deploy; settings cache is 60s):
--   UPDATE public.settings
--      SET admin_money_daily_cap_per_admin = NULL,
--          admin_money_alert_threshold = NULL,
--          admin_dispute_refunds_enabled = FALSE
--    WHERE id = 'app_settings';
-- Schema rollback (ONLY after reverting the backend code that reads/writes
-- these columns -- SettingsUpdateRequest would otherwise PGRST204 on save):
--   ALTER TABLE public.settings
--     DROP COLUMN IF EXISTS admin_dispute_refunds_enabled,
--     DROP COLUMN IF EXISTS admin_money_alert_threshold,
--     DROP COLUMN IF EXISTS admin_money_daily_cap_per_admin;
-- Existing table permissions/RLS remain unchanged. No new query or index
-- (the daily sum uses audit_logs' existing actor_id and created_at indexes).

BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '20s';

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS admin_money_daily_cap_per_admin NUMERIC(12,2) NULL,
    ADD COLUMN IF NOT EXISTS admin_money_alert_threshold NUMERIC(12,2) NULL,
    ADD COLUMN IF NOT EXISTS admin_dispute_refunds_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.settings.admin_money_daily_cap_per_admin IS
    'N23: max CAD one admin may move per UTC day (absolute sum of wallet credits, '
    'wallet debits and dispute refunds, from audit_logs). Over cap -> 403. NULL = disabled.';
COMMENT ON COLUMN public.settings.admin_money_alert_threshold IS
    'N23: a single admin wallet credit/debit or dispute refund at or above this CAD '
    'amount is allowed but alerts (warning log, audit_logs row, Sentry domain=admin). NULL = disabled.';
COMMENT ON COLUMN public.settings.admin_dispute_refunds_enabled IS
    'N23: FALSE = admin dispute resolve records approved refunds but issues none '
    '(manual refund required); TRUE = Stripe refund, subject to the daily cap.';

COMMIT;
