# Admin Dashboard Audit — Phase 4: Backend Security Deep-dive

**Date:** 2026-04-26

---

## 1. Input Validation Coverage

### Pydantic model usage

Admin route handlers fall into two categories: those that use typed Pydantic request models, and those that accept raw `Dict[str, Any]`. The latter group provides no API-boundary enforcement.

**Handlers using proper Pydantic models (field-validated):**

| File | Models |
|---|---|
| `staff.py` | `StaffCreateRequest`, `StaffUpdateRequest` |
| `wallet.py` | `AdminCreditRequest` (gt=0, le=10k), `AdminDebitRequest` |
| `messaging.py` | `CloudMessageRequest` (min/max length, Literal audience) |
| `auth.py` | `LoginRequest`, `RefreshRequest`, `ChangePasswordRequest` |
| `monitoring.py` | `FlushPrefixRequest` |
| `support.py` | `FlagRequest`, `ComplaintRequest`, `ComplaintResolveRequest` |
| `settings.py` | `SettingsUpdateRequest` (extra="forbid") |
| `drivers.py` | `DriverVerifyRequest`, `DriverActionRequest`, `DriverStatusOverride`, `DriverNoteCreate` |

**Handlers accepting raw `Dict[str, Any]` (28 handlers):**

| File | Handlers |
|---|---|
| `promotions.py` | create, update |
| `service_areas.py` | create area, update area, update surge, create fee, update fee, update tax |
| `documents.py` | create requirement, update requirement, review document |
| `vehicle_fleet.py` | create/update vehicle type, create/update fare config |
| `faqs.py` | create FAQ, update FAQ, send notification |
| `legal_documents.py` | upsert legal document |
| `support.py` | create/update dispute, resolve dispute, create ticket, reply to ticket, update ticket |
| `drivers.py` | update driver (raw field dict) |
| `settings.py` | update heatmap settings |
| `users.py` | update user status |

**Security impact:** Raw dict handlers have no required-field enforcement, no type coercion, and no min/max constraints at the API layer. FastAPI will still reject requests with wrong Content-Type, but a caller can omit required fields (getting `None` or a default), pass strings where numbers are expected, or pass negative values for financial fields. The most dangerous cases are financial fields in `promotions.py` (discount_value, total_budget) and `service_areas.py` (fee amount, surge multiplier).

---

## 2. Rate Limiting

### Auth endpoints — protected

| Endpoint | Limit | Correct? |
|---|---|---|
| `POST /auth/login` | 5/min per IP | ✅ |
| `POST /auth/refresh` | 20/min per IP | ✅ |
| `POST /auth/logout` | 10/min per IP | ✅ |
| `POST /auth/logout-all` | 5/min per IP | ✅ |
| `PUT /auth/change-password` | 3/min per IP | ✅ |

### All other admin endpoints — unprotected beyond global

All non-auth admin endpoints rely on the global `default_limits=["100/minute", "1000/hour"]` from `default_limiter` in `rate_limiter.py:64`. The `admin_rate_limit` decorator is defined (`rate_limiter.py:191: "100/minute"`) but **not applied to any admin route handler** — it exists only as an unused export.

**Impact of missing per-operation rate limits:**

| Endpoint | Risk without specific limit |
|---|---|
| `POST /wallet/credit` | An attacker with a compromised token can credit 100 wallets/minute |
| `PUT /staff/{id}` | Role escalation attempt at 100/min — no per-account lockout |
| `POST /cloud-messaging/send` | 100 mass blasts/minute — spam/abuse vector |
| `PUT /service-areas/{id}` | Surge can be flipped 100x/min — cache thrashing |
| `DELETE /staff/{id}` | 100 admin deletions/minute possible |

**Finding F-36:** No per-operation rate limits on any destructive admin mutation endpoints.

---

## 3. Idempotency

### Wallet credit/debit — no idempotency

`POST /wallet/credit` and `POST /wallet/debit` have no `Idempotency-Key` support. If a browser retries a credit request on network timeout (common with Axios interceptors), the user gets double-credited. Wallet.py correctly uses Decimal arithmetic and writes an audit log entry, but nothing prevents identical concurrent requests from both succeeding.

The idem key infrastructure exists (`idem:` Redis prefix, 24h TTL per monitoring.py comment), but is not wired into admin wallet endpoints.

**Finding F-37:** `POST /wallet/credit` and `POST /wallet/debit` have no idempotency protection — double-write on network retry.

### Confirmation controls

Only one destructive endpoint requires explicit confirmation: `POST /monitoring/redis/flush-prefix` (`confirm: "FLUSH"`). No equivalent gate exists for:
- Rotating Stripe/Twilio credentials (`PUT /settings`)
- Deleting admin staff (`DELETE /staff/{id}`)
- Bulk-deleting GPS history (`POST /maintenance/cleanup-location-history`)
- Broadcasting to all users (`POST /cloud-messaging/send`)
- Changing admin role to super_admin (`PUT /staff/{id}`)

---

## 4. SQL / NoSQL Injection Surface

**No injection surface found.**

All database access goes through `db_supabase.py` helper functions which use Supabase's `supabase-py` client (PostgREST typed interface). No string-interpolated SQL was found in any admin route. The `db_supabase.rpc()` helper (line 1216) accepts `func_name: str` but is never called from admin routes — only from `db_supabase.py` itself with hardcoded function names (`find_nearby_drivers`, `corporate_wallet_apply_delta`).

The `$regex` filter in `users.py`, `maintenance.py`, and `promotions.py` applies `re.escape()` to the search term before building the filter object — regex injection is correctly mitigated.

---

## 5. Supabase Client and RLS

The backend always uses `SUPABASE_SERVICE_ROLE_KEY` (`supabase_client.py:11`). Service role bypasses RLS on all tables. This is correct for an admin backend — admin users are authenticated separately via JWT, and service role is needed to read/write across all tenant rows.

**Risk:** The sole gate between an HTTP request and unrestricted DB access is the `get_admin_user` FastAPI dependency. If this dependency is bypassed (e.g. by mounting a router without it), the service role key grants full DB write access with no further checks. The current `admin_router` pattern with `dependencies=[Depends(get_admin_user)]` in `__init__.py` is the correct mitigation — but it requires every future sub-router to be added through `__init__.py`, not directly to the app.

No admin route was found to use the Supabase anon key.

---

## 6. Sensitive-Settings Write Path

`PUT /settings` in `settings.py:52` accepts the full `SettingsUpdateRequest` Pydantic model (extra="forbid") and writes to the `settings` table. 

**Confirmed gaps:**
1. **No audit log** — who changed the Stripe key and when is unrecorded (F-27).
2. **No confirmation** — a single API call rotates the live Stripe secret key.
3. **Plaintext response** — `GET /settings` returns the full secret (F-24).
4. **No two-person rule** — no approval workflow for credential rotation.

The CLAUDE.md question _"encrypted-at-rest? Who can rotate? Is there a two-person rule?"_ — credentials are stored as plaintext strings in the Supabase `settings` table. At-rest encryption depends entirely on Supabase's volume-level encryption (ca-central-1 region). There is no application-level encryption of the credential values.

---

## 7. Phase 4 New Findings

| ID | Finding | Severity |
|---|---|---|
| F-36 | No per-operation rate limits on destructive admin mutations (wallet credit, staff delete, mass notify, surge override) | MEDIUM |
| F-37 | `POST /wallet/credit` + `POST /wallet/debit` have no idempotency key — double-write on retry | MEDIUM |
| F-38 | 28 admin endpoints accept raw `Dict[str, Any]` — no field-level validation at API boundary | MEDIUM |
| F-39 | No confirmation gate for Stripe key rotation, admin deletion, GPS bulk-delete, mass notification | LOW |
| F-40 | Settings credentials stored as plaintext in DB — no application-level encryption | LOW (env-dependent) |
