-- 193_cloud_messages_marketing.sql
--
-- Extend cloud_messages for CASL-aware broadcasts (routes/admin/messaging.py):
--
--   • is_marketing      — when true the broadcast is a commercial electronic
--     message: the send path gates every recipient on express consent +
--     suppression per channel and uses the marketing senders (footer +
--     unsubscribe / STOP). Defaults false so existing operational/service
--     broadcasts (safety, outage) keep sending unchanged.
--   • service_area_id    — set when audience = 'service_area'; the broadcast
--     targets riders with recent rides in that area. Plain TEXT to match the
--     service_area_id stored on rides (no FK — service areas can be archived
--     while historical messages keep their reference).
--
-- Both columns are nullable / defaulted → forward-compatible with running
-- traffic (instantaneous metadata-only ADD COLUMN on a nullable/defaulted col).
--
-- RLS: cloud_messages is a service-role-only (backend) table — no user-facing
-- RLS policy is added or required here.
--
-- Rollback:
--   ALTER TABLE public.cloud_messages DROP COLUMN IF EXISTS is_marketing;
--   ALTER TABLE public.cloud_messages DROP COLUMN IF EXISTS service_area_id;

ALTER TABLE public.cloud_messages
    ADD COLUMN IF NOT EXISTS is_marketing    BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS service_area_id TEXT;

COMMENT ON COLUMN public.cloud_messages.is_marketing IS
    'True when the broadcast is a CASL commercial electronic message (consent-gated, marketing senders). False = operational/service notice.';
COMMENT ON COLUMN public.cloud_messages.service_area_id IS
    'Target service area when audience = service_area (riders with recent rides there). No FK so archived areas keep historical references.';
