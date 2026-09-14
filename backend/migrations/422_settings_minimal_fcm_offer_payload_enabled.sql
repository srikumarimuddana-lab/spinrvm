-- 422_settings_minimal_fcm_offer_payload_enabled.sql
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS minimal_fcm_offer_payload_enabled;
--
-- Why this migration exists
-- ------------------------
-- #1231 finding 15 (remaining half, docs: the issue itself) -- the ride-offer
-- FCM `data` payload (backend/routes/rides/matching.py's batch-dispatch path)
-- carries precise pickup/dropoff GPS coordinates and rider_rating in cleartext
-- through Google/Apple push infra. This column is the kill switch for the new,
-- reduced-payload path: when TRUE, matching.py's `_FCM_EXCLUDE` additionally
-- drops pickup_lat/pickup_lng/pickup_nav_lat/pickup_nav_lng/dropoff_lat/
-- dropoff_lng/rider_rating from the FCM `data` dict (the WebSocket
-- `dispatch_payload` is untouched either way), and the driver-app's background
-- handler hydrates those fields via a new authenticated
-- GET /api/v1/drivers/rides/{ride_id}/offer fetch instead.
--
-- Default FALSE matches the Pydantic field's default and today's actual
-- behaviour -- applying this migration changes zero behaviour on its own.
-- Ships fully dark; flip to TRUE only after a human verifies real offer
-- delivery on physical iOS + Android devices in staging (native background/
-- killed-app FCM handling cannot be exercised in this sandbox).
--
-- Additive only: nullable-with-default column, no table rewrite (single-row
-- config table regardless), same shape as migration 401
-- (dispatch_direct_pool_enabled).

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS minimal_fcm_offer_payload_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.settings.minimal_fcm_offer_payload_enabled IS
    '#1231 finding 15 kill switch: when TRUE, the ride-offer FCM data payload '
    'drops precise pickup/dropoff coordinates and rider_rating (driver-app '
    'refetches them via an authenticated GET /drivers/rides/{ride_id}/offer '
    'call instead). Default FALSE = current full-payload behaviour, unchanged. '
    'Requires human device verification (physical iOS + Android, staging) '
    'before enabling in production -- see PR for #1231 finding 15.';
