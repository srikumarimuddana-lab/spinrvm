# Infrastructure & Platform

Startup, middleware, config, DB client, schemas, validators, Redis, rate limiting, WS pub/sub, error handling, logging, and migrations.

**Files covered:**
`server.py`, `core/lifespan.py`, `core/middleware.py`, `core/config.py`, `db_supabase.py`, `supabase_client.py`, `schemas.py`, `validators.py`, `utils/redis_client.py`, `utils/rate_limiter.py`, `utils/ws_pubsub.py`, `utils/error_handling.py`, `features.py`, `migrations/*.sql`.

For auth-specific infra, see `AUTH_AND_USERS.md`. For realtime/WS semantics from the ride perspective, see `RIDES_AND_DISPATCH.md`.

---

## 1. Startup (`core/lifespan.py`)

```
1. init_database()                     → probe users table; blocks startup in prod on failure
2. app.state.db = db_supabase
3. spawn 7 asyncio background loops:
     subscription_expiry  (6 h)
     surge_engine         (2 m)
     scheduled_dispatcher (60 s)
     payment_retry        (5 m)
     document_expiry      (12 h)
     corporate_autotopup  (10 m)   ← corporate B2B
     corporate_low_balance(1 h)    ← corporate B2B
4. ws_pubsub.start(manager, WS_REDIS_URL)   subscribe "spinr:ws:dispatch"
5. Sentry init if SENTRY_DSN set           (10% traces, 10% profiles)
```

**Shutdown order:** cancel ws_pubsub → cancel all background tasks → release DB client. Reversal prevents zombie tasks from reaching a closed connection.

---

## 2. Middleware stack (`core/middleware.py`)

Registered innermost → outermost (request traverses outer → inner):

| Middleware | Role |
|---|---|
| `CORSMiddleware` | `ALLOWED_ORIGINS` env (comma-separated). Dev allows localhost; always allows `spinr-admin.vercel.app`. Wildcard + `credentials` rejected in production. |
| `SecurityHeadersMiddleware` | X-Frame-Options: DENY, X-Content-Type-Options: nosniff, HSTS `max-age=31536000; preload` prod-only, CSP `default-src 'none'` for JSON, relaxed for `/docs`. |
| `RelativeRedirectMiddleware` | Rewrites absolute `Location` headers to relative. Keeps 307 redirects from bypassing the Next.js proxy. |
| `SlowAPI RateLimitMiddleware` | Per-route limits backed by Redis (`RATE_LIMIT_REDIS_URL`). In-process `memory://` only in dev. |

### Exception handlers

| Exception | Handler | Body shape |
|---|---|---|
| `SpinrException` | `spinr_exception_handler` | `{success:false, error:{code, message, details, timestamp}}` |
| `RequestValidationError` | `validation_exception_handler` | 422 with field errors |
| `HTTPException` | `http_exception_handler` | JSON with CORS + `X-Request-ID` header |
| `Exception` | `general_exception_handler` | 500. Dev includes stack; prod scrubs to generic message. |

Every error response carries `X-Request-ID` (UUID hex[:12]) for log correlation.

---

## 3. Config (`core/config.py`)

`Settings` is a pydantic-settings model loading from env. Selected highlights — the full inventory is in the source.

| Setting | Env var | Default | Role |
|---|---|---|---|
| `SUPABASE_URL` | `SUPABASE_URL` | `""` | Supabase project; blocks prod startup on weak default. |
| `SUPABASE_SERVICE_ROLE_KEY` | same | `""` | Service-role JWT; bypasses RLS. Blocks prod startup on weak default. |
| `JWT_SECRET` | `JWT_SECRET` | weak placeholder | HS256 key for all our JWTs. ≥32 chars. Blocks prod startup on default. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | same | 15 | Rider/driver access TTL. |
| `ADMIN_ACCESS_TOKEN_TTL_HOURS` | same | 12 | Admin JWT TTL. |
| `REFRESH_TOKEN_EXPIRE_DAYS` | same | 30 | Refresh window. |
| `ALLOWED_ORIGINS` | same | dev-friendly list | Rejects `*` + credentials in prod. |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | same | weak placeholder | Blocks prod on defaults. |
| `RATE_LIMIT_REDIS_URL` / `WS_REDIS_URL` | same | `""` | Required in prod. |
| `FARE_CACHE_TTL_SECONDS` | same | 300 | Fare cache TTL. |
| `OTP_MAX_FAILURES` / `OTP_FAILURE_WINDOW_SECONDS` / `OTP_LOCKOUT_DURATION_SECONDS` | same | 5 / 3600 / 86400 | SEC-008 OTP brute-force guard. |
| `STORAGE_BUCKET` | same | `driver-documents` | Supabase Storage bucket. |
| `ENV` | same | `development` | Gates HSTS, error verbosity, config validation. |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | same | None | If unset, FCM push disabled (warning logged). |
| `SENTRY_DSN` | same | None | Optional APM. |

---

## 4. Router registration (`server.py`)

Mounted on `/api/v1` plus a parallel `/api/...` mount for admin / corporate paths used by the dashboard.

Public + mobile-facing: `auth`, `users`, `rides`, `fares`, `fare-split`, `drivers`, `drivers/documents`, `addresses`, `payments`, `notifications`, `promotions`, `disputes`, `favorites`, `loyalty`, `wallet`, `quests`, `webhooks`, `upload`, `support`, `pricing`, `settings`.

Admin: `admin/...` (drivers, rides, users, staff, wallet, promotions, subscriptions, messaging, monitoring, maintenance, faqs, settings, support, documents, analytics).

Corporate B2B: `corporate/accounts`, `corporate/wallet/*` under both `/api/v1/corporate/*` and `/api/corporate/*`. See `docs/CORPORATE_B2B.md`.

WebSocket: `/ws/{client_type}/{client_id}` — see `RIDES_AND_DISPATCH.md` §10.

---

## 5. Supabase DB layer (`db_supabase.py`)

~66 helpers wrapping `supabase-py`. Highlights:

### Core primitives

| Function | Purpose |
|---|---|
| `run_sync(func)` | Execute sync Supabase calls in a thread pool. Retries once on `h2.ConnectionTerminated` / `httpcore.RemoteProtocolError` (HTTP/2 GOAWAY). |
| `_serialize_for_api(data)` | Recursively ISO-format datetime/date. |
| `_single_row_from_res` / `_rows_from_res` | Extract `.data` from Supabase API responses. |

### Generic CRUD

`get_rows(table, filters, *, select, order, limit, offset)`, `count_documents(table, filters)`, `find_one(table, filters)`, `insert_one`, `insert_many`, `update_one`, `delete_many`, `delete_one`, `rpc(name, payload)`.

### Domain helpers (by group)

- **Users:** `get_user_by_id`, `get_user_by_phone`, `create_user`.
- **Drivers:** `get_driver_by_id`, `find_nearby_drivers`, `update_driver_location`, `set_driver_available`, `claim_driver_atomic`.
- **Rides:** `get_ride`, `insert_ride`, `update_ride`, `get_rides_for_user`, `get_rides_for_driver`, `get_ride_count_by_date_range`, `get_ride_details_enriched`.
- **OTP:** `insert_otp_record`, `get_otp_record`, `verify_otp_record`, `delete_otp_record`.
- **Flags/Complaints/Lost+found:** `create_flag`, `create_complaint`, `resolve_complaint`, `create_lost_and_found`, …
- **Analytics:** `get_flags_for_target`, `get_ride_location_trail`, `get_live_ride_data`, `get_user_status`, `get_driver_status_by_user`.
- **Stripe idempotency:** `claim_stripe_event(event_id)`, `mark_stripe_event_processed(event_id)`.
- **Corporate:** `get_all_corporate_accounts`, `get_corporate_account_by_id`, `insert_corporate_account`, `update_corporate_account`, `delete_corporate_account`, `list_corporate_accounts_filtered`, `update_corporate_account_status`, `record_kyb_decision`, `get_corporate_wallet_by_company`, `update_corporate_stripe_customer_id`, `ensure_corporate_wallet`, `get_corporate_members_for_user`, `list_wallets_needing_autotopup`, `sum_autotopups_today`, `get_default_payment_method`, `list_wallets_low_balance_no_autotopup`, `mark_low_balance_notified`, `list_wallet_transactions`, `update_corporate_wallet_config`, plus the `corporate_wallet_apply_delta` RPC helper.

Every helper is `async` even when it only wraps sync calls — it goes through `run_sync` so FastAPI's event loop is never blocked.

---

## 6. Supabase client (`supabase_client.py`)

```
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
```

Single global instance. Key detail: the internal `postgrest` httpx client is replaced with HTTP/1.1 to avoid `h2.ConnectionTerminated` on stream exhaustion — this was a recurring cause of 500s in earlier deploys. Keep that replacement when upgrading the library.

RLS policies are defined in migrations; the backend bypasses them via service role. Any code path that proxies requests on behalf of a mobile user must still enforce access checks in Python.

---

## 7. Schemas (`schemas.py`)

Pydantic v2 models for request/response/internal DTOs. Representative:

| Model | Group | Notes |
|---|---|---|
| `SendOTPRequest`, `VerifyOTPRequest` | Auth | Request bodies. |
| `CreateProfileRequest`, `UserProfile` | User | Profile completion + public shape. |
| `OTPRecord` | Auth | Internal; stored in `otp_records`. |
| `RefreshTokenRequest`, `AuthResponse` | Auth | Token exchange. |
| `AppSettings` | Config | Runtime admin-editable settings (Stripe keys, matching algorithm, platform fee %, cancel fees, legal text). |
| `ServiceArea`, `VehicleType`, `FareConfig` | Geo/Pricing | Internal. |
| `Driver` | Driver | Profile + vehicle + docs + live state. |
| `Ride`, `CreateRideRequest`, `RideRatingRequest` | Ride | Complete ride record + request bodies with validators (length / lat-lng range). |
| `SavedAddress` / `SavedAddressCreate` | User | Saved locations. |

Corporate B2B schemas live alongside in `schemas.py` and in the corporate route modules. See `docs/CORPORATE_B2B.md` §4.

---

## 8. Validators (`validators.py`)

21 pure validators. Return `(ok, normalized_value)` or raise for strict contexts. Highlights:

| Validator | Rule |
|---|---|
| `validate_phone` | E.164 (`+[country][number]`, min 7 digits). Normalizes bare numbers. |
| `validate_email` | RFC 5322 simplified; max 254 chars. |
| `validate_coordinates` | lat ∈ [-90, 90], lng ∈ [-180, 180]; warns on null island. |
| `validate_monetary_amount` | Decimal-backed, 2 dp, min/max, optional `allow_zero`. |
| `validate_uuid` / `validate_id` | UUID v4 / alphanumeric-dash-underscore (max 64). |
| `sanitize_string` | Strips HTML by default; logs (does not throw) on SQL/XSS patterns. |
| `validate_datetime` | ISO / epoch / common formats; optional past/future gates. |
| `validate_address` | ≥5 chars, at least one alphanumeric. |
| `validate_ride_location` | Both pickup + dropoff valid and distinct. |
| **Corporate-specific:** `validate_cra_business_number`, `validate_canadian_tax_region`, `validate_email_domain` | Used for KYB, tax region, email-domain SSO gates. |

Pydantic adapters: `pydantic_phone_validator`, `pydantic_email_validator`, `pydantic_coordinates_validator` — use these inside field_validators instead of reimplementing.

---

## 9. Redis client (`utils/redis_client.py`)

Transparent in-process fallback when `REDIS_URL` unset. Production must set Redis or rate-limit / pub/sub do not work.

| Function | Fallback |
|---|---|
| `redis_get(key)` | in-proc dict |
| `redis_set(key, value, ttl)` | `_local_set` with `time.monotonic() + ttl` |
| `redis_incr(key)` | `_local_incr` (not multi-process safe) |
| `redis_expire(key, ttl)` | `_local_expire` |
| `redis_delete(key)` | `_local_delete` |
| `redis_delete_pattern(pattern)` | fnmatch-based glob |

Uses: rate-limit counters, OTP lockout state, fare cache, ad-hoc caches.

---

## 10. Rate limiter (`utils/rate_limiter.py`)

SlowAPI `Limiter` with Redis storage. Key functions: `get_phone_based_key` (phone hash), `get_client_identifier` (user id or IP).

| Endpoint | Limit | Key |
|---|---|---|
| OTP send / verify | 3/min | phone |
| Login | 5/min | user id / IP |
| General API | 30/min | IP |
| Ride create | 10/min | IP |
| Driver location | 60/min | IP |
| Doc upload | 5/min | IP |
| Admin | 100/min | IP |

SEC-008 OTP lockout: `otp:failures:{phone}` counter with 1 h sliding window; block for 24 h after 5 failures.

In `ENV=production`, empty `RATE_LIMIT_REDIS_URL` blocks startup — in-process is not safe across replicas.

---

## 11. WebSocket pub/sub (`utils/ws_pubsub.py`)

Channel: `spinr:ws:dispatch`. Every replica subscribes. Producer publishes `{client_id, message}`; consumer on each replica delivers locally iff the target is present.

| Member | Behavior |
|---|---|
| `active` | True iff Redis + consumer task both alive. |
| `start(manager, redis_url)` | Connect, subscribe, spawn consumer. Returns False if Redis unreachable (dev fallback). |
| `publish(client_id, message)` | Publish JSON to channel. Returns False when inactive so caller can fall back to local. |
| `_consumer()` | Long-running task; forwards messages to `manager._deliver_local` when the client is local; drops otherwise. |
| `stop()` | Cancel consumer, close connection. |
| `resolve_ws_redis_url()` | Prefers `WS_REDIS_URL`; falls back to `RATE_LIMIT_REDIS_URL`. |

**Production invariant:** with sticky LB but multi-replica, lack of pub/sub silently drops ~50% of messages. Required in prod.

---

## 12. Error handling (`utils/error_handling.py`)

`SpinrException` hierarchy with `ErrorCode` enum:

- `AuthenticationException` (400x) — invalid token, OTP expired, credentials, permissions, account disabled.
- `ValidationException` (200x) — invalid format, missing field, out-of-range.
- `ResourceNotFoundException` (300x) — ride/driver/user not found, already exists, conflict.
- `RideException` (400x) — invalid status transition, no drivers, price mismatch.
- `DriverException` (500x) — not available, offline, documents rejected.
- `PaymentException` (600x) — failed, invalid method, insufficient funds.
- `InternalErrorException` (900x) — service unavailable, rate limit, external service error.

All 4xx/5xx responses include `success:false, error:{code, message, details, timestamp}` and top-level `detail` (legacy mobile compat). CORS headers + `X-Request-ID` attached in the generic 500 handler. Loguru is called with `opt(raw=True)` to avoid format-string crashes on tracebacks containing `{}`.

---

## 13. Logging

Loguru JSON to stderr, captured by Railway:

```
{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} | {message}
```

No local file sink (ephemeral disk on Railway). Special logger: `diag_logger` for diagnostics
(`socket_manager.py`, `routes/drivers/_deps.py`, `routes/rides/cancellation.py`).

`logging_utils._goonline_logger` also exists but **is imported by nothing** — the `[GO-ONLINE]`
lines you see in production come from loguru with the tag hardcoded in the message, not from
that logger. Both it and `diag_logger` set `propagate = False`, so `caplog` cannot see either.

**Never log secrets** — the JWT secret, Stripe keys, Twilio tokens, service role key. Error handlers scrub details in prod.

**Never log personal information**, and prefer an allowlist over a denylist when logging
anything derived from a DB row. `utils/pii.py` provides `geohash()` (coarse area from
coordinates), `redact_phone()`, `redact_email()`, `area_only()`, and `first_name_only()` — use
them at the emission site. Two worked examples in the DB layer: `repositories/_base.py`'s
`_log_safe_write()` (key names + geohash, never values) and `_redact_pg_error()` (Postgres
error text carries `Key (col)=(value)` and `Failing row contains (…)`, so it is redacted before
reaching either the log or the exception `details`).

Note that `.claude/hooks/pre-commit`'s "PII in logs" step is a small source-text denylist. It
cannot see a runtime-interpolated payload — e.g. `logger.info(f"... payload={row}")` — so a
green result there is not evidence that a log line is safe.

---

## 14. SMS (`sms_service.py`)

```
send_sms(to, body, twilio_sid, twilio_token, twilio_from) → {"success": true, "provider": "twilio"|"console", "sid": ...}
send_otp_sms(phone, code, …) → thin wrapper composing the template
```

Twilio creds come from `AppSettings` (admin-editable at runtime). If unset, dev falls back to console log — tests and local runs do not need Twilio.

---

## 15. Features module (`features.py`)

High-level feature endpoints that do not fit neatly into a domain file:

- **Airport fees** — `calculate_airport_fee(...)` ray-casts pickup/dropoff/stops against airport polygons; returns `{airport_fee, airport_zone_name, is_pickup, is_dropoff, is_stop}`.
- **Support tickets** — user and admin CRUD (mounted under `/support` and `/admin/support`).
- **FAQs** — CRUD with category + sort_order + is_active.
- **Surge admin** — GET/PATCH surge_multiplier per service area (manual override).
- **Push notifications** — `send_push_notification(user_id, title, body, data)` via FCM when `FIREBASE_SERVICE_ACCOUNT_JSON` is configured.
- **Point-in-polygon** — shared with `geo_utils.py`.

---

## 16. Migrations (`backend/migrations/`)

Numbered, append-only SQL. `00_schema_migrations_table.sql` is the tracking table; `24_schema_migrations.sql` adds the version column used by later migrations.

Groupings:

- **Core & driver (01–17)** — driver FK, dynamic documents, service areas, Stripe payments, corporate accounts v0, FCM, promotions v0, full schema consolidation (`08_complete_schema.sql`), service-area subregions, subscription plans, disputes, driver matching, driver lifecycle state machine, notes/activity log, ride aggregates, driver daily stats rollup, corporate FKs.
- **Admin & wallet (18–22)** — admin staff, rider wallet + ledger, quests, loyalty, Stripe events idempotency.
- **Profile & migrations (23–24)** — profile image URL, schema_version column.
- **Token rotation & RLS (25–26)** — refresh tokens + `token_version`, RLS coverage gap fix.
- **Corporate B2B (27–28b)** — KYB/autotopup/invoice, `corporate_wallet_apply_delta` RPC, requirement_key normalization.
- **Hardening (29–31)** — wallet FKs & admin types, identity audit, driver dedup + constraints.

Running: `supabase db push` or the project-specific script. Always author forward-only; revert with a new migration, not an edit.

---

## 17. Common tasks

| Task | Where |
|---|---|
| Add a new env var | `core/config.py` Settings class; document in this file §3; add to Railway variables. |
| Add a new background loop | `core/lifespan.py` — `_spawn(…)` call + cancellation at shutdown. Ensure loop is idempotent or guards against multi-replica double-fire. |
| Add a new rate-limit preset | `utils/rate_limiter.py` — define a limiter, decorate the route. |
| New migration | Next integer filename in `migrations/`; forward-only; update RLS policies if adding new user-facing tables. |
| Tighten CSP on a route | `core/middleware.py` SecurityHeadersMiddleware special-case (the `/docs` relaxation shows the pattern). |
| Add a new Pydantic model | `schemas.py` if shared; otherwise colocate with the route module. |
