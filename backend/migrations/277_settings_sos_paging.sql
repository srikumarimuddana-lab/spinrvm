-- 277_settings_sos_paging.sql
--
-- Back the SOS on-call paging settings (ACTION_ITEMS.md B15(b)) with real
-- columns, and add the driver discreet-SOS rollout flag (B16).
--
-- Why this exists: B15(b) shipped `backend/utils/safety_paging.py::page_on_call`
-- plus `sos_paging_webhook_url` / `sos_paging_routing_key` on the `AppSettings`
-- pydantic model and the admin settings API — but never added the backing
-- columns. Reads hid the omission (`get_app_settings()` merges AppSettings
-- defaults, so the keys always resolved to "" and paging silently no-opped),
-- while writes would have surfaced it hard: admin_update_settings builds
-- `settings.model_dump(exclude_none=True)` and hands it straight to PostgREST,
-- so the first attempt to actually configure paging would return PGRST204 and
-- fail the ENTIRE settings save, not just that field. The feature was
-- unshippable until this migration lands.
--
-- Fields landed:
--   • sos_paging_webhook_url      — provider webhook the SOS pager POSTs to
--     (PagerDuty Events API v2 shape by default; an Opsgenie or other endpoint
--     accepting that shape is a config change, not a rewrite). Blank = paging
--     disabled, which is the shipped default: page_on_call makes zero HTTP
--     calls and returns False. This column is the on/off switch.
--   • sos_paging_routing_key      — PagerDuty "Integration Key". CREDENTIAL:
--     masked on GET via _mask_credentials in routes/admin/settings.py.
--   • driver_sos_discreet_enabled — rollout flag for the driver discreet-SOS
--     UX (B16). Default FALSE so the behaviour ships dark; flipping it is the
--     rollback path, no redeploy needed.
--
-- All three are nullable / defaulted so the migration is forward-compatible
-- with running traffic. `settings` is a single-row table (id = 'app_settings'),
-- so there is no batching concern on the ALTER.
--
-- RLS: the settings table is service-role-only (backend) — no user-facing
-- RLS policy is added or required here. Same stance as
-- 229_settings_lms_integration.sql.
--
-- Note on driver_sos_discreet_enabled read path: unlike the two paging fields
-- (admin-only), this flag is exposed on the PUBLIC GET /settings projection in
-- backend/routes/settings.py, because the driver app needs it before an SOS
-- ever happens. It carries no secret — it is a boolean describing UI behaviour.
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS sos_paging_webhook_url;
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS sos_paging_routing_key;
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS driver_sos_discreet_enabled;
--   (Dropping driver_sos_discreet_enabled reverts the driver SOS button to the
--   shared/rider behaviour, since the client falls back to `false` when the
--   key is absent from the settings payload. Prefer setting it FALSE over
--   dropping it — that is the intended rollback and needs no migration.)

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS sos_paging_webhook_url      TEXT,
    ADD COLUMN IF NOT EXISTS sos_paging_routing_key      TEXT,
    ADD COLUMN IF NOT EXISTS driver_sos_discreet_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.settings.sos_paging_webhook_url IS
    'On-call paging webhook for triggered SOS alerts (PagerDuty Events API v2 shape by default). Blank = paging disabled; utils/safety_paging.py::page_on_call makes no HTTP call in that state. Super-admin-only to change.';
COMMENT ON COLUMN public.settings.sos_paging_routing_key IS
    'Routing / integration key sent in the SOS paging payload. CREDENTIAL — masked on GET via _mask_credentials; never returned in plaintext. Super-admin-only to change.';
COMMENT ON COLUMN public.settings.driver_sos_discreet_enabled IS
    'Rollout flag for the driver discreet-SOS UX (ACTION_ITEMS.md B16): silent alert, no red flash, no native modal. FALSE = drivers keep the shared/rider SOS behaviour. Exposed on the public GET /settings projection; contains no secret.';
