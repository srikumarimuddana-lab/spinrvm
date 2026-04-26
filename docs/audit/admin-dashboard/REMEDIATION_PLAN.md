# Admin Dashboard Audit — Remediation Plan

**Date:** 2026-04-26  
**Priority system:** P0 = fix before next production release; P1 = fix within 2 weeks; P2 = fix within 4 weeks; P3 = schedule for next sprint

---

## P0 — Fix Before Next Production Release

### P0-1: Mask credentials in `GET /settings` response (F-24)

**File:** `backend/routes/admin/settings.py:47`, `backend/settings_loader.py`

```python
# In settings.py GET handler, after fetching settings:
CREDENTIAL_FIELDS = {"stripe_secret_key", "stripe_webhook_secret", "twilio_auth_token", "google_maps_api_key"}

def _mask_credentials(settings: dict) -> dict:
    return {
        k: (v[:8] + "*****" if k in CREDENTIAL_FIELDS and v else v)
        for k, v in settings.items()
    }

@router.get("/settings")
async def get_settings(admin: dict = Depends(get_admin_user)):
    settings = await get_app_settings()
    return _mask_credentials(settings.__dict__ if hasattr(settings, '__dict__') else dict(settings))
```

Add a separate `GET /settings/reveal/{field}` endpoint requiring `super_admin` role, recording an `audit_logs` entry on every call.

---

### P0-2: Fix privilege escalation in `PUT/DELETE /staff/{id}` (F-25)

**File:** `backend/routes/admin/staff.py:181, 227`

```python
@router.put("/staff/{staff_id}")
async def update_staff(staff_id: str, req: StaffUpdateRequest, admin: dict = Depends(get_admin_user)):
    # ADD THIS BLOCK:
    if req.role is not None or req.modules is not None:
        if admin.get("role") != "super_admin":
            raise HTTPException(status_code=403, detail="Only super admins can modify role or modules")
    # Prevent last super_admin demotion:
    if req.role and req.role != "super_admin":
        current = await db_supabase.find_one("admin_staff", {"id": staff_id})
        if current and current.get("role") == "super_admin":
            count = await db_supabase.count("admin_staff", {"role": "super_admin", "is_active": True})
            if count <= 1:
                raise HTTPException(status_code=400, detail="Cannot demote the last active super admin")

@router.delete("/staff/{staff_id}")
async def delete_staff(staff_id: str, admin: dict = Depends(get_admin_user)):
    # ADD THIS BLOCK:
    if admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Only super admins can delete staff")
    # Prevent self-deletion:
    if staff_id == admin.get("id"):
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
```

---

### P0-3: Enforce surge cap in `fare_service.py` (F-26)

**File:** `backend/services/fare_service.py:148`

```python
# Add to top of file:
from backend.utils.surge_engine import SURGE_CAP  # or: from utils.surge_engine import SURGE_CAP

# Change line 148:
# Before:
surge = float(matching_area.get("surge_multiplier", 1.0))
# After:
surge = min(float(matching_area.get("surge_multiplier", 1.0)), SURGE_CAP)
```

Additionally, in `service_areas.py` update handlers, validate at API boundary:
```python
if "surge_multiplier" in allowed_fields and body.get("surge_multiplier") is not None:
    val = float(body["surge_multiplier"])
    if val > SURGE_CAP and admin.get("role") != "super_admin":
        raise HTTPException(status_code=400, detail=f"surge_multiplier cannot exceed {SURGE_CAP} without super_admin role")
```

---

## P1 — Fix Within 2 Weeks

### P1-1: Add `HttpOnly` to admin access token cookie (F-02)

**File:** `admin-dashboard/src/store/authStore.ts`

```typescript
// Change setAuthCookie():
`admin_token=${encodeURIComponent(token)}; path=/; max-age=3600; SameSite=Strict; HttpOnly${isHttps ? '; Secure' : ''}`
// max-age set to 3600 (1h) to match access token TTL (F-18 fix)
```

Create `admin-dashboard/src/middleware.ts` to enforce server-side auth gate on all `/dashboard/*` routes (F-17):
```typescript
import { NextRequest, NextResponse } from 'next/server';

export function middleware(request: NextRequest) {
  const token = request.cookies.get('admin_token');
  if (!token && request.nextUrl.pathname.startsWith('/dashboard')) {
    return NextResponse.redirect(new URL('/login', request.url));
  }
  return NextResponse.next();
}

export const config = { matcher: ['/dashboard/:path*'] };
```

---

### P1-2: Add audit log on settings write + key reveal (F-27)

**File:** `backend/routes/admin/settings.py:52`

```python
@router.put("/settings")
async def update_settings(body: SettingsUpdateRequest, admin: dict = Depends(get_admin_user)):
    changed_keys = [k for k, v in body.model_dump(exclude_unset=True).items() if v is not None]
    await db_supabase.update_one("settings", {}, body.model_dump(exclude_unset=True))
    await db_supabase.insert_one("audit_logs", {
        "id": str(uuid.uuid4()),
        "actor_id": admin["id"],
        "actor_role": admin.get("role"),
        "action": "settings_updated",
        "resource": "settings",
        "resource_id": "app_settings",
        "details": {"changed_keys": changed_keys},  # Never log values
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
```

---

### P1-3: Add idempotency to wallet credit/debit (F-37)

**File:** `backend/routes/admin/wallet.py`

```python
# In AdminCreditRequest / AdminDebitRequest Pydantic models, add:
idempotency_key: Optional[str] = None

# At the top of credit/debit handlers:
if req.idempotency_key:
    redis_key = f"admin:wallet:idem:{req.idempotency_key}"
    existing = await redis.get(redis_key)
    if existing:
        return json.loads(existing)  # Return cached response

# After successful write:
if req.idempotency_key:
    await redis.setex(redis_key, 86400, json.dumps(response))
```

---

### P1-4: Fix `sender_id` hardcoding in ticket replies (F-29)

**File:** `backend/routes/admin/support.py:229`

```python
# Add admin dependency to handler:
async def admin_reply_to_ticket(ticket_id: str, reply: dict, admin: dict = Depends(get_admin_user)):
    ...
    "sender_id": admin["id"],  # was: "admin-001"
```

---

### P1-5: Fix `resolved_by` in dispute resolution (F-32)

**File:** `backend/routes/admin/support.py:141`

```python
async def admin_resolve_dispute(dispute_id: str, resolution: dict, admin: dict = Depends(get_admin_user)):
    resolved_by = admin["id"]  # was: resolution.get("resolved_by", "admin")
```

---

### P1-6: Fix GPS retention guard — validate `days` param (F-31)

**File:** `backend/routes/admin/maintenance.py:26`

```python
# Before:
async def admin_cleanup_location_history(days: int = 30):
# After:
async def admin_cleanup_location_history(days: int = Query(30, ge=7, le=1095)):
    # ge=7 preserves 7-day dispute resolution window
    # le=1095 enforces 3-year Saskatchewan Transportation Act retention
```

---

### P1-7: Add server-side audit entry for all CSV/PDF exports (F-41)

**Approach:** Create `POST /admin/exports/log` endpoint that records export events.

Alternatively, add an audit log entry at the API endpoints that supply the data:

```python
# In GET /users, GET /drivers, GET /rides/list handlers:
# When the response includes a full dataset (no pagination filter):
if not search and limit > 500:
    await db_supabase.insert_one("audit_logs", {
        "id": str(uuid.uuid4()),
        "actor_id": admin["id"],
        "actor_role": admin.get("role"),
        "action": "bulk_export",
        "resource": "users",
        "resource_id": None,
        "details": {"row_count": len(result), "filters": str(filters)},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
```

---

### P1-8: Add `beforeSend` PII scrubbing to Sentry configs (F-44)

**File:** `admin-dashboard/sentry.client.config.ts` and `sentry.server.config.ts`

```typescript
function scrubPii(event: Sentry.Event): Sentry.Event | null {
  const PII_PATTERNS = [
    /\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b/g,  // email
    /\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b/g,    // phone
    /"(stripe_secret_key|twilio_auth_token|google_maps_api_key)"\s*:\s*"[^"]+"/g,
  ];
  const eventStr = JSON.stringify(event);
  const scrubbed = PII_PATTERNS.reduce(
    (s, pattern) => s.replace(pattern, '[REDACTED]'),
    eventStr
  );
  return JSON.parse(scrubbed);
}

Sentry.init({
  // ...
  beforeSend: scrubPii,
});
```

---

### P1-9: Make `audit_logs` append-only (F-46)

**Migration:** Create `backend/migrations/38_audit_logs_append_only.sql`

```sql
-- Rollback: DROP POLICY "Admin append-only audit_logs"; CREATE POLICY "Admin full access audit_logs" ...
-- Replace FOR ALL policy with INSERT + SELECT only:
DROP POLICY IF EXISTS "Admin full access audit_logs" ON audit_logs;

CREATE POLICY "Admin read audit_logs"
ON audit_logs FOR SELECT TO authenticated
USING (EXISTS (
    SELECT 1 FROM users WHERE users.id = auth.uid()::text
    AND users.role IN ('admin', 'super_admin')
));

CREATE POLICY "Service write audit_logs"
ON audit_logs FOR INSERT TO service_role WITH CHECK (true);

CREATE POLICY "Service read audit_logs"
ON audit_logs FOR SELECT TO service_role USING (true);
-- No UPDATE or DELETE policies — append-only enforced at RLS level
```

---

## P2 — Fix Within 4 Weeks

### P2-1: Implement MFA for admin accounts (F-01)

Add TOTP-based MFA using `pyotp`. Extend `admin_staff` table with `mfa_secret`, `mfa_enabled` columns. Make MFA required for `super_admin` and optional-but-prompted for other roles.

New migration: `backend/migrations/39_admin_mfa.sql`

---

### P2-2: Add per-operation rate limits to destructive endpoints (F-36)

**File:** `backend/utils/rate_limiter.py` + apply in route handlers

```python
# In rate_limiter.py — these decorators already exist, just apply them:
wallet_credit_limit = default_limiter.limit("10/minute")
staff_mutation_limit = default_limiter.limit("20/minute")
mass_notify_limit = default_limiter.limit("5/hour")
surge_override_limit = default_limiter.limit("10/minute")
```

---

### P2-3: Add Pydantic models for the 28 raw-dict handlers (F-38, F-30)

Priority order (highest financial risk first):
1. `promotions.py` — `PromotionCreateRequest` with `discount_value: Decimal = Field(gt=0)`, `total_budget: Decimal = Field(ge=0)`
2. `service_areas.py` — `ServiceAreaUpdateRequest`, `FeeCreateRequest`
3. `vehicle_fleet.py` — `VehicleTypeRequest`, `FareConfigRequest`
4. Remaining: `documents.py`, `faqs.py`, `legal_documents.py`, `support.py`, `users.py`

---

### P2-4: Expand audit log coverage to 9+ unlogged modules (F-33)

Add audit log inserts to every state-mutating handler in:
- `promotions.py` (create/update/delete)
- `service_areas.py` (all writes)
- `documents.py` (approve/reject)
- `messaging.py` (send — record `actor_id`)
- `maintenance.py` (GPS cleanup, rollup)

Use Schema B (with `actor_id`) consistently. Deprecate Schema A's `user_email` column.

---

### P2-5: Fix N+1 in driver acceptance analytics (F-48)

**File:** `backend/routes/admin/analytics.py:128`

Replace the per-driver loop with a single Postgres GROUP BY query via `db_supabase.rpc()` or a Supabase PostgREST aggregate endpoint.

```python
# Replace the per-driver loop with:
rows = await db.rpc("get_driver_acceptance_stats", {
    "start_date": start_date.isoformat(),
    "area_id": service_area_id or None,
})
```

Create migration `40_driver_acceptance_stats_fn.sql` with the aggregating function.

---

### P2-6: Remove GPS coordinates from ride invoice PDF (F-42)

**File:** `admin-dashboard/src/app/dashboard/rides/_components/ride-invoice.tsx:138`

Remove lines 138–141 (lat/lng section). Replace with `"[Location data omitted]"` or city-level area name.

---

### P2-7: Consolidate audit_log schema and remove `user_email` column (F-45)

New migration replacing the `user_email` column with `actor_id`:
```sql
-- backend/migrations/41_audit_logs_schema_consolidation.sql
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS actor_id TEXT;
UPDATE audit_logs SET actor_id = user_email WHERE actor_id IS NULL;
ALTER TABLE audit_logs DROP COLUMN IF EXISTS user_email;
```

Update `maintenance.py:log_audit()` to use `actor_id` parameter.

---

### P2-8: Add idle session timeout (F-19)

**File:** `admin-dashboard/src/store/authStore.ts`

```typescript
// Detect idle time and proactively logout:
const IDLE_TIMEOUT_MS = 30 * 60 * 1000; // 30 minutes
let idleTimer: NodeJS.Timeout;

const resetIdleTimer = () => {
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => logout(), IDLE_TIMEOUT_MS);
};

// Attach to: 'mousemove', 'keydown', 'click', 'scroll'
```

---

### P2-9: Add per-account failed login counter (F-21)

**File:** `backend/routes/admin/auth.py`

```python
async def _check_account_lockout(email: str) -> None:
    key = f"admin:login:fail:{email.lower()}"
    count = await redis.get(key)
    if count and int(count) >= 10:
        raise HTTPException(status_code=429, detail="Account temporarily locked — too many failed attempts")

async def _record_failed_login(email: str) -> None:
    key = f"admin:login:fail:{email.lower()}"
    await redis.incr(key)
    await redis.expire(key, 3600)  # 1-hour window
```

---

## P3 — Schedule for Next Sprint

### P3-1: Implement Next.js `middleware.ts` with proper auth (F-17)

Full server-side auth gate using the HttpOnly cookie from P1-1. Redirect to login on missing/expired token.

### P3-2: Add CSP without `unsafe-inline`/`unsafe-eval` (F-05)

Migrate inline styles and scripts to external files. Configure nonce-based CSP in `next.config.ts`.

### P3-3: Add IP restriction for admin surface (F-06)

In `middleware.ts`: check `x-forwarded-for` against allowed IP list (env variable `ADMIN_ALLOWED_IPS`). Deny with 403 for IPs outside the allowlist.

### P3-4: Add `/analytics` response caching (F-50)

Cache analytics responses in Redis with 60–300s TTL:
```python
async def get_analytics_overview(...):
    cache_key = f"analytics:overview:{date_range}"
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)
    result = _compute_overview(...)
    await redis.setex(cache_key, 300, json.dumps(result))
    return result
```

### P3-5: Fix WCAG 2.1 AA violations (F-51, F-52, F-53, F-54)

- Add `aria-label` to all unlabeled form inputs in rides, users, drivers, promotions, monitoring pages
- Add text labels to color-only status indicators
- Create `loading.tsx` files in `src/app/dashboard/` and key sub-routes
- In `error.tsx`: replace `error.message` with generic message in production; send details to Sentry only

### P3-6: Fix admin stats endpoint serial queries (F-49)

Replace the 14-iteration daily-chart loop with a single `date_trunc('day', ...) GROUP BY` Postgres query. Replace Python-side revenue `sum(float(...))` with Postgres `SUM()` aggregate.

### P3-7: Add row-count cap to export endpoints (F-43)

Add server-side `max_export_rows = 5000` guard in the endpoints that supply export data. Return a `X-Export-Row-Count` response header. Require `super_admin` for full-dataset exports.

---

## Remediation Sprint Mapping

| Sprint | Items | Effort |
|---|---|---|
| Hotfix (immediate) | P0-1 (credential mask), P0-2 (privilege escalation), P0-3 (surge cap) | ~2 days |
| Sprint 1 | P1-1 through P1-9 | ~5 days |
| Sprint 2 | P2-1 (MFA), P2-2 (rate limits), P2-3 (Pydantic models), P2-4 (audit coverage) | ~5 days |
| Sprint 3 | P2-5 through P2-9 + P3-1, P3-2, P3-3 | ~5 days |
| Sprint 4 | P3-4 through P3-7 | ~3 days |

**Total estimated remediation effort:** 3–4 weeks of focused backend/frontend work.

---

## Tracking

As each finding is fixed, add the commit SHA to the finding entry in the relevant phase report. Once all P0 and P1 items are resolved, re-run the DAST walkthrough (Phase 3) focusing on F-24, F-25, F-26, F-37, F-41, and F-46 to confirm closure.
