# Backend Reference — Function & Class Index

A quick-lookup index for the Spinr backend. Use this to jump from a symbol name to the file and line where it lives. For narrative and flow, go to the domain docs (`ARCHITECTURE.md`, `AUTH_AND_USERS.md`, `RIDES_AND_DISPATCH.md`, `WALLET_AND_PAYMENTS.md`, `INFRASTRUCTURE.md`, `ADMIN_AND_OPS.md`) or `docs/CORPORATE_B2B.md` for the B2B wallet subsystem.

Line numbers are accurate as of 2026-04-17. Treat them as hints; the file path is authoritative.

---

## Conventions

- All paths are relative to `backend/`.
- `…` in a signature marks standard FastAPI dependencies / pagination kwargs omitted for brevity.
- Pure helpers are marked `(pure)` — no I/O, safe to unit-test.
- `(async)` is FastAPI's coroutine convention; expect `await` at call sites.

---

## 1. Auth, identity, session

### `dependencies.py`

| Symbol | Purpose |
|---|---|
| `generate_otp()` — L48 | 4-digit OTP via `secrets.choice`. |
| `generate_pickup_otp()` — L58 | 4-digit pickup-OTP (same algo). |
| `hash_token(raw)` — L63 | sha256 for refresh-token storage. |
| `create_refresh_token()` — L68 | Opaque 32-byte token. |
| `create_jwt_token(user_id, phone, session_id, *, token_version)` — L73 | 15-min HS256 access JWT. |
| `verify_jwt_token(token)` — L104 | Decode; raise 401 on expiry/invalid. |
| `_token_version_mismatch(payload, user_row)` — L114 (pure) | Revocation check; missing claim treated as 0. |
| `get_current_user(credentials)` — L127 (async) | Firebase-first, JWT fallback, admin claim shortcut. |
| `get_admin_user(current_user)` — L257 (async) | Role gate for admin endpoints. |
| `get_current_admin` — L266 | Alias for `get_admin_user`. |
| `JWT_ALGORITHM`, `OTP_EXPIRY_MINUTES`, `OTP_LENGTH`, `PICKUP_OTP_LENGTH` | Module-level constants. |

### `routes/auth.py`

| Symbol | Purpose |
|---|---|
| `POST /auth/send-otp` | Rate-limited OTP send, Twilio or console. |
| `POST /auth/verify-otp` | Verify OTP, mint access + refresh tokens, upsert session. |
| `POST /auth/refresh` | Refresh-token rotation. |
| `POST /auth/logout` | Revoke refresh token for this device. |
| `POST /auth/logout-all` | Bump `users.token_version` → revoke everything. |

### `routes/admin/auth.py`

| Symbol | Purpose |
|---|---|
| `POST /api/admin/auth/login` | Email+password, bcrypt with sha256 legacy upgrade. |
| `POST /api/admin/auth/refresh` | Admin refresh rotation. |
| `POST /api/admin/auth/logout` / `logout-all` | Bump `admin_staff.token_version`. |

### `utils/password.py`

| Symbol | Purpose |
|---|---|
| `hash_password(plain)` | bcrypt cost 12. |
| `verify_password(plain, stored_hash)` | Try bcrypt, then legacy sha256; returns `(ok, needs_upgrade)`. |

### `utils/refresh_tokens.py`

| Symbol | Purpose |
|---|---|
| `insert_refresh_token(user_id, raw, expires_at)` | Store sha256 hash. |
| `lookup_refresh_token(raw)` | Hash + load; None if missing/expired. |
| `revoke_refresh_token(raw)` | Delete row. |
| `rotate_refresh_token(raw, user_id)` | Revoke + issue new. |

### `utils/crypto.py`

| Symbol | Purpose |
|---|---|
| `random_token(nbytes=32)` | Wrapper over `secrets.token_urlsafe`. |
| `constant_time_compare(a, b)` | `hmac.compare_digest` wrapper. |

### `sms_service.py`

| Symbol | Purpose |
|---|---|
| `send_sms(to, body, twilio_sid, twilio_token, twilio_from)` | Twilio REST; console fallback. |
| `send_otp_sms(phone, code, …)` | OTP template wrapper. |

---

## 2. Users

### `routes/users.py`

| Endpoint | Purpose |
|---|---|
| `GET /users/me` | Current profile. |
| `PUT /users/me` | Update name/email/gender/profile_image. |
| `POST /users/create-profile` | Finish profile completion. |
| `DELETE /users/me` | Soft delete. |
| `POST /users/push-token` | Register FCM token. |

### `routes/admin/users.py`

| Endpoint | Purpose |
|---|---|
| `GET /admin/users` | Paginated list with filters. |
| `GET /admin/users/{id}` | Full profile + ride/wallet summary. |
| `PATCH /admin/users/{id}/status` | active / suspended / banned. |
| `POST /admin/users/{id}/force-logout` | Bump `token_version`. |
| `GET /admin/users/{id}/rides` | Rider history. |
| `GET /admin/users/{id}/wallet-transactions` | Wallet ledger. |

### `routes/admin/staff.py`

| Endpoint | Purpose |
|---|---|
| `GET /admin/staff` | List admins. |
| `POST /admin/staff` | Create (super-admin). |
| `PATCH /admin/staff/{id}` | Update role/modules/status. |
| `DELETE /admin/staff/{id}` | Revoke via token_version bump. |
| `POST /admin/staff/{id}/reset-password` | Generate + optionally email. |

---

## 3. Rides

### `routes/rides.py`

Decimal helpers:

| Symbol | Purpose |
|---|---|
| `_d(v)` — L49 (pure) | to `Decimal`. |
| `_round(v)` — L54 (pure) | quantize 2 dp. |
| `_f(v)` — L58 (pure) | `Decimal` → `float`. |

Dispatch + lifecycle:

| Symbol | Purpose |
|---|---|
| `match_driver_to_ride(ride_id, *, ride=None)` — L88 | Core dispatch. |
| `_offer_timeout_handler(ride_id, driver_id, rider_id, timeout_seconds=30)` — L318 | Re-dispatch if no accept. |
| `create_ride(request, body, …)` — L493 | POST `/rides`. |
| `ride_search_timeout(r_id, timeout_seconds=300)` — L671 | 5-min auto-cancel. |
| `estimate_ride(request, …)` — L400 | POST `/rides/estimate`. |
| `get_active_ride(...)` — L718 | GET `/rides/active`. |
| `get_ride_history(...)` — L782 | GET `/rides/history`. |
| `get_ride(ride_id, …)` — L816 | GET `/rides/{id}`. |
| `add_tip(ride_id, request, …)` — L914 | POST `/rides/{id}/tip`. |
| `process_payment(ride_id, request, …)` — L941 | POST `/rides/{id}/process-payment`. |
| `rate_driver(...)` — L1219 | POST `/rides/{id}/rate`. |
| `cancel_ride_rider(...)` — L1283 | POST `/rides/{id}/cancel`. |
| `get_share_trip_link(...)` — L1058 | GET `/rides/{id}/share`. |
| `share_trip_with_contact(...)` — L1089 | POST `/rides/{id}/share`. |
| `get_shared_contacts(...)` — L1156 | GET `/rides/{id}/shared-contacts`. |
| `track_shared_ride(share_token)` — L1167 | GET `/rides/track/{token}` (public). |
| `add_stop_mid_trip(...)` — L1412 | POST `/rides/{id}/stops`. |
| `remove_stop_mid_trip(...)` — L1449 | DELETE `/rides/{id}/stops/{idx}`. |
| `trigger_emergency(...)` — L1490 | POST `/rides/{id}/emergency`. |
| `get_chat_status(...)` — L1555 | GET `/rides/{id}/chat-status`. |
| `get_ride_messages(...)` — L1649 | GET `/rides/{id}/messages`. |
| `send_ride_message(...)` — L1688 | POST `/rides/{id}/messages`. |
| `get_scheduled_rides(...)` — L1754 | GET `/rides/scheduled`. |
| `cancel_scheduled_ride(...)` — L1762 | DELETE `/rides/scheduled/{id}`. |
| `simulate_driver_arrival(...)` — L1787 | Dev test endpoint. |
| `rider_start_ride(...)` — L1803 | POST `/rides/{id}/start`. |
| `rider_complete_ride(...)` — L1820 | POST `/rides/{id}/complete`. |
| `get_ride_receipt(...)` — L1832 | GET `/rides/{id}/receipt`. |

Pydantic schemas: `RideEstimateRequest` L392, `ShareTripWithContactRequest` L1084, `AddStopMidTripRequest` L1405, `EmergencyRequest` L1484, `SendMessageRequest` L1684.

### `services/dispatch_service.py`

| Symbol | Purpose |
|---|---|
| `_is_dispatchable_driver(driver)` — L36 (pure) | Has user_id + lat/lng. |
| `filter_and_rank_drivers(ride, candidates, algorithm, min_rating, search_radius_km)` — L51 (pure) | Filter + rank, attach `distance_km`. |
| `select_driver_by_algorithm(drivers_with_distance, algorithm, last_assigned_driver_id)` — L84 (pure) | nearest / rating_based / round_robin / combined. |
| `DispatchService.resolve_matching_config(ride, *, app_settings=None)` — L134 | Config from service area or app_settings. |
| `DispatchService.find_candidate_drivers(ride)` — L170 | DB fetch. |
| `DispatchService.claim_driver(driver_id)` — L182 | Atomic claim. |
| `DispatchService.claim_any_driver(drivers_with_distance)` — L197 | Try in order until one claim succeeds. |
| `DispatchService.assign_driver_to_ride(ride_id, driver_id, now)` — L212 | Update ride row. |
| `DispatchService.last_assigned_driver_id()` — L233 | For round_robin. |

### `services/fare_service.py`

| Symbol | Purpose |
|---|---|
| `_fd(v)` — L34 (pure) | Quantize 2 dp via Decimal. |
| `build_default_fares(vehicle_types, surge=1.0)` — L44 (pure) | Fallback fares. |
| `find_service_area_for_point(areas, lat, lng)` — L68 (pure) | Polygon match. |
| `merge_fare_configs_with_vehicle_types(fare_configs, vehicle_types, surge)` — L81 (pure) | Join + apply surge. |
| `FareService.list_active_vehicle_types()` — L123 | DB fetch. |
| `FareService.fares_for_location(lat, lng)` — L127 | area → configs → defaults. |

### `routes/fares.py`

| Symbol | Purpose |
|---|---|
| `_fd(v)` — L34 (pure) | Quantize 2 dp. |
| `_fare_cache_key(lat, lng)` — L43 (pure) | Redis key (~1.1 km cell). |
| `invalidate_fare_cache()` — L48 | Flush `fares:*`. |
| `_build_default_fares(vt_list, surge=1.0)` — L61 (pure) | Defaults with surge. |
| `resolve_service_area_for_point(lat, lng, all_areas=None)` — L84 | Polygon match. |
| `build_fares_for_area(matched_area, vehicle_types)` — L104 | Fares from matched area. |
| `_fares_for_location_impl(lat, lng, all_areas=None, vehicle_types=None)` — L156 | Shared impl. |
| `get_fares_for_location(lat, lng)` — L185 | GET `/fares` + cache. |
| `get_vehicle_types()` — L55 | GET `/vehicle-types`. |

### `routes/fare_split.py`

| Symbol | Purpose |
|---|---|
| `_d(v)` — L32 (pure) | Decimal helper. |
| `create_fare_split(req, …)` — L55 | POST `/fare-split`. |
| `get_fare_split(split_id, …)` — L123 | GET `/fare-split/{id}`. |
| `get_fare_split_for_ride(ride_id, …)` — L167 | GET `/fare-split/ride/{id}`. |
| Schemas: `CreateFareSplitRequest` L39, `RespondToSplitRequest` L44, `PaySplitRequest` L48. |

### `routes/websocket.py`

| Symbol | Purpose |
|---|---|
| `heartbeat_task(websocket, connection_key)` — L38 | 30-s ping loop. |
| `websocket_endpoint(websocket, client_type, client_id)` — L54 | Auth, register, message loop. |

### `socket_manager.py`

| Symbol | Purpose |
|---|---|
| `ConnectionManager.connect(websocket, client_id)` — L18 | Register WS. |
| `ConnectionManager.disconnect(client_id)` — L28 | Deregister. |
| `ConnectionManager.send_personal_message(message, client_id)` — L38 | Redis pub/sub or local. |
| `ConnectionManager._deliver_local(message, client_id)` — L63 | Local delivery. |
| `ConnectionManager.broadcast(message)` — L88 | Local-only broadcast. |
| `ConnectionManager.broadcast_to_admins(message)` — L100 | All admin clients. |
| `ConnectionManager.update_driver_location(driver_id, lat, lng)` — L109 | In-memory cache. |
| `ConnectionManager.get_driver_location(driver_id)` — L112 | Read cache. |
| `manager` — L116 | Module singleton. |

### `utils/ws_pubsub.py`

| Symbol | Purpose |
|---|---|
| `_WSPubSub.__init__()` — L54 | Init state. |
| `_WSPubSub.active` (property) — L61 | Redis + consumer alive? |
| `_WSPubSub.start(manager, redis_url)` — L66 | Connect + subscribe. |
| `_WSPubSub.publish(client_id, message)` — L110 | Publish JSON. |
| `_WSPubSub._consumer()` — L137 | Subscriber loop. |
| `pubsub` | Module singleton. |

### `utils/surge_engine.py`

| Symbol | Purpose |
|---|---|
| `ratio_to_multiplier(ratio)` — L44 (pure) | Demand/supply → tier. |
| `_count_demand_in_area(area_id)` — L52 | DB count. |
| `_count_supply_in_area(area)` — L72 | DB count + polygon filter. |
| `calculate_surge_for_area(area)` — L94 | Compute metrics. |
| `recalculate_all_surges()` — L115 | Per-area update. |
| `surge_recalculation_loop()` | Every 2 min. |

### `utils/scheduled_rides.py`

| Symbol | Purpose |
|---|---|
| `_dispatch_scheduled_ride(ride)` — L25 | Scheduled → searching + dispatch. |
| `_send_reminder(ride)` — L73 | 10-min push. |
| `check_scheduled_rides()` — L102 | Batch scan. |
| `scheduled_ride_dispatcher_loop()` — L149 | Every 60 s. |

### `utils/document_expiry.py`

| Symbol | Purpose |
|---|---|
| `check_expiring_documents()` — L24 | Scan drivers. |
| `document_expiry_loop()` — L132 | Every 12 h. |

### `utils/demand_forecast.py`

| Symbol | Purpose |
|---|---|
| `_get_historical_hourly_demand(area_id, lookback_days)` — L63 | 28-day demand matrix. |

### `geo_utils.py`

| Symbol | Purpose |
|---|---|
| `calculate_distance(lat1, lng1, lat2, lng2)` — L5 (pure) | Haversine km. |
| `get_service_area_polygon(area)` — L14 (pure) | Legacy + GeoJSON. |
| `point_in_polygon(lat, lng, polygon)` — L39 (pure) | Ray-cast. |

### `routes/drivers.py`

Transition guards: `ARRIVE_FROM_STATES` L40, `START_FROM_STATES` L46, `COMPLETE_FROM_STATES` L52.

Helpers + endpoints:

| Symbol | Purpose |
|---|---|
| `_require_ride_in_state(ride_id, driver_id, allowed_states)` — L55 | Guard. |
| `get_driver_config(...)` — L85 | GET `/drivers/config`. |
| `get_my_driver(...)` — L131 | GET `/drivers/me`. |
| `update_my_driver(body, …)` — L167 | PUT `/drivers/me`. |
| `register_driver(body, …)` — L289 | POST `/drivers/register`. |
| `register_driver_push_token(...)` — L407 | POST `/drivers/push-token`. |
| `update_driver_status_self(...)` — L441 | POST `/drivers/status`. |
| `set_destination_mode(req, …)` — L476 | POST `/drivers/destination`. |
| `clear_destination_mode(...)` — L504 | DELETE `/drivers/destination`. |
| `get_destination_mode(...)` — L527 | GET `/drivers/destination`. |
| `get_demand_heatmap(...)` — L247 | GET `/drivers/demand-heatmap`. |
| `get_driver_balance(...)` — L542 | GET `/drivers/balance`. |
| `get_driver_earnings(...)` — L589 | GET `/drivers/earnings`. |
| `get_driver_daily_earnings(...)` — L657 | GET `/drivers/earnings/daily`. |
| `get_driver_trip_earnings(...)` — L702 | GET `/drivers/earnings/trips`. |
| `get_driver_weekly_earnings(...)` — L748 | GET `/drivers/earnings/weekly`. |
| `get_driver_monthly_earnings(...)` — L851 | GET `/drivers/earnings/monthly`. |
| `get_driver_earnings_comparison(...)` — L936 | GET `/drivers/earnings/comparison`. |
| `get_nearby_drivers_public(...)` — L1007 | GET `/drivers/nearby`. |
| `update_location_batch(batch, …)` — L1083 | POST `/drivers/location-batch`. |
| `get_bank_account(...)` — L1134 | GET `/drivers/bank-account`. |
| `onboard_stripe(...)` — L1157 | POST `/drivers/stripe-onboard`. |
| `save_bank_account(req, …)` — L1208 | POST `/drivers/bank-account`. |
| `delete_bank_account(...)` — L1240 | DELETE `/drivers/bank-account`. |
| `request_payout(req, …)` — L1251 | POST `/drivers/payouts`. |
| `get_payout_history(...)` — L1309 | GET `/drivers/payouts`. |
| `get_t4a_summary(year, …)` — L1327 | GET `/drivers/t4a/{year}`. |
| `export_earnings(...)` — L1352 | GET `/drivers/earnings/export`. |
| `get_active_ride(...)` — L1370 | GET `/drivers/rides/active`. |
| `get_ride_history(...)` — L1442 | GET `/drivers/rides/history`. |
| `accept_ride(ride_id, …)` — L1473 | POST `/drivers/rides/{id}/accept`. |
| `decline_ride(ride_id, …)` — L1568 | POST `/drivers/rides/{id}/decline`. |
| `arrive_at_pickup(ride_id, …)` — L1612 | POST `/drivers/rides/{id}/arrive`. |
| `verify_pickup_otp(ride_id, request, …)` — L1659 | POST `/drivers/rides/{id}/verify-otp`. |
| `start_ride(ride_id, …)` — L1693 | POST `/drivers/rides/{id}/start`. |
| `complete_ride(ride_id, …)` — L1719 | POST `/drivers/rides/{id}/complete`. |
| `cancel_ride(ride_id, …)` — L1882 | POST `/drivers/rides/{id}/cancel`. |
| `check_expiring_subscriptions()` | Background loop (Spinr Pass reminder). |

Schemas: `RideOTPRequest` L32, `UpdateDriverProfileRequest` L142, `PushTokenPayload` L402, `SetDestinationRequest` L470, `BankAccountCreate` L1121, `PayoutRequest` L1130.

### `routes/admin/drivers.py`

| Symbol | Purpose |
|---|---|
| `_user_display_name(user)` — L26 (pure) | Name helper. |
| `_batch_fetch_drivers_and_users(...)` — L34 | Batch to avoid N+1. |
| `_log_driver_activity(...)` — L62 | Insert activity log row. |
| `admin_get_drivers(...)` — L115 | GET `/admin/drivers`. |
| `admin_get_driver_stats(...)` — L176 | GET `/admin/drivers/stats`. |
| `admin_update_driver(...)` — L380 | PUT `/admin/drivers/{id}`. |
| `admin_verify_driver(...)` — L421 | POST `/admin/drivers/{id}/verify`. |
| `admin_driver_action(...)` — L471 | POST `/admin/drivers/{id}/action`. |
| `admin_override_driver_status(...)` — L600 | PUT `/admin/drivers/{id}/status-override`. |
| Notes & activity endpoints L643–L685. |
| `admin_get_driver_rides(...)` — L698 | Rides by driver. |
| `admin_get_driver_daily_stats(...)` — L705 | Per-day stats. |
| `admin_assign_driver_area(...)` — L733 | PUT `/admin/drivers/{id}/area`. |
| `admin_get_driver_location_trail(...)` — L747 | GPS trail. |

Schemas: `DriverVerifyRequest` L92, `DriverActionRequest` L96, `DriverStatusOverride` L101, `DriverNoteCreate` L107.

### `routes/admin/rides.py`

| Symbol | Purpose |
|---|---|
| `admin_get_rides(...)` — L33 | GET `/admin/rides`. |
| `admin_get_active_rides(...)` — L71 | Live monitoring. |
| `admin_cancel_ride(...)` — L128 | Force-cancel. |
| `admin_get_stats(...)` — L216 | GET `/admin/stats`. |
| `admin_get_ride_stats(...)` — L273 | Detailed analytics. |
| `admin_get_ride_details(...)` — L339 | Full ride. |
| `admin_get_ride_location_trail(...)` — L348 | GPS history. |
| `admin_get_live_ride(...)` — L355 | Live update. |
| `admin_get_ride_invoice(...)` — L364 | Invoice. |
| `admin_get_ride_route_map(...)` — L412 | PNG. |
| `admin_get_heatmap_data(...)` — L490 | Pickup heatmap. |
| `admin_get_earnings(...)` — L561 | Financial summary. |
| `admin_export_rides(...)` — L602 | CSV/JSON. |
| `admin_export_drivers(...)` — L634 | CSV. |
| `admin_get_payouts(...)` — L667 | Payouts list. |
| `admin_get_payout_stats(...)` — L696 | Analytics. |

Schema: `AdminCancelRideRequest` L124.

---

## 4. Wallet, payments, loyalty, promotions

### `routes/wallet.py`

| Endpoint | Purpose |
|---|---|
| `GET /wallet` | Balance + pending. |
| `POST /wallet/topup` | Create Stripe PaymentIntent. |
| `GET /wallet/transactions` | Ledger. |
| `POST /wallet/debit` (internal) | Ride charge path. |

### `routes/payments.py`

Guards: reject raw card fields before JSON parse.

| Endpoint | Purpose |
|---|---|
| `POST /payments/setup-intent` | SetupIntent. |
| `POST /payments/payment-methods` | List cards. |
| `DELETE /payments/payment-methods/{id}` | Detach card. |
| `POST /payments/default-method` | Set default PM. |
| `POST /payments/ride-intent` | Ride PaymentIntent. |
| `POST /payments/topup-intent` | Top-up PaymentIntent. |

### `routes/webhooks.py`

| Symbol | Purpose |
|---|---|
| `POST /webhooks/stripe` | Signature verify; claim event; dispatch; mark processed. |

### `utils/payment_retry.py`

| Symbol | Purpose |
|---|---|
| `payment_retry_loop()` | Every 5 min. Retry `requires_payment_method` PaymentIntents with off-session confirm + exponential backoff. |

### `utils/email_receipt.py`

Asynchronous post-ride receipt render + send via configured SMTP/ESP.

### `utils/cloudinary.py`

Signed upload URLs for user-uploaded images (profile, vehicle photos, dispute evidence).

### `routes/loyalty.py`

| Endpoint | Purpose |
|---|---|
| `GET /loyalty/summary` | Tier + points + delta. |
| `POST /loyalty/redeem` | Redeem points. |
| `GET /loyalty/redemptions` | History. |

Tier thresholds: Bronze 0 / Silver 500 / Gold 2 000 / Platinum 10 000. Multipliers 1.0 / 1.25 / 1.5 / 2.0 on points earned.

### `routes/quests.py`

| Endpoint | Purpose |
|---|---|
| `GET /quests/active` | Active quests. |
| `GET /quests/history` | Completed/expired. |
| `POST /quests/{id}/claim` | Reward (wallet credit / badge). |

Quest types: `ride_count`, `earnings_target`, `online_hours`, `peak_rides`, `consecutive_days`, `rating_maintained`.

### `routes/promotions.py`

| Endpoint | Purpose |
|---|---|
| `GET /promotions/available` | Eligible now. |
| `POST /promotions/apply` | Validate + attach. |
| `GET /promotions/history` | Redemptions. |

10 validation rules documented in `WALLET_AND_PAYMENTS.md` §6.

### `routes/disputes.py`

| Endpoint | Purpose |
|---|---|
| `POST /disputes` | Open dispute on a completed ride. |
| `GET /disputes/{id}` | Thread. |
| `GET /disputes` | Mine. |
| `POST /disputes/{id}/message` | Add thread message. |

### `routes/notifications.py`

| Endpoint | Purpose |
|---|---|
| `GET /notifications` | Inbox. |
| `POST /notifications/{id}/read` | Mark read. |
| `GET /notifications/preferences` | Read prefs. |
| `PUT /notifications/preferences` | Update prefs. |
| `POST /notifications/push-token` | Register/unregister FCM token. |

### `routes/favorites.py`

Rider → favorite drivers; driver → favorite riders.

### `routes/addresses.py`

Rider saved addresses: home / work / custom.

### `routes/admin/wallet.py`

| Endpoint | Purpose |
|---|---|
| `GET /admin/wallet/{user_id}` | Balance + ledger. |
| `POST /admin/wallet/{user_id}/credit` | Manual credit. |
| `POST /admin/wallet/{user_id}/debit` | Manual debit. |
| `GET /admin/wallet/transactions/search` | Cross-user ledger search. |

### `routes/admin/promotions.py`

Full promo CRUD, targeting editor, usage analytics, push-to-segment.

### `routes/admin/subscriptions.py`

Spinr Pass plans + driver subscriptions.

### `routes/admin/messaging.py`

Broadcast push / SMS to segments.

---

## 5. Corporate B2B

See `docs/CORPORATE_B2B.md` for the subsystem in depth. Entry points:

- Routes: `routes/corporate_accounts.py`, `routes/corporate_wallet.py`.
- Service: `services/corporate_wallet_service.py`.
- Background: `utils/corporate_autotopup.py`, `utils/corporate_low_balance.py`.
- DB helpers: `db_supabase.get_corporate_wallet_by_company`, `ensure_corporate_wallet`, `list_wallets_needing_autotopup`, `sum_autotopups_today`, `list_wallets_low_balance_no_autotopup`, `mark_low_balance_notified`, `list_wallet_transactions`, `update_corporate_wallet_config`, plus the `corporate_wallet_apply_delta` RPC.
- Migration: `27_corporate_b2b_v1.sql`, `28_corporate_wallet_rpc.sql`.
- Validators: `validate_cra_business_number`, `validate_canadian_tax_region`, `validate_email_domain` in `validators.py`.

---

## 6. Platform & infra

### `server.py`

Mounts routers (`/api/v1/*` + parallel `/api/*` for admin/corporate). Configures logging, exception handlers, middleware.

### `core/lifespan.py`

Startup/shutdown orchestrator. Spawns 7 background loops; subscribes WS pub/sub; initializes Sentry.

### `core/middleware.py`

`CORSMiddleware`, `SecurityHeadersMiddleware`, `RelativeRedirectMiddleware`, `SlowAPI RateLimitMiddleware`, + exception handlers.

### `core/config.py`

`Settings` pydantic-settings class. Fail-fast validation in production.

### `db_supabase.py`

| Symbol | Purpose |
|---|---|
| `run_sync(func)` | Thread-pool + h2 retry. |
| `_serialize_for_api(data)` | ISO datetime recursion. |
| `get_rows`, `count_documents`, `find_one`, `insert_one`, `insert_many`, `update_one`, `delete_many`, `delete_one`, `rpc` | Generic CRUD. |
| `get_user_by_id`, `get_user_by_phone`, `create_user` | Users. |
| `get_driver_by_id`, `find_nearby_drivers`, `update_driver_location`, `set_driver_available`, `claim_driver_atomic` | Drivers. |
| `get_ride`, `insert_ride`, `update_ride`, `get_rides_for_user`, `get_rides_for_driver`, `get_ride_count_by_date_range`, `get_ride_details_enriched` | Rides. |
| `insert_otp_record`, `get_otp_record`, `verify_otp_record`, `delete_otp_record` | OTP. |
| `create_flag`, `create_complaint`, `resolve_complaint`, `create_lost_and_found`, … | Safety. |
| `get_flags_for_target`, `get_ride_location_trail`, `get_live_ride_data`, `get_user_status`, `get_driver_status_by_user` | Analytics. |
| `claim_stripe_event(event_id)`, `mark_stripe_event_processed(event_id)` | Stripe idempotency. |
| `get_all_corporate_accounts`, `get_corporate_account_by_id`, `insert_corporate_account`, `update_corporate_account`, `delete_corporate_account`, `list_corporate_accounts_filtered`, `update_corporate_account_status`, `record_kyb_decision`, `get_corporate_wallet_by_company`, `update_corporate_stripe_customer_id`, `ensure_corporate_wallet`, `get_corporate_members_for_user`, `list_wallets_needing_autotopup`, `sum_autotopups_today`, `get_default_payment_method`, `list_wallets_low_balance_no_autotopup`, `mark_low_balance_notified`, `list_wallet_transactions`, `update_corporate_wallet_config` | Corporate B2B. |

### `supabase_client.py`

Global `supabase` service-role client. HTTP/1.1 override on internal postgrest httpx client to dodge h2 GOAWAY issues.

### `schemas.py`

Pydantic v2 models. Full inventory in `INFRASTRUCTURE.md` §7 and the per-domain docs.

### `validators.py`

21 validators. See `INFRASTRUCTURE.md` §8 for the catalog.

### `utils/redis_client.py`

| Symbol | Purpose |
|---|---|
| `redis_get`, `redis_set`, `redis_incr`, `redis_expire`, `redis_delete`, `redis_delete_pattern` | Redis wrappers with in-process dict fallback. |

### `utils/rate_limiter.py`

SlowAPI `Limiter`, per-route presets (OTP 3/min, login 5/min, ride 10/min, driver location 60/min, upload 5/min, admin 100/min, general 30/min). SEC-008 OTP lockout via `redis_incr` on `otp:failures:{phone}`.

### `utils/error_handling.py`

`SpinrException` base + specialized subclasses. `ErrorCode` enum. `X-Request-ID` correlation.

### `features.py`

- `calculate_airport_fee(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng, stops)` — airport zone detection.
- Support tickets endpoints (user + admin).
- FAQ CRUD.
- Admin surge overrides.
- `send_push_notification(user_id, title, body, data)` via FCM.
- Shared `point_in_polygon`.

---

## 7. Admin ops

### `routes/admin/analytics.py`

Dashboards: cancellation reasons, acceptance rate, ride funnel, revenue.

### `routes/admin/monitoring.py`

| Endpoint | Purpose |
|---|---|
| `GET /admin/monitoring/drivers` | Live driver list. |
| `GET /admin/monitoring/rides` | Active rides. |

### `routes/admin/maintenance.py`

| Endpoint | Purpose |
|---|---|
| `POST /admin/maintenance/cleanup-location-history?days=30` | Purge old GPS + `online_idle`. |
| `POST /admin/maintenance/rollup-driver-daily?target_date=ISO` | Upsert per-day stats. |

### `routes/admin/settings.py`

Runtime-editable `app_settings` row: Stripe keys, Twilio creds, platform fees, matching algo, subscription flag, ToS + privacy text.

### `routes/admin/support.py`

Tickets: list / detail / reply / patch. User-side in `features.py`.

### `routes/admin/faqs.py`

CRUD for FAQ entries.

### `routes/admin/documents.py`

Driver document review: pending, approve, reject, download (signed URL), requirements.

---

## 8. Migrations (numbered)

Full catalog in `INFRASTRUCTURE.md` §16. Highlights:

- **Corporate B2B:** `27_corporate_b2b_v1.sql`, `28_corporate_wallet_rpc.sql`, `28_requirement_key.sql`.
- **Token rotation:** `25_refresh_tokens_and_token_version.sql`.
- **RLS gap fix:** `26_rls_coverage_gap.sql`.
- **Stripe idempotency:** `22_stripe_events.sql`.
- **Rider wallet:** `19_wallet.sql`.
- **Driver lifecycle state machine:** `12_driver_lifecycle_status.sql`.
- **Ride aggregates:** `15_ride_aggregate_columns.sql`.
- **Daily stats rollup:** `16_driver_daily_stats.sql`.
- **Complete schema consolidation:** `08_complete_schema.sql`.

---

## 9. Background loops summary

| Loop | File | Interval |
|---|---|---|
| `subscription_expiry` | `routes/drivers.py::check_expiring_subscriptions` | 6 h |
| `surge_engine` | `utils/surge_engine.py::surge_recalculation_loop` | 2 min |
| `scheduled_dispatcher` | `utils/scheduled_rides.py::scheduled_ride_dispatcher_loop` | 60 s |
| `payment_retry` | `utils/payment_retry.py::payment_retry_loop` | 5 min |
| `document_expiry` | `utils/document_expiry.py::document_expiry_loop` | 12 h |
| `corporate_autotopup` | `utils/corporate_autotopup.py::corporate_autotopup_loop` | 10 min |
| `corporate_low_balance` | `utils/corporate_low_balance.py::corporate_low_balance_loop` | 1 h |

---

## 10. Where to look first — cheat sheet

| Question | File |
|---|---|
| How does a ride get created? | `routes/rides.py::create_ride` |
| How does dispatch pick a driver? | `services/dispatch_service.py`, `routes/rides.py::match_driver_to_ride` |
| Where is the ride state machine enforced? | `routes/drivers.py` guards + `_require_ride_in_state` |
| How is a fare computed? | `routes/fares.py::get_fares_for_location` → `services/fare_service.py` |
| How does surge move? | `utils/surge_engine.py` |
| What does the Stripe webhook do? | `routes/webhooks.py` + `db_supabase.claim_stripe_event` |
| Where is OTP brute-force prevented? | `utils/rate_limiter.py` (SEC-008) + `routes/auth.py` |
| How does force-logout work? | `token_version` bump; `dependencies.py::_token_version_mismatch` |
| How do WebSocket messages cross replicas? | `utils/ws_pubsub.py` channel `spinr:ws:dispatch` |
| How are admins bootstrapped? | `ADMIN_EMAIL` / `ADMIN_PASSWORD` env + `routes/admin/auth.py` login (bcrypt w/ sha256 legacy upgrade) |
| Where is the corporate B2B surface? | `docs/CORPORATE_B2B.md` + `routes/corporate_*.py` + `services/corporate_wallet_service.py` |
