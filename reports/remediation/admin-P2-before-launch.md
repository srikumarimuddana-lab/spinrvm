# P2 — Admin Before-Launch: Fix Before Public Launch

These 12 items are MED severity gaps that must be closed before opening the platform to the general public. They cover MFA, idle session timeout, schema validation on the messaging + service-area endpoints, range constraints, backend test coverage for admin business logic + RBAC, Next.js security headers, and pagination caps on the remaining unbounded queries.

Source audit: `reports/audits/2026-04-25-admin-panel-audit-v1.txt`
Branch: `claude/audit-continuation-batch-2`

**Estimated total effort:** ~52 hours.

---

## A-P2-1 · No Multi-Factor Authentication on Admin Login

**What's wrong:** `backend/routes/admin/auth.py` is password-only. No TOTP, hardware key, email/SMS second factor. Admin accounts can read every rider and driver's PII and financial data. A leaked password is total compromise.

**File to fix:** `backend/routes/admin/auth.py` + new `backend/migrations/NN_admin_mfa.sql`

**How to fix:**
1. Migration: add `mfa_enabled BOOLEAN DEFAULT FALSE`, `totp_secret TEXT`, `mfa_backup_codes TEXT[]` columns to `admin_staff`.
2. Use `pyotp` for TOTP. New endpoints: `POST /admin/auth/mfa/enroll` (returns QR data), `POST /admin/auth/mfa/verify` (confirms enrollment), `POST /admin/auth/login` extended to accept `totp_code` when `mfa_enabled`.
3. Issue 10 single-use backup codes on enrollment, hashed at rest.
4. Make MFA mandatory for `super_admin` and `finance` roles immediately; opt-in for the rest at launch.

**Regression test:** `test_super_admin_login_requires_totp` — correct password without code → 401; correct password + valid TOTP → 200; valid backup code consumes the code (second use → 401).

**Why it matters:** The single-strongest control against credential theft, and the one regulators specifically check for at SOC2 Type II audit.

**Effort:** 12–16 h · **Severity:** MEDIUM · **Risk score:** 16 · **Regulations:** SOC2 CC6.1, PIPEDA · **Audit ref:** 02-5

---

## A-P2-2 · No Idle Session Timeout

**What's wrong:** `admin-dashboard/src/store/authStore.ts:10` uses an 8-hour cookie max-age with no idle tracking. Backend stores no `last_activity` for staff. An admin who steps away from an unlocked machine has a valid session for the full TTL.

**File to fix:** `admin-dashboard/src/store/authStore.ts` + `backend/dependencies/__init__.py` get_admin_user

**How to fix:**
1. Client: hook on user activity (click, keystroke). 30 minutes of inactivity → call `/admin/auth/logout` then redirect to `/login`. Warn at 25 minutes.
2. Server: each authenticated request sets `admin_staff.last_activity_at = NOW()`. The dependency rejects with 401 if `NOW() - last_activity_at > 30 min`.
3. Refresh-token issuance also tracks `last_activity_at`.

**Regression test:** `test_admin_session_idle_timeout` — login, advance clock 31 min with no activity, next request returns 401 with `idle_timeout`.

**Why it matters:** Standard SOC2 control; required by most enterprise auditors.

**Effort:** 6–8 h · **Severity:** MEDIUM · **Risk score:** 12 · **Regulations:** SOC2 · **Audit ref:** 02-4

---

## A-P2-3 · Cloud Messaging Endpoint Accepts Raw `Dict[str, Any]` — No Schema Validation

**What's wrong:** `backend/routes/admin/messaging.py:22` declares `payload: Dict[str, Any]`. Only validation is `if not title or not description`. Title/description have no length cap (a 1 MB string is accepted), audience values aren't enum-checked (a typo silently sends to nobody), `scheduled_at` isn't datetime-validated.

**File to fix:** `backend/routes/admin/messaging.py:22`

**How to fix:**
```python
class CloudMessageRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    audience: Literal["customers","drivers","particular_customer","particular_driver","all"]
    channels: List[Literal["push","sms","email"]]
    scheduled_at: Optional[datetime] = None
    target_user_ids: Optional[List[str]] = None  # for "particular_*" cases
    model_config = ConfigDict(extra="forbid")
```

**Regression test:** Send `audience="custmers"` (typo) → expect 422, not silent zero-recipient send.

**Why it matters:** Defense in depth; also fixes the silent-zero-send footgun that has bitten ops once already.

**Effort:** 2 h · **Severity:** MEDIUM · **Risk score:** 12 · **Audit ref:** 04-1

---

## A-P2-4 · Service Areas Endpoint Accepts Raw Dict on All Operations

**What's wrong:** `backend/routes/admin/service_areas.py:42,80,139,192,212,257` — every POST/PUT body is `Dict[str, Any]`. No coordinate range checks, no name length, no surge multiplier bounds (`999×` would be accepted), no fee bounds. Even existing `float()` casts are unguarded.

**File to fix:** `backend/routes/admin/service_areas.py`

**How to fix:**
```python
class Coord(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)

class ServiceAreaRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    coordinates: List[Coord] = Field(min_length=3)

class SurgePricingRequest(BaseModel):
    multiplier: Decimal = Field(ge=Decimal("1.0"), le=Decimal("2.5"))  # SURGE_CAP

class FeeRequest(BaseModel):
    amount: Decimal = Field(gt=Decimal("0"), le=Decimal("100"))
```
Decimal-only here per the money-arithmetic rule.

**Regression test:** POST `multiplier=999` → 422; POST `lat=200` → 422; POST valid → 201.

**Why it matters:** Surge cap is a regulatory commitment (CLAUDE.md: 2.5× hard ceiling). A 999× input that bypasses validation is a compliance event.

**Effort:** 4 h · **Severity:** MEDIUM · **Risk score:** 16 · **Regulations:** SK-TNC · **Audit ref:** 04-2

---

## A-P2-5 · Driver Action / Status Override Use Plain `str`

**What's wrong:** `backend/routes/admin/drivers.py:98–107` — `DriverActionRequest.action` and `DriverStatusOverride.status` are plain `str`. Garbage strings pass validation; an invalid action raises a `KeyError` deep in the handler instead of being rejected at the boundary.

**File to fix:** `backend/routes/admin/drivers.py:98–107`

**How to fix:**
```python
DriverAction = Literal["approve","reject","suspend","ban","unban","reactivate"]
DriverStatus = Literal["pending","active","rejected","suspended","banned"]

class DriverActionRequest(BaseModel):
    action: DriverAction
    reason: Optional[str] = Field(default=None, max_length=500)
```

**Regression test:** POST `action="explode"` → 422.

**Why it matters:** Tiny fix, large clarity win — boundary validation is always cheaper than handler validation.

**Effort:** 0.5 h · **Severity:** MEDIUM · **Risk score:** 8 · **Audit ref:** 04-3

---

## A-P2-6 · Settings Float Fields Have No Range Constraints

**What's wrong:** `backend/routes/admin/settings.py:36–40` — `platform_fee_percent`, `cancellation_fee_admin/driver`, `min_driver_rating`, `search_radius_km` are unbounded `Optional[float]`. A typo of `500` for `platform_fee_percent` would set a 500% fee.

**File to fix:** `backend/routes/admin/settings.py:36–40`

**How to fix:**
```python
class SettingsUpdate(BaseModel):
    platform_fee_percent: Optional[float] = Field(default=None, ge=0, le=1.0)
    cancellation_fee_admin: Optional[Decimal] = Field(default=None, ge=0, le=50)
    cancellation_fee_driver: Optional[Decimal] = Field(default=None, ge=0, le=50)
    min_driver_rating: Optional[float] = Field(default=None, ge=1.0, le=5.0)
    search_radius_km: Optional[int] = Field(default=None, ge=1, le=100)
```

**Regression test:** PATCH `platform_fee_percent=5.0` → 422 (must be ≤ 1.0).

**Why it matters:** Spinr's "0% commission" identity is one bad PATCH away from a 500% fee disaster. Range constraints make that physically impossible.

**Effort:** 1 h · **Severity:** MEDIUM · **Risk score:** 12 · **Audit ref:** 04-4

---

## A-P2-7 · No Backend Tests for Admin Business Logic

**What's wrong:** `backend/tests/test_admin_routes_auth.py` (75 lines) is the entire admin test suite — and it only checks unauthenticated → 401. There are no tests for driver approve/reject/suspend/ban, wallet credit/debit, user suspend/ban, force-cancel ride, staff creation, service-area CRUD, promotion CRUD, cloud message dispatch.

**File to fix:** new `backend/tests/test_admin_drivers.py`, `test_admin_wallet.py`, `test_admin_users.py`, `test_admin_rides.py`

**How to fix:** One file per admin domain. For each endpoint, cover (a) happy path, (b) wrong role, (c) invalid input. Use the `mock_supabase_client` fixture from `conftest.py`. Aim for 70% coverage on `backend/routes/admin/*` (per the per-route minimums in CLAUDE.md).

**Regression test:** Itself the regression test layer. CI gates 70% coverage on `routes/admin/*`.

**Why it matters:** No test layer means every refactor is a roll of the dice. Pairs with B-P1-3 (`--cov-fail-under`).

**Effort:** 16–24 h · **Severity:** MEDIUM · **Risk score:** 16 · **Audit ref:** 06-1

---

## A-P2-8 · No RBAC / Module Enforcement Tests

**What's wrong:** No tests verify a "support" role cannot reach wallet or analytics endpoints. Once A-P1-1 lands, every regression that loosens module enforcement will be silent.

**File to fix:** new `backend/tests/test_admin_rbac.py`

**How to fix:** Parametrized matrix — for each role × each privileged endpoint, assert 403 if the role's preset doesn't include the module. Include the inverse (allowed combinations → 200). Run on every PR that touches `routes/admin/*` or `dependencies/__init__.py`.

```python
@pytest.mark.parametrize("role,endpoint,expect", [
    ("support", "/admin/wallet/credit", 403),
    ("support", "/admin/users/", 200),
    ("finance", "/admin/wallet/credit", 200),
    ...
])
def test_rbac_matrix(role, endpoint, expect): ...
```

**Regression test:** Itself.

**Why it matters:** A-P1-1 closes the bypass; this test prevents it from re-opening silently.

**Effort:** 4 h · **Severity:** MEDIUM · **Risk score:** 12 · **Audit ref:** 06-2

---

## A-P2-9 · Admin Dashboard Has No Security Headers

**What's wrong:** `admin-dashboard/next.config.ts` has no `headers()` export — no `X-Frame-Options`, no CSP, no `X-Content-Type-Options`, no HSTS, no Referrer-Policy. Backend sets these on API responses, but the Next.js HTML is served without them. Clickjacking and MIME-sniffing on the admin UI itself is undefended.

**File to fix:** `admin-dashboard/next.config.ts`

**How to fix:**
```ts
async headers() {
  return [{
    source: "/(.*)",
    headers: [
      { key: "X-Frame-Options", value: "DENY" },
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
      { key: "Content-Security-Policy",
        value: "default-src 'self'; script-src 'self'; img-src 'self' data: https:; connect-src 'self' https://api.spinr.app" },
    ],
  }];
}
```

**Regression test:** `curl -I https://admin.spinr.app/` shows all headers present.

**Why it matters:** UI-layer defense in depth. With A-P0-1's HttpOnly cookie, clickjacking would let an attacker frame the admin into performing actions — `X-Frame-Options: DENY` is the hard counter.

**Effort:** 2 h · **Severity:** MEDIUM · **Risk score:** 12 · **Regulations:** OWASP A05 · **Audit ref:** 07-2

---

## A-P2-10 · Force-Cancel Ride Not Audit-Logged

**What's wrong:** Admin force-cancel calls the cancel logic without writing an `audit_logs` row. Drivers who dispute a forced cancellation can't see who triggered it.

**File to fix:** `backend/routes/admin/rides.py` (cancel endpoint)

**How to fix:**
```python
await db_supabase.insert_row("audit_logs", {
    "action": "ride_cancelled",
    "entity_type": "ride",
    "entity_id": ride_id,
    "actor_id": admin["id"],
    "reason": reason,
    "created_at": datetime.utcnow(),
})
```

**Regression test:** Force-cancel a ride; assert `audit_logs` has the row with admin actor.

**Why it matters:** Driver-trust feature. Pairs with the audit-log family added in A-P1-4/5/6.

**Effort:** 1 h · **Severity:** MEDIUM · **Risk score:** 12 · **Audit ref:** 05-4

---

## A-P2-11 · Promotion Stats Loads All Promo Usage Into Memory

**What's wrong:** `backend/routes/admin/promotions.py:196–198` — `get_promotion_stats` loads `all_promos` (limit=10000) and `all_usage` (limit=10000) into memory, then aggregates in Python. As promo usage grows, slow first, silently capped at 10k second.

**File to fix:** `backend/routes/admin/promotions.py:196–198`

**How to fix:** Compute aggregations in SQL:
```sql
CREATE OR REPLACE FUNCTION get_promotion_stats()
RETURNS TABLE (promo_id uuid, total_uses bigint, total_savings numeric)
LANGUAGE sql STABLE AS $$
  SELECT promo_id, COUNT(*), COALESCE(SUM(savings),0)
  FROM promo_usage GROUP BY promo_id;
$$;
```
Backend calls the RPC directly.

**Regression test:** Seed 50k promo_usage rows; call stats endpoint; assert under 200 ms; counts match.

**Why it matters:** Same anti-pattern family as A-P1-8. SQL aggregations belong in SQL.

**Effort:** 2 h · **Severity:** MEDIUM · **Risk score:** 12 · **Audit ref:** 08-4

---

## A-P2-12 · Driver Ride History Loads Up to 50,000 Rides per Driver

**What's wrong:** `backend/routes/admin/drivers.py:322` — `admin_get_driver_rides` fetches up to `limit=50000` rides per driver. A long-tenured driver with high ride volume will load 50k rows into memory for a single admin page view.

**File to fix:** `backend/routes/admin/drivers.py:322`

**How to fix:** Cap at `limit=500` with offset/cursor pagination. The admin UI shows a recent-rides table, not a full history dump.

```python
@router.get("/drivers/{driver_id}/rides")
async def admin_get_driver_rides(
    driver_id: str,
    limit: int = Query(50, ge=1, le=500),
    cursor: Optional[str] = None,
    ...
):
    ...
```

**Regression test:** Seed driver with 5,000 rides; call endpoint with default limit; assert response has 50 rides + a `next_cursor`; latency under 100 ms.

**Why it matters:** Closes the last unbounded admin query. Without it, the same SLA-breach class as A-P0-3 will reappear once any driver crosses 10k rides.

**Effort:** 0.5 h · **Severity:** MEDIUM · **Risk score:** 12 · **Audit ref:** 08-6

---

## Checklist

- [ ] A-P2-1 TOTP MFA mandatory for super_admin and finance roles (02-5)
- [ ] A-P2-2 30-minute idle session timeout client + server (02-4)
- [ ] A-P2-3 `CloudMessageRequest` Pydantic model (04-1)
- [ ] A-P2-4 Pydantic models for every service-area endpoint (04-2)
- [ ] A-P2-5 `Literal` types on driver action / status override (04-3)
- [ ] A-P2-6 Range-bounded settings fields (04-4)
- [ ] A-P2-7 Backend tests for admin business logic; ≥70% coverage on `routes/admin/*` (06-1)
- [ ] A-P2-8 RBAC matrix tests on every role × endpoint (06-2)
- [ ] A-P2-9 Next.js `headers()` with X-Frame-Options, CSP, HSTS, etc. (07-2)
- [ ] A-P2-10 Audit-log force-cancel ride (05-4)
- [ ] A-P2-11 Promotion stats aggregation in SQL (08-4)
- [ ] A-P2-12 Cap driver ride history at limit=500 with pagination (08-6)

## After this file

- Move on to `admin-P3-hardening.md` (12 items): bcrypt the super-admin password, tighter login rate limits, IP whitelist, forgot-password flow, `super_admin` checks on staff update/delete, re-auth on promotion-to-super_admin, Vitest unit tests, security tests on auth, error-handling cleanups, audit_logs schema standardisation, subscriptions UI completion.
