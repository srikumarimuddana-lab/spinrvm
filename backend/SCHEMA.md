# Spinr backend – canonical table and column names

Use these names in Supabase and in code so admin, API, and DB stay aligned.

## Settings

- **Table:** `settings`
- **Shape:** Single row with `id = 'app_settings'` and flat keys.
- **Canonical keys:** (from `AppSettings` in `schemas.py`)
  - `google_maps_api_key`, `stripe_publishable_key`, `stripe_secret_key`, `stripe_webhook_secret`
  - `twilio_account_sid`, `twilio_auth_token`, `twilio_from_number`
  - `driver_matching_algorithm`, `min_driver_rating`, `search_radius_km`
  - `cancellation_fee_admin`, `cancellation_fee_driver`, `platform_fee_percent`
  - `terms_of_service_text`, `privacy_policy_text`, `updated_at`
- **Usage:** All readers use `get_app_settings()` from `settings_loader.py` (single source of truth). Admin GET/PUT use the same single row.

## Service areas

- **Table:** `service_areas`
- **Columns:** `id`, `name`, `city`, `is_active`, `is_airport`, `airport_fee`, `created_at`, and either:
  - **`polygon`** – JSON array of `{ "lat": number, "lng": number }`, or
  - **`geojson`** – GeoJSON geometry (coordinates as `[lng, lat]`).
- **Usage:** Use `get_service_area_polygon(area)` from `utils.py` to get a list of `{lat, lng}` for point-in-polygon checks (supports both `polygon` and `geojson`).

## Vehicle types

- **Table:** `vehicle_types`
- **Columns:** `id`, `name`, `description`, `icon`, `capacity`, `image_url`, `is_active`, `created_at`. Admin may also use `base_fare`, `price_per_km`, `price_per_minute`, `display_order` if stored here.

## Fare configs

- **Table:** `fare_configs` (not `fare_configurations`)
- **Columns:** `id`, `service_area_id`, `vehicle_type_id`, `base_fare`, `per_km_rate`, `per_minute_rate`, `minimum_fare`, `booking_fee`, `is_active`, `created_at`. Admin API may send `price_per_km` / `price_per_minute`; map to `per_km_rate` / `per_minute_rate` when writing.

## Rides

- **Table:** `rides`
- **Canonical column names:**
  - **Fare:** `total_fare` (not `fare`)
  - **Completion time:** `ride_completed_at` (not `completed_at`)
  - **Platform share:** `admin_earnings` (not `platform_fee`)
- **Usage:** Use `total_fare`, `ride_completed_at`, and `admin_earnings` in queries, stats, and exports so admin and backend match.

## Driver location history

- **Table:** `driver_location_history`
- **Columns:** `driver_id`, `lat`, `lng`, `timestamp` (or `created_at` depending on schema).
