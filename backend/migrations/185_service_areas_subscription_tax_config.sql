-- Migration 185: Per-service-area subscription tax configuration
--
-- Adds a subscription_tax_config JSONB column to service_areas so admins can
-- configure the correct tax rates for each province/territory without a
-- redeployment.  Saskatchewan is the launch province (GST 5% + PST 6%).
-- Alberta collects GST only (5%).  Ontario uses HST (13%).
-- The admin can also disable tax collection for an area (e.g. pilot periods).
--
-- Schema of the JSONB object:
--   enabled   BOOLEAN  — when false, no tax is added (default true for CA areas)
--   province  TEXT     — two-letter code used on the invoice label (SK, AB, ON …)
--   gst_rate  NUMERIC  — federal GST rate as a percentage (default 5.0)
--   pst_rate  NUMERIC  — provincial PST/QST rate (default 6.0 for SK, 0 for AB/ON)
--   hst_rate  NUMERIC  — harmonised HST rate (default 0; set to 13 for ON/NS/…)
--
-- Rollback:
--   ALTER TABLE public.service_areas DROP COLUMN IF EXISTS subscription_tax_config;

ALTER TABLE public.service_areas
    ADD COLUMN IF NOT EXISTS subscription_tax_config JSONB
        DEFAULT '{"enabled": true, "province": "SK", "gst_rate": 5.0, "pst_rate": 6.0, "hst_rate": 0.0}'::jsonb;

COMMENT ON COLUMN public.service_areas.subscription_tax_config IS
    'Per-area Spinr Pass subscription tax rates.  Managed by admin dashboard. '
    'Schema: {enabled:bool, province:text, gst_rate:numeric, pst_rate:numeric, hst_rate:numeric}.';
