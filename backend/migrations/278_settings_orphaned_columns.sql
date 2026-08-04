-- 278_settings_orphaned_columns.sql
--
-- Back six admin-WRITABLE settings fields that were added to the admin
-- settings API (and mostly to the `AppSettings` pydantic model) without ever
-- getting a column on `public.settings`.
--
-- Found by the schema-drift test added alongside this migration
-- (`backend/tests/test_settings_schema_drift.py`), while fixing the identical
-- defect for the SOS paging pair in 277_settings_sos_paging.sql.
--
-- Why this is a real bug and not cosmetic: reads are fine, because
-- `settings_loader.get_app_settings()` merges `AppSettings()` defaults over
-- whatever the row actually contains — a missing column just resolves to its
-- default, forever, silently. Writes are not. `admin_update_settings`
-- (backend/routes/admin/settings.py:375) builds
-- `settings.model_dump(exclude_none=True)` and passes it straight to
-- PostgREST, so the first admin who sets one of these fields gets PGRST204 and
-- the ENTIRE settings save fails — not just their field. Each of these is
-- therefore a latent 500 on the settings page, armed and waiting for the first
-- person to touch the control.
--
-- Fields landed:
--   • ai_disabled_mode — how the rider app presents the AI assistant while it
--     is switched off ('coming_soon' | 'hidden'). Read at routes/ai.py:124.
--     This is the presentation half of an operator kill switch; an admin
--     flipping it today would break the settings page instead.
--   • apns_key_id, apns_team_id, apns_bundle_id — Apple Push identifiers
--     (visible, not secret; same treatment as twilio_account_sid).
--   • apns_p8_key — the APNs signing key. CREDENTIAL: already listed in
--     _CREDENTIAL_FIELDS and masked on GET via _mask_credentials.
--   • stripe_auto_heal_processing — gate on the reconciler's mark-paid path
--     (utils/stripe_reconcile.py:107,678). Its own docstring says it is
--     "shipped dark on purpose... must be reviewed and validated in staging
--     before an operator enables it in production" — but the operator could
--     not have enabled it, because the write 500s. Same defect as the SOS
--     paging pair, on a path that moves money (marks a ride paid, credits the
--     driver's tip). Column added; the flag stays OFF by default, so this
--     changes no behaviour — it only makes the intended switch reachable.
--
-- Deliberately NOT landed here: the 13 AppSettings fields that have no column
-- but are also absent from `SettingsUpdateRequest` (driver_map_*, corporate_*
-- cascade toggles, scheduled_ride_*). Those are read-only, default-backed
-- flags — nothing can write them, so nothing can trigger the PGRST204 path,
-- and adding columns would imply a write path that does not exist. The
-- accompanying test asserts the invariant that actually matters: every
-- admin-WRITABLE settings field has a column. See that file's docstring for
-- why it is scoped to writability rather than to every model field.
--
-- All six are nullable (no DEFAULT) so the migration is forward-compatible
-- with running traffic and existing behaviour is unchanged: a NULL column
-- reads back through the same AppSettings default it resolves to today.
-- `settings` is a single-row table (id = 'app_settings'), so there is no
-- batching concern on the ALTER.
--
-- RLS: the settings table is service-role-only (backend) — no user-facing RLS
-- policy is added or required here. Same stance as
-- 229_settings_lms_integration.sql.
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS ai_disabled_mode;
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS apns_key_id;
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS apns_team_id;
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS apns_bundle_id;
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS apns_p8_key;
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS stripe_auto_heal_processing;
--   (Dropping these restores today's behaviour exactly — the fields fall back
--   to their AppSettings defaults on read, and become unwritable again.)

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS ai_disabled_mode            TEXT,
    ADD COLUMN IF NOT EXISTS apns_key_id                 TEXT,
    ADD COLUMN IF NOT EXISTS apns_team_id                TEXT,
    ADD COLUMN IF NOT EXISTS apns_bundle_id              TEXT,
    ADD COLUMN IF NOT EXISTS apns_p8_key                 TEXT,
    ADD COLUMN IF NOT EXISTS stripe_auto_heal_processing BOOLEAN;

COMMENT ON COLUMN public.settings.ai_disabled_mode IS
    'How the rider app presents the AI assistant while it is disabled: coming_soon | hidden. NULL = AppSettings default (coming_soon). Validated by SettingsUpdateRequest''s regex, not by a CHECK constraint, so the allowed set stays in one place.';
COMMENT ON COLUMN public.settings.apns_key_id IS
    'Apple Push Notification service key ID. Identifier, not a secret — visible on GET, same treatment as twilio_account_sid.';
COMMENT ON COLUMN public.settings.apns_team_id IS
    'Apple Developer team ID used to sign APNs tokens. Identifier, not a secret.';
COMMENT ON COLUMN public.settings.apns_bundle_id IS
    'iOS bundle identifier APNs pushes are addressed to. Identifier, not a secret.';
COMMENT ON COLUMN public.settings.apns_p8_key IS
    'APNs .p8 signing key contents. CREDENTIAL — masked on GET via _mask_credentials; never returned in plaintext.';
COMMENT ON COLUMN public.settings.stripe_auto_heal_processing IS
    'Enables the Stripe reconciler''s auto-heal of stuck-processing rides (utils/stripe_reconcile.py). MOVES MONEY — marks a ride paid and credits the driver tip. NULL/FALSE = detection-only, which is the intended default; validate in staging before enabling.';
