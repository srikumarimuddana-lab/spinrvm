# Auth & Users Domain

Identity, sessions, and admin access control for the Spinr backend.

**Files covered:**
`routes/auth.py`, `routes/admin/auth.py`, `routes/users.py`, `routes/admin/staff.py`, `routes/admin/users.py`, `dependencies.py`, `utils/password.py`, `utils/refresh_tokens.py`, `utils/crypto.py`, `utils/rate_limiter.py`, `sms_service.py`.

---

## 1. Token model

| Token | Who | Where minted | Signing | TTL | Revocable? |
|-------|-----|--------------|---------|-----|------------|
| **Firebase ID token** | Rider/driver (preferred) | Firebase client SDK | Firebase keys | Short | Yes (Firebase revoke) |
| **Access JWT (rider/driver)** | Rider/driver (legacy fallback) | `dependencies.create_jwt_token` | HS256 `JWT_SECRET` | 15 min (`ACCESS_TOKEN_EXPIRE_MINUTES`) | Yes — `token_version` bump |
| **Access JWT (admin)** | Admin console | `routes/admin/auth.py` | HS256 `JWT_SECRET` (same secret — unified 2025-Q4) | 8–12 h (`ADMIN_ACCESS_TOKEN_TTL_HOURS`) | Yes — `token_version` bump on `admin_staff` |
| **Refresh token** | Rider/driver/admin | `dependencies.create_refresh_token` (32-byte `secrets.token_urlsafe`) | Opaque | 30 d (`REFRESH_TOKEN_EXPIRE_DAYS`) | Yes — delete row or `token_version` bump |

**Key invariants:**

- **One JWT secret.** `settings.JWT_SECRET` signs every JWT the backend mints. Previously `dependencies.py` and `routes/admin/auth.py` each read their own env var with separate fallbacks — silently divergent secrets. Unified in the module-level comment at `dependencies.py:29-35`.
- **Role is never trusted from a rider/driver JWT.** `get_current_user` re-reads role from the `users` table. A forged token with `role=super_admin` gets auto-downgraded to whatever the DB says.
- **Role IS trusted from an admin JWT** (`dependencies.py:190-201`). Admin tokens carry `role+email+modules` claims. Because admin-001 has no `users` row, it would otherwise be auto-created as a rider and fail the admin role check. Signed with our own secret → claims trustworthy.
- **Session revocation.** Every user + admin_staff row has a `token_version` integer. Access tokens carry the version as a claim. `_token_version_mismatch(payload, user_row)` in `dependencies.py:114` treats missing claim as 0 (backwards-compatible with tokens minted before migration 25).
- **Single-device enforcement.** `users.current_session_id` holds the active session. Access token carries `session_id`. Mismatch → 401 `Session expired. Logged in from another device.`
- **Refresh tokens stored hashed.** `hash_token(raw) = sha256(raw)` (`dependencies.py:64`). We never store plaintext; rotation issues a fresh opaque token and writes only the hash.

---

## 2. OTP flow (rider / driver)

```
POST /auth/send-otp            { phone }
   │
   ├─ validate_phone → E.164 normalized
   ├─ rate limit 3/min by phone hash
   ├─ OTP lockout check: if otp:failures:{phone} ≥ 5 in last 1 h → 429 (24 h penalty)
   ├─ generate_otp()  (4 digits, secrets.choice)
   ├─ insert_otp_record(phone, code, expires_at=now+5m, verified=false)
   └─ send_otp_sms(phone, code) via Twilio (console fallback in dev)
       → {ok: true}

POST /auth/verify-otp          { phone, code }
   │
   ├─ rate limit 3/min by phone hash
   ├─ get_otp_record(phone)
   │   ├─ missing / expired / verified → 400 invalid
   │   └─ code mismatch → increment otp:failures:{phone}; 400 invalid
   ├─ verify_otp_record(id) → mark verified
   ├─ get_user_by_phone(phone) or create (role=rider, profile_complete=false)
   ├─ rotate session_id, bump into users.current_session_id
   ├─ create_jwt_token(user_id, phone, session_id, token_version=user.token_version)
   ├─ create_refresh_token(); store sha256 in refresh_tokens
   └─ return {token, refresh_token, user, is_new_user, expires_in, access_expires_at, refresh_expires_at}
```

**Why 4 digits:** product decision. 1/10,000 guess odds per attempt, mitigated by 5/min rate limit + 5-minute expiry + 24-hour lockout after 5 failures. See `dependencies.py:37-43`.

---

## 3. Refresh flow

```
POST /auth/refresh             { refresh_token }
   │
   ├─ sha256(refresh_token) = hash
   ├─ look up refresh_tokens row by token_hash
   │   ├─ missing / expired / revoked → 401
   ├─ look up user
   ├─ mint new access JWT
   ├─ rotate refresh token:
   │     • delete old row
   │     • create_refresh_token(); insert sha256
   └─ return {token, refresh_token, access_expires_at, refresh_expires_at}
```

**Rotation-on-use** closes the window where a stolen refresh token would keep working. If the legitimate client and attacker both present the same token, the second presenter gets a revoked-token 401 → server-side breach detection.

---

## 4. Admin auth

Admin console uses email + password, not OTP.

```
POST /api/admin/auth/login     { email, password }
   │
   ├─ get admin_staff row by email (case-insensitive)
   ├─ verify password:
   │     ├─ bcrypt.checkpw (cost 12) against admin.password_hash  →  if OK, done
   │     └─ fallback: sha256 compare (legacy). On match:
   │           • re-hash with bcrypt, update admin.password_hash
   │           • (transparent upgrade; admin doesn't know)
   ├─ rotate session; bump admin_staff.current_session_id
   ├─ mint 8-12h JWT with {user_id, email, role, modules, token_version, session_id}
   ├─ create refresh token (30 d)
   └─ return {token, refresh_token, admin: {id, email, role, modules}}

POST /api/admin/auth/logout
   └─ bump admin_staff.token_version → every issued access token is now stale

POST /api/admin/auth/logout-all
   └─ same (semantically identical for a single admin user)
```

**Legacy sha256 upgrade** exists because early admins were seeded with sha256-hashed passwords before bcrypt landed. On first successful login after the migration, their row is rewritten with bcrypt. Removing the sha256 branch too early would lock those admins out.

**Module-based permissions.** `admin_staff.modules` is a JSON array of module keys (e.g. `["rides","drivers","finance","support",...]`). 18 modules total. Super-admin has implicit access to all; other roles carry an explicit list. The admin dashboard checks modules before rendering each section.

Roles: `admin, super_admin, operations, support, finance, custom`. Routes requiring admin use `get_admin_user` (`dependencies.py:257`).

---

## 5. User endpoints

### Rider/driver (`routes/users.py`)

| Method | Path | Notes |
|---|---|---|
| GET | `/users/me` | Current profile (role, profile_complete, is_driver flag). |
| PUT | `/users/me` | Update first_name, last_name, email, gender, profile_image. |
| POST | `/users/create-profile` | Completes `profile_complete=true` on a user that OTP'd in but never filled details. |
| DELETE | `/users/me` | Soft-delete / anonymize (per jurisdiction). Redacts PII; preserves ride history for driver payouts. |
| POST | `/users/push-token` | Register FCM token for push notifications. |

### Admin — users (`routes/admin/users.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/users` | Paginated list with filters (role, status, search). |
| GET | `/admin/users/{id}` | Full profile + ride/wallet summary. |
| PATCH | `/admin/users/{id}/status` | active / suspended / banned. |
| POST | `/admin/users/{id}/force-logout` | `users.token_version += 1`; all existing access tokens rejected next request. |
| GET | `/admin/users/{id}/rides` | Rider history from this user's perspective. |
| GET | `/admin/users/{id}/wallet-transactions` | Rider wallet ledger. |

### Admin — staff (`routes/admin/staff.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/staff` | List admins with role + modules. |
| POST | `/admin/staff` | Create admin (super-admin only). Generates bcrypt hash. |
| PATCH | `/admin/staff/{id}` | Update role / modules / status. |
| DELETE | `/admin/staff/{id}` | Soft-delete; bumps token_version to revoke. |
| POST | `/admin/staff/{id}/reset-password` | Generate new password; optionally email. |

---

## 6. Security surface

| Risk | Mitigation | Location |
|------|------------|----------|
| OTP brute force | Redis sliding-window lockout: 5 failures in 1 h → 24 h block. | `utils/rate_limiter.py` SEC-008 |
| Credential stuffing | bcrypt cost 12 on admin passwords. | `utils/password.py` |
| Token theft | 15-min access TTL; refresh rotation on use; `token_version` kill-switch. | `dependencies.py`, `utils/refresh_tokens.py` |
| Session replay across devices | `current_session_id` enforcement. | `dependencies.py:212-214` |
| Forged admin role in rider JWT | Role re-read from DB for non-admin tokens. | `dependencies.py:209-228` |
| JWT secret divergence | Unified `settings.JWT_SECRET`. | `dependencies.py:29-35` |
| Phone enumeration via OTP | Same response for registered/unregistered phones. | `routes/auth.py` |
| Admin bootstrap with weak secret | Fail-fast in `ENV=production` on default `JWT_SECRET` / `ADMIN_PASSWORD`. | `core/config.py` |

---

## 7. Function / class quick reference

### `dependencies.py`

| Name | Purpose |
|---|---|
| `generate_otp` / `generate_pickup_otp` | 4-digit numeric OTP via `secrets.choice`. |
| `hash_token(raw)` | sha256 for refresh token storage. |
| `create_refresh_token()` | `secrets.token_urlsafe(32)`. |
| `create_jwt_token(user_id, phone, session_id, *, token_version)` | 15-min HS256 access JWT. |
| `verify_jwt_token(token)` | Decode + raise 401 on expiry/invalid. |
| `_token_version_mismatch(payload, user_row)` | True if claim < stored. |
| `get_current_user(credentials)` | Firebase-first, JWT fallback, admin claim path, role re-read. |
| `get_admin_user(current_user)` | Require role ∈ admin roles. |

### `utils/refresh_tokens.py`

| Name | Purpose |
|---|---|
| `insert_refresh_token(user_id, token, expires_at)` | Store sha256 hash. |
| `lookup_refresh_token(raw)` | Hash, load row, return None if missing/expired. |
| `revoke_refresh_token(raw)` | Delete row. |
| `rotate_refresh_token(raw, user_id)` | Revoke + issue in one call. |

### `utils/password.py`

| Name | Purpose |
|---|---|
| `hash_password(plain)` | bcrypt cost 12. |
| `verify_password(plain, stored_hash)` | Tries bcrypt, then legacy sha256; returns `(ok, needs_upgrade)`. |

### `utils/crypto.py`

| Name | Purpose |
|---|---|
| `random_token(nbytes=32)` | Wrapper over `secrets.token_urlsafe`. |
| `constant_time_compare(a, b)` | `hmac.compare_digest` wrapper. |

### `utils/rate_limiter.py`

Pre-built SlowAPI limiters:

| Handle | Limit | Key |
|---|---|---|
| `otp_limit` | 3/min | phone hash |
| `login_limit` | 5/min | user id or IP |
| `ride_limit` | 10/min | IP |
| `driver_location_limit` | 60/min | IP |
| `document_upload_limit` | 5/min | IP |
| `admin_limit` | 100/min | IP |
| `general_limit` | 30/min | IP |

SEC-008 OTP brute-force: `check_otp_lockout(phone)` and `record_otp_failure(phone)` backed by Redis `INCR` on `otp:failures:{phone}`.

### `sms_service.py`

| Name | Purpose |
|---|---|
| `send_sms(to, body, sid, token, from_)` | Twilio REST API; dev fallback logs to console. |
| `send_otp_sms(phone, code, …)` | Composes the OTP template and delegates to `send_sms`. |

---

## 8. Common tasks — where to start

| Task | File to open |
|---|---|
| Change OTP length / window / lockout | `dependencies.py:40`, `core/config.py` (`OTP_MAX_FAILURES`, `OTP_FAILURE_WINDOW_SECONDS`, `OTP_LOCKOUT_DURATION_SECONDS`). |
| Add an admin module | `routes/admin/staff.py` (module catalog), front-end module registry, `get_admin_user`-gated routes. |
| Force-logout a specific user | `PATCH /admin/users/{id}/force-logout` (bumps token_version). |
| Revoke a single refresh token | Delete from `refresh_tokens` where `token_hash = sha256(raw)`. |
| Add SSO | New middleware/route that verifies SSO token, issues our access+refresh JWT — keep `token_version` semantics. |
| Extend rider JWT payload | `create_jwt_token` (`dependencies.py:73`). Keep claims minimal; never trust them for role. |
