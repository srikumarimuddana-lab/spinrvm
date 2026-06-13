-- Migration 149: Add stripe_connect_webhook_secret to settings
--
-- account.updated (driver Connect KYC) and payout.paid / payout.failed
-- (driver bank settlement) are Connected-accounts events. Stripe delivers
-- them from a webhook endpoint scoped to "Connected accounts", which gets
-- its OWN signing secret (whsec_) — distinct from the platform endpoint's
-- stripe_webhook_secret. routes/webhooks.py now verifies incoming events
-- against both secrets so a single URL can receive both endpoints' traffic.
--
-- Credential: masked on GET via _mask_credentials in routes/admin/settings.py;
-- super-admin reveal flow is the only way to read the plaintext value.
--
-- Forward-compatible: nullable, no backfill. Absent value reads as NULL,
-- which the Pydantic model handles via the "" default and the webhook
-- handler treats as "no connect endpoint configured" (single-secret mode).
--
-- Rollback:
--   ALTER TABLE settings DROP COLUMN IF EXISTS stripe_connect_webhook_secret;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS stripe_connect_webhook_secret TEXT;

COMMENT ON COLUMN public.settings.stripe_connect_webhook_secret IS
    'Signing secret (whsec_) for the Stripe Connected-accounts webhook endpoint '
    '(account.updated, payout.paid, payout.failed). Separate from '
    'stripe_webhook_secret, which signs the platform endpoint. Masked on GET; '
    'verified alongside the platform secret in routes/webhooks.py.';
