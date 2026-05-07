# Spinr API Reference

**Base URL:** `https://spinr-api.railway.app` (production) · `http://localhost:8000` (dev)

**Interactive docs:** `GET /docs` (Swagger UI) · `GET /redoc` (ReDoc) · `GET /openapi.json` (raw spec)

**API version prefix:** `/api/v1` — all endpoints below are relative to this prefix unless noted.

**Authentication:** `Authorization: Bearer <access_token>` on every authenticated request.
Access tokens expire in 15 minutes; use `POST /auth/refresh` to renew.

---

## Authentication — `/auth`

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/auth/send-otp` | — | Send a 6-digit OTP via SMS to the given phone number |
| POST | `/auth/verify-otp` | — | Verify OTP; returns `access_token`, `refresh_token`, `expires_in`, `user` |
| POST | `/auth/firebase` | — | Exchange a Firebase ID token for a Spinr JWT pair |
| GET  | `/auth/me` | ✓ | Return the authenticated user's profile |
| POST | `/auth/refresh` | — | Exchange a refresh token for a new access + refresh token pair |
| POST | `/auth/logout` | ✓ | Revoke the presented refresh token; deletes Redis session key |
| POST | `/auth/logout-all` | ✓ | Bump `token_version`; invalidates all outstanding tokens for the user |

**`POST /auth/send-otp`**
```json
// Request
{ "phone": "+13061234567" }

// Response 200
{ "message": "OTP sent", "dev_otp": "1234" }  // dev_otp only when ENV != production
```

**`POST /auth/verify-otp`**
```json
// Request
{ "phone": "+13061234567", "code": "123456" }

// Response 200
{
  "access_token": "eyJ...",
  "refresh_token": "abc...",
  "expires_in": 900,
  "token_type": "bearer",
  "user": { "id": "uuid", "phone": "+13061234567", "role": "rider", "profile_complete": false }
}
```

**`POST /auth/refresh`**
```json
// Request
{ "refresh_token": "abc..." }

// Response 200
{ "access_token": "eyJ...", "refresh_token": "def...", "expires_in": 900 }
```

---

## Rides — `/rides`

| Method | Path | Auth | Role | Description |
|--------|------|------|------|-------------|
| POST | `/rides/estimate` | ✓ | rider | Get fare estimates for all vehicle types |
| POST | `/rides` | ✓ | rider | Create (book) a ride — idempotent via `Idempotency-Key` header |
| GET  | `/rides/active` | ✓ | rider | Fetch the caller's current active ride |
| GET  | `/rides/history` | ✓ | rider | Paginated ride history (`?limit=20&offset=0`) |
| GET  | `/rides/{ride_id}` | ✓ | rider | Fetch a single ride by ID |
| POST | `/rides/{ride_id}/cancel` | ✓ | rider | Cancel a ride (allowed before `TRIP_STARTED`) |
| POST | `/rides/{ride_id}/rate` | ✓ | rider | Submit a driver rating (1–5) + optional tip |
| POST | `/rides/{ride_id}/tip` | ✓ | rider | Add a post-trip tip |
| POST | `/rides/{ride_id}/emergency` | ✓ | rider | Trigger SOS; notifies emergency contacts + admin |
| POST | `/rides/{ride_id}/stops` | ✓ | rider | Add an intermediate stop to an active ride |
| DELETE | `/rides/{ride_id}/stops/{stop_index}` | ✓ | rider | Remove an intermediate stop |
| GET  | `/rides/{ride_id}/receipt` | ✓ | rider | Fetch itemised trip receipt |
| GET  | `/rides/{ride_id}/share` | ✓ | rider | Get a live-tracking share link |
| POST | `/rides/{ride_id}/share` | ✓ | rider | Add a shared contact for live tracking |
| GET  | `/rides/{ride_id}/shared-contacts` | ✓ | rider | List contacts receiving live tracking |
| GET  | `/rides/track/{share_token}` | — | — | Public live-tracking endpoint (no auth) |
| GET  | `/rides/{ride_id}/messages` | ✓ | rider/driver | Fetch in-trip chat history |
| POST | `/rides/{ride_id}/messages` | ✓ | rider/driver | Send an in-trip chat message |
| GET  | `/rides/{ride_id}/chat-status` | ✓ | rider | Check whether chat is open |
| GET  | `/rides/{ride_id}/call` | ✓ | rider | Get a masked phone number for in-trip calling |
| GET  | `/rides/scheduled` | ✓ | rider | List upcoming scheduled rides |
| DELETE | `/rides/scheduled/{ride_id}` | ✓ | rider | Cancel a scheduled ride |

**`POST /rides/estimate`**
```json
// Request
{
  "pickup_lat": 50.4452,  "pickup_lng": -104.6189,
  "dropoff_lat": 50.4580, "dropoff_lng": -104.6035
}

// Response 200
[
  { "vehicle_type_id": "uuid", "name": "Economy", "fare": "12.50", "eta_minutes": 4 },
  { "vehicle_type_id": "uuid", "name": "Comfort",  "fare": "16.75", "eta_minutes": 6 }
]
```

**`POST /rides`**
```json
// Request  (header: Idempotency-Key: <client-uuid>)
{
  "pickup_address":  "123 Main St, Regina",
  "pickup_lat":      50.4452,
  "pickup_lng":     -104.6189,
  "dropoff_address": "456 Broad St, Regina",
  "dropoff_lat":     50.4580,
  "dropoff_lng":    -104.6035,
  "vehicle_type_id": "uuid",
  "payment_method":  "wallet",   // "wallet" | "card"
  "payment_method_id": null,     // Stripe PaymentMethod ID when payment_method = "card"
  "stops": [],
  "scheduled_time": null,        // ISO-8601 UTC for pre-booking, null for immediate
  "scheduled_timezone": null,    // IANA tz name, required when scheduled_time is set
  "estimate_token": "abc..."     // opaque token from /estimate to lock in the fare
}

// Response 201
{ "id": "uuid", "status": "searching", "pickup_otp": "7823", ... }
```

**Ride status machine:**
```
searching → driver_assigned → driver_accepted → driver_arrived
         → trip_started → trip_completed
         ↘ cancelled  (valid before trip_started)
```

---

## Drivers — `/drivers`

### Driver profile & configuration

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET  | `/drivers/config` | ✓ | Fetch driver app config (ride offer timeout, pickup radius) |
| GET  | `/drivers/me` | ✓ (driver) | Fetch the driver's profile |
| PUT  | `/drivers/me` | ✓ (driver) | Update driver profile |
| POST | `/drivers/register` | ✓ | Register a new driver account |
| POST | `/drivers/push-token` | ✓ | Register / refresh an FCM push token |
| POST | `/drivers/status` | ✓ (driver) | Set online / offline status |
| POST | `/drivers/destination` | ✓ (driver) | Set a preferred dropoff destination filter |
| DELETE | `/drivers/destination` | ✓ (driver) | Clear destination filter |
| GET  | `/drivers/destination` | ✓ (driver) | Fetch current destination filter |
| POST | `/drivers/location-batch` | ✓ (driver) | Batch-update driver GPS (called every 3 s) |
| GET  | `/drivers/nearby` | ✓ (rider) | List drivers near a coordinate (`?lat=&lng=&vehicle_type=`) |
| POST | `/drivers/me/export-data` | ✓ (driver) | Request a PIPEDA data export |

### Earnings

| Method | Path | Description |
|--------|------|-------------|
| GET | `/drivers/earnings?period=day\|week\|month` | Summary earnings for a period |
| GET | `/drivers/earnings/daily?start=&end=` | Day-by-day breakdown |
| GET | `/drivers/earnings/trips?limit=&offset=` | Per-trip earnings list |
| GET | `/drivers/earnings/weekly` | Rolling 52-week chart data |
| GET | `/drivers/earnings/monthly` | Rolling 12-month chart data |
| GET | `/drivers/earnings/comparison` | Current vs. previous period comparison |
| GET | `/drivers/earnings/export` | Download CSV of trip earnings |
| GET | `/drivers/t4a/{year}` | Canadian T4A tax summary for a given year |

### Payouts & banking

| Method | Path | Description |
|--------|------|-------------|
| GET | `/drivers/bank-account` | Fetch linked bank account |
| POST | `/drivers/bank-account` | Save bank account (Stripe external account) |
| DELETE | `/drivers/bank-account` | Remove bank account |
| POST | `/drivers/stripe-onboard` | Start Stripe Connect onboarding; returns an account link URL |
| POST | `/drivers/payouts` | Request an instant payout |
| GET | `/drivers/payouts` | List payout history |

### Active ride lifecycle (driver side)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/drivers/rides/active` | Fetch the driver's current active ride |
| GET | `/drivers/rides/history` | Paginated ride history |
| POST | `/drivers/rides/{ride_id}/accept` | Accept an offered ride |
| POST | `/drivers/rides/{ride_id}/decline` | Decline an offered ride |
| POST | `/drivers/rides/{ride_id}/arrive` | Signal arrival at pickup |
| POST | `/drivers/rides/{ride_id}/verify-otp` | Verify rider's pickup OTP to start the trip |
| POST | `/drivers/rides/{ride_id}/start` | Start the trip |
| POST | `/drivers/rides/{ride_id}/complete` | Complete the trip |
| POST | `/drivers/rides/{ride_id}/cancel` | Cancel the ride (`?reason=<string>`) |
| POST | `/drivers/rides/{ride_id}/rate-rider` | Rate the rider after trip completion |

---

## Payments — `/payments`

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/payments/create-intent` | ✓ | Create a Stripe PaymentIntent for a ride |
| POST | `/payments/confirm` | ✓ | Confirm payment and update ride payment status |
| POST | `/payments/setup-intent` | ✓ | Create a Stripe SetupIntent to save a card |
| GET  | `/payments/methods` | ✓ | List saved Stripe payment methods |
| GET  | `/payments/cards` | ✓ | List saved cards |
| POST | `/payments/cards` | ✓ | Add a new card via Stripe PaymentMethod ID |
| POST | `/payments/cards/{card_id}/default` | ✓ | Set a card as default |
| DELETE | `/payments/cards/{card_id}` | ✓ | Remove a saved card |

---

## Wallet — `/wallet`

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET  | `/wallet` | ✓ | Fetch wallet balance and status |
| POST | `/wallet/top-up` | ✓ | Top up wallet via Stripe (`amount`, `payment_method_id`) |
| POST | `/wallet/pay` | ✓ | Deduct wallet balance for a ride (`ride_id`, `amount`) |
| GET  | `/wallet/transactions` | ✓ | Paginated ledger (`?limit=20`) |
| POST | `/wallet/transfer` | ✓ (admin) | Internal wallet transfer between users |

---

## WebSocket — `/ws`

**Endpoint:** `wss://<host>/ws/{user_id}`

**Auth handshake (first message):**
```json
{ "type": "auth", "token": "<access_token>" }
```

**Server → client events:**

| `type` | When | Payload fields |
|--------|------|---------------|
| `ride_offered` | Dispatch assigns a ride to a driver | `ride_id`, `fare`, `pickup_address`, `dropoff_address`, `rider_name`, `rider_rating`, `distance_km`, `duration_minutes`, `timeout_seconds` |
| `ride_status_update` | Ride state changes | `ride_id`, `status`, `driver_lat`, `driver_lng` |
| `driver_location` | Driver moves (sent to rider) | `ride_id`, `lat`, `lng`, `speed`, `heading` |
| `ride_taken` | Accepted ride was already taken by another driver | `ride_id` |
| `chat_message` | In-trip chat | `ride_id`, `sender_role`, `content`, `timestamp` |
| `emergency_ack` | SOS acknowledgement | `ride_id` |
| `ping` | Keep-alive (every 30 s) | — |

**Client → server events:**

| `type` | Description |
|--------|-------------|
| `location_update` | Driver GPS (`lat`, `lng`, `speed`, `heading`) |
| `chat_message` | In-trip chat message |
| `pong` | Reply to `ping` |

**Limits:** 30 messages/second per connection · 64 KB max message size.

---

## Admin API — `/api/admin`

Admin endpoints require a JWT with `role` ∈ `{admin, super_admin, operations, support, finance, custom}`.

Key domains (full spec available at `/docs` when running the server):

| Prefix | Covers |
|--------|--------|
| `/api/admin/auth` | Admin login, session management |
| `/api/admin/drivers` | Driver approval, suspension, document review |
| `/api/admin/rides` | Ride search, manual override, refunds |
| `/api/admin/users` | User management, PIPEDA data deletion |
| `/api/admin/analytics` | KPI dashboard, heatmap, revenue charts |
| `/api/admin/service-areas` | Geofence management, surge multiplier |
| `/api/admin/settings` | App config (Stripe keys, fare rules, feature flags) |
| `/api/admin/wallet` | Balance adjustments, payout overrides |
| `/api/admin/promotions` | Promo codes, referral campaigns |
| `/api/admin/corporate` | Company management, billing, allowances |
| `/api/admin/staff` | Admin staff accounts and module permissions |

---

## Error responses

All errors follow:
```json
{ "detail": "<human-readable message>" }
```

| Status | Meaning |
|--------|---------|
| 400 | Bad request / validation failure |
| 401 | Missing or expired token; `ERR_SESSION_EXPIRED` / `ERR_SESSION_REVOKED` detail strings are machine-readable |
| 403 | Authenticated but not authorised (wrong role / module) |
| 404 | Resource not found |
| 409 | Conflict — e.g. ride already accepted |
| 422 | Pydantic validation error (field-level errors in `detail` array) |
| 429 | Rate limited — `Retry-After` header is set |
| 500 | Internal error — full details logged server-side; generic message returned to client |
| 503 | Service degraded (DB unreachable) |

---

## Rate limits

| Endpoint group | Limit |
|----------------|-------|
| `POST /auth/send-otp` | 5 / 10 min per phone number |
| `POST /auth/verify-otp` | 5 failures / hour → 24-hour lockout |
| `POST /auth/logout` | 3 / min |
| Most read endpoints | 60 / min per user |

Rate limiting uses Redis for shared state across replicas. Key is `user:{id}` when authenticated, client IP otherwise.

---

## Correlation IDs

Every response includes `X-Request-ID`. Supply the same header on requests to have your own trace ID echoed back — useful for mobile client log correlation.

---

## Webhooks — `/webhooks/stripe`

Stripe delivers payment events here. The endpoint verifies `Stripe-Signature` and is idempotent (events are claimed in `stripe_events` before processing). No consumer setup required.
