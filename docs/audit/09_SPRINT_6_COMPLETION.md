# Sprint 6 Completion Report

**Date:** 2026-04-09  
**Sprint Goal:** Close highest-value security and code-quality gaps from the Fortune 100 audit: short-lived JWT access tokens with refresh rotation, OTP hashing, fare decimal arithmetic, and a persistent in-ride SOS button.

---

## Branches Delivered

| Branch | Issues Closed | Status |
|--------|--------------|--------|
| `sprint6/jwt-refresh` | SEC-014, SEC-015 | ✅ Merged |
| `sprint6/otp-hardening` | SEC-016, SEC-017 | ✅ Merged |
| `sprint6/decimal-money` | CQ-009 | ✅ Merged |
| `sprint6/sos-button` | FEAT-001 | ✅ Merged |

---

## Branch 1 — `sprint6/jwt-refresh` (SEC-014, SEC-015)

### Problem
Access tokens had a 30-day lifetime hardcoded in `create_jwt_token()`. The `ACCESS_TOKEN_EXPIRE_MINUTES` config value was imported but never used. There was no refresh mechanism — a stolen token could not be revoked for 30 days.

### Solution

**`backend/core/config.py`**
- `ACCESS_TOKEN_EXPIRE_MINUTES = 15` — production default
- `REFRESH_TOKEN_EXPIRE_DAYS = 30` — long-lived refresh window

**`backend/dependencies.py`**
- `create_jwt_token()` now uses `timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)`
- `create_refresh_token(user_id)` generates a 32-byte URL-safe opaque token via `secrets.token_urlsafe(32)`
- `hash_token(raw)` — SHA-256 of raw token for safe DB storage

**`backend/routes/auth.py`**
- `verify_otp`: issues access + refresh token pair; stores SHA-256 hash in `refresh_tokens` collection
- `POST /auth/refresh`: validates hash, checks revoked/expiry, rotates both tokens (old revoked before new pair issued), returns updated `AuthResponse`

**`backend/schemas.py`**
- `AuthResponse` extended: `refresh_token: str`, `expires_in: int`
- New `RefreshTokenRequest` schema

**`shared/api/client.ts`**
- `setRefreshCallback(fn)` — authStore registers its refresh fn during `initialize()`
- `handleApiError` intercepts 401: calls refresh callback, deduplicates concurrent refreshes via `_refreshPromise`, retries original request once with new token; falls through to `logout()` if refresh fails

**`shared/store/authStore.ts`**
- New state: `refreshToken`, `tokenExpiresAt`
- `setTokens()` — persists both tokens to SecureStore/localStorage + in-memory
- `refreshTokens()` — calls `POST /auth/refresh`, rotates tokens, returns `bool`
- `initialize()` registers `setRefreshCallback(() => get().refreshTokens())`
- `logout()` purges `refresh_token` and `token_expires_at` from storage

**`rider-app/app/otp.tsx` / `driver-app/app/otp.tsx`**
- Extract `refresh_token` and `expires_in` from verify-otp response and persist via `setTokens()`

### Security Posture Improvement
- Access token lifetime: 30 days → **15 minutes**
- Refresh tokens stored as SHA-256 hashes (never raw)
- Token rotation on every refresh (replay protection)
- Silent 401 recovery in all API calls (no user-visible re-login on refresh)

---

## Branch 2 — `sprint6/otp-hardening` (SEC-016, SEC-017)

### Problem
OTP codes stored in plain text in `otp_records` collection. Anyone with DB read access could enumerate valid login codes. Address fields in `CreateRideRequest` accepted unlimited arbitrary strings with no coordinate bounds check.

### Solution

**`backend/utils/crypto.py`** (new file)
```python
import hashlib
def hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()
```

**`backend/routes/auth.py`**
- `send_otp`: stores `hash_otp(otp_code)` instead of plaintext
- `verify_otp`: queries `{'code': hash_otp(code)}` — plaintext code never hits DB
- Dev bypass (`'1234'`): consistent hash path even in dev mode
- Phone number masked to last-4 digits in dev log

**`backend/schemas.py` — `CreateRideRequest` validators**
```python
@validator('pickup_address', 'dropoff_address')
def validate_address(cls, v):
    v = v.strip()
    if len(v) < 3: raise ValueError('Address must be at least 3 characters')
    if len(v) > 500: raise ValueError('Address must be 500 characters or fewer')
    return v

@validator('pickup_lat', 'dropoff_lat')
def validate_lat(cls, v):
    if not (-90.0 <= v <= 90.0): raise ValueError('Latitude must be between -90 and 90')
    return v

@validator('pickup_lng', 'dropoff_lng')
def validate_lng(cls, v):
    if not (-180.0 <= v <= 180.0): raise ValueError('Longitude must be between -180 and 180')
    return v
```

### Security Posture Improvement
- OTP codes: stored in plain text → **SHA-256 hash only** (pre-image resistant)
- Address injection surface: unlimited string → **3–500 chars, stripped**
- Coordinate spoofing: unchecked → **geodetically valid bounds enforced**

---

## Branch 3 — `sprint6/decimal-money` (CQ-009)

### Problem
All fare arithmetic in `fares.py` and `rides.py` used raw Python `float`. IEEE 754 double precision produces silent rounding errors on currency values (e.g. `0.1 + 0.2 = 0.30000000000000004`). A $12.35 fare could silently become $12.349999999... in the DB.

### Solution

**`backend/routes/rides.py`**
```python
from decimal import Decimal, ROUND_HALF_UP

_TWO_PLACES = Decimal('0.01')

def _d(v) -> Decimal:
    return Decimal(str(v))          # float → Decimal without drift

def _round(v: Decimal) -> Decimal:
    return v.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)

def _f(v: Decimal) -> float:
    return float(v)                 # Decimal → float at JSON boundary
```
Both `estimate_ride` and `create_ride` fare blocks now use `_d()/_round()/_f()`.

**`backend/routes/fares.py`**
```python
def _fd(v) -> float:
    return float(Decimal(str(v)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))
```
All default fare literals (`3.50`, `1.50`, etc.) and DB-sourced values are normalised through `_fd()` before being returned to callers.

### Design Notes
- No schema changes — Pydantic models retain `float` fields; conversion is internal
- `Decimal(str(float_val))` is the safe conversion (avoids `Decimal(0.1)` drift)
- Rounding is applied at each intermediate step, not just the total
- `_f()` converts back to `float` only at the JSON serialisation boundary

---

## Branch 4 — `sprint6/sos-button` (FEAT-001)

### Problem
The `SOSButton` component and `POST /rides/{ride_id}/emergency` endpoint existed from Sprint 4's rider-app-safety work. The button was wired into the bottom sheet action row on `ride-in-progress.tsx` but was inaccessible when the sheet was collapsed to its minimum snap point — exactly the scenario where a rider needs it most.

### Solution

**`rider-app/app/ride-in-progress.tsx`**
- Added a second `SOSButton` rendered as an absolute-positioned `SafeAreaView` overlay at `top: 0, right: 16, zIndex: 20` — always visible above the map regardless of bottom sheet state
- Uses the same `triggerEmergency` store action, ensuring one backend call regardless of which button the rider taps

**Already implemented (Sprint 4, on main)**
- `shared/components/SOSButton.tsx` — long-press (2s hold) UX, pulse animation, vibration, GPS capture, backend POST, 911 prompt
- `rider-app/app/driver-arriving.tsx` — SOS in header (top-right)
- `rider-app/app/driver-arrived.tsx` — SOS in header (top-right)  
- `rider-app/store/rideStore.ts` — `triggerEmergency(rideId, lat?, lng?)`
- `backend/routes/rides.py` — `POST /{ride_id}/emergency` notifies admin + emergency contacts via WebSocket + SMS

---

## Cumulative Sprint 6 Issue Resolution

| Issue | Description | Resolved |
|-------|-------------|---------|
| SEC-014 | JWT access token lifetime 30 days | ✅ → 15 minutes |
| SEC-015 | No token refresh mechanism | ✅ → Refresh rotation with 401 intercept |
| SEC-016 | OTP stored in plain text | ✅ → SHA-256 hash only |
| SEC-017 | No ride request input validation | ✅ → Address length + coordinate bounds |
| CQ-009 | Float money arithmetic | ✅ → Decimal with ROUND_HALF_UP |
| FEAT-001 | SOS button not persistent during ride | ✅ → Floating overlay always visible |

---

## Remaining Open Issues (post Sprint 6)

From the 55-issue audit, the following remain open (lower priority):

| ID | Description | Priority |
|----|-------------|----------|
| SEC-018 | Stripe webhook signature verification | ✅ Already done (pre-Sprint 6) |
| FEAT-004 | Surge pricing admin UI | ✅ Already done (pre-Sprint 6) |
| CQ-010 | Admin dashboard E2E test coverage | P2 |
| OPS-001 | Zero-downtime deployment strategy | P2 |
| OPS-002 | Database migration strategy | P2 |
| PERF-001 | Redis caching for fare lookups | P3 |
| PERF-002 | Driver location update throttling | P3 |
| UX-001 | Rider cancellation policy UX | P3 |

The 6 remaining P0/P1 items that were blocking production are now all resolved across Sprints 1-6.
