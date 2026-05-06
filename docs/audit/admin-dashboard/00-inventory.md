# Admin Dashboard Audit — Phase 0 Inventory

**Date:** 2026-04-26  
**Auditor:** Claude Code  
**Branch:** `claude/admin-audit-review-ROzNO`  
**Scope:** `backend/routes/admin/` + `admin-dashboard/` (Next.js 16)

---

## 1. Surface Map

### 1.1 Backend — Admin Route Files

| File | Router prefix (final) | # Endpoints | `require_module` | Audit-log writes |
|---|---|---|---|---|
| `auth.py` | `/api/admin/auth` | 6 | — (public router) | 0 |
| `analytics.py` | `/api/v1/admin/analytics` | 6 | `dashboard` | 0 |
| `documents.py` | `/api/v1/admin/documents` | 5 | `documents` | 0 |
| `drivers.py` | `/api/v1/admin/drivers` | 13 | `drivers` | 0 |
| `faqs.py` | `/api/v1/admin/faqs` & `.../notifications` | 6 | `support` | 0 |
| `legal_documents.py` | `/api/v1/admin/legal-documents` | 2 | `documents` | 0 |
| `maintenance.py` | `/api/v1/admin/maintenance` & `.../audit-logs` | 3 | `dashboard` | 4 |
| `messaging.py` | `/api/v1/admin/cloud-messaging` | 4 | `notifications` | 0 |
| `monitoring.py` | `/api/admin/monitoring` ⚠️ | 5 | **none** | 0 |
| `promotions.py` | `/api/v1/admin/promotions` | 6 | `promotions` | 0 |
| `rides.py` | `/api/v1/admin/rides` & `.../earnings` | 16 | `rides` | 0 |
| `service_areas.py` | `/api/v1/admin/service-areas` & `.../areas` | 13 | `service_areas` | 0 |
| `settings.py` | `/api/v1/admin/settings` | 4 | `settings` | 0 |
| `staff.py` | `/api/v1/admin/staff` | 6 | `staff` | 3 (create/update/delete) |
| `subscriptions.py` | `/api/v1/admin/subscription-plans` | 6 | `earnings` | 0 |
| `support.py` | `/api/v1/admin/disputes` & `.../tickets` & `.../flags` | 22 | `support` | 0 |
| `users.py` | `/api/v1/admin/users` | 3 | `users` | 1 (status change) |
| `vehicle_fleet.py` | `/api/v1/admin/vehicle-types` & `.../fare-configs` | 14 | `vehicle_types` | 0 |
| `wallet.py` | `/api/v1/admin/wallet` | 3 | `earnings` | 2 (credit/debit) |

> **⚠️ monitoring.py anomaly:** This router has its own `prefix="/admin/monitoring"` and is mounted at `/api` directly in `server.py` (not inside `admin_router`). It uses per-endpoint `Depends(get_admin_user)` but has **no `require_module()` restriction** — any authenticated admin can call all monitoring + Redis-flush endpoints regardless of their assigned modules.

**Total backend admin endpoints:** ~124 (excluding auth)  
**Endpoints with audit logging:** 6 of ~124 (4.8%)  
**Files with zero audit logging:** 16 of 19

---

### 1.2 Backend — Admin Auth Routes (public, no auth gate)

| Method | Path | Rate limit | Notes |
|---|---|---|---|
| `GET` | `/api/admin/auth/session` | — | Returns current session from JWT |
| `POST` | `/api/admin/auth/login` | 5/min/IP | bcrypt verify; issues JWT + refresh token |
| `POST` | `/api/admin/auth/refresh` | 20/min/IP | Rotates refresh token |
| `POST` | `/api/admin/auth/logout` | 10/min/IP | Revokes refresh token |
| `POST` | `/api/admin/auth/logout-all` | 5/min/IP | Bumps `token_version` |
| `POST` | `/api/admin/auth/change-password` | 3/min/IP | Requires current password; 12-char minimum |

---

### 1.3 Frontend — Next.js Route Inventory

| URL Path | Module gated | Auth required | Backend dependency |
|---|---|---|---|
| `/login` | — | No | `POST /api/admin/auth/login` |
| `/dashboard` | — | Yes (middleware) | `GET /api/admin/auth/session` |
| `/dashboard/analytics` | `dashboard` | Yes | analytics endpoints |
| `/dashboard/audit-logs` | `dashboard` | Yes | `GET /api/v1/admin/audit-logs` |
| `/dashboard/cloud-messaging` | `notifications` | Yes | messaging endpoints |
| `/dashboard/corporate-accounts` | — | Yes | corporate routes (non-admin router) |
| `/dashboard/corporate-accounts/[id]` | — | Yes | corporate detail routes |
| `/dashboard/corporate-accounts/[id]/members` | — | Yes | corporate members |
| `/dashboard/corporate-accounts/[id]/policy` | — | Yes | corporate policy |
| `/dashboard/corporate-accounts/kyb-queue` | — | Yes | KYB queue endpoints |
| `/dashboard/disputes` | `support` | Yes | disputes endpoints |
| `/dashboard/documents` | `documents` | Yes | document review endpoints |
| `/dashboard/drivers` | `drivers` | Yes | driver management endpoints |
| `/dashboard/earnings` | `earnings` | Yes | earnings/payouts endpoints |
| `/dashboard/forecast` | `dashboard` | Yes | demand-forecast endpoint |
| `/dashboard/heatmap` | `dashboard` | Yes | heatmap-data endpoint |
| `/dashboard/monitoring` | — (any admin) | Yes | monitoring endpoints |
| `/dashboard/monitoring/redis` | — (any admin) | Yes | Redis stats + flush endpoint |
| `/dashboard/notifications` | `notifications` | Yes | notifications/send endpoints |
| `/dashboard/promotions` | `promotions` | Yes | promotions endpoints |
| `/dashboard/quests` | — | Yes | quests endpoints (non-admin) |
| `/dashboard/rides` | `rides` | Yes | rides endpoints |
| `/dashboard/rides/live/[id]` | `rides` | Yes | live ride tracking |
| `/dashboard/service-areas` | `service_areas` | Yes | service area endpoints |
| `/dashboard/settings` | `settings` | Yes | settings endpoints |
| `/dashboard/staff` | `staff` | Yes | staff CRUD endpoints |
| `/dashboard/subscriptions` | `earnings` | Yes | subscription plan endpoints |
| `/dashboard/support` | `support` | Yes | tickets/complaints/flags/FAQs/legal |
| `/dashboard/surge` | `service_areas` | Yes | surge override endpoints |
| `/dashboard/users` | `users` | Yes | user management endpoints |
| `/dashboard/vehicle-types` | `vehicle_types` | Yes | vehicle + fare config endpoints |
| `/company-portal/[id]/*` | — | Yes | company portal sub-routes |
| `/register/driver` | — | No | Driver self-registration |
| `/track/[rideId]` | — | No | Public ride tracking page |

---

## 2. Auth & RBAC Architecture

### 2.1 Authentication Stack

```
Browser (Next.js 16)
  │
  ├─ Access token: in-memory (Zustand) + admin_token cookie (JS-readable, SameSite=Lax)
  ├─ Refresh token: sessionStorage via Zustand persist middleware
  └─ Token refresh: scheduled timer + page-reload silentRefresh()
            │
            ▼
   POST /api/admin/auth/login
            │
            ▼
  FastAPI admin_auth_router (public, no auth gate)
  ├─ admin-001: env ADMIN_EMAIL + ADMIN_PASSWORD (plain compare) — no bcrypt
  └─ other staff: bcrypt verify against admin_staff.password_hash (rounds=12)
            │
            ▼
  _mint_admin_access_token() → HS256 JWT (TTL: ADMIN_ACCESS_TOKEN_TTL_HOURS=1h)
  + issue_refresh_token() → opaque token (30-day, SHA-256 hash stored)
```

### 2.2 RBAC Matrix

| Role | Description | Module restrictions |
|---|---|---|
| `super_admin` | Full access | Bypasses all `require_module()` checks |
| `admin` | Role-level admin access | Must have relevant modules in JWT claim |
| `operations` | Operations team | Module-restricted |
| `support` | Customer support | Module-restricted |
| `finance` | Finance team | Module-restricted |
| `custom` | Custom role | Module-restricted |

**Modules defined in `require_module()` wiring:**
`settings`, `service_areas`, `vehicle_types`, `drivers`, `rides`, `users`, `promotions`, `support`, `documents`, `staff`, `earnings`, `notifications`, `dashboard`

### 2.3 Token Trust Model

- **Admin tokens:** JWT claims fully trusted (`role`, `email`, `modules` from claims)
- **Non-`admin-001` staff:** DB lookup on every request to enforce `is_active` + `token_version`
- **`admin-001`:** JWT claims only — no DB row required, no `is_active` check possible
- **Rider/driver tokens:** Role re-read from DB on every request

---

## 3. Audit Log Coverage Matrix

The `audit_logs` table has columns: `actor_id`, `actor_role`, `action`, `resource`, `resource_id`, `details` JSONB, `ip_address`, `created_at`.

| Domain | Write operations | Audit logged |
|---|---|---|
| Staff | create, update (incl. deactivate), delete | ✅ |
| Users | status change | ✅ |
| Wallet | credit, debit | ✅ |
| Maintenance | cleanup, rollup | ✅ |
| Drivers | approve, reject, suspend, ban, verify, notes | ❌ |
| Rides | cancel | ❌ |
| Service areas | create, update, delete, surge override | ❌ |
| Settings | update (any app setting) | ❌ |
| Promotions | create, update, delete | ❌ |
| Support | resolve dispute, close ticket, resolve flag | ❌ |
| Documents | approve/reject document review | ❌ |
| Vehicle types | create, update, delete | ❌ |
| Fare configs | create, update, delete | ❌ |
| Subscriptions | create, update, delete plan | ❌ |
| Messaging | send broadcast message | ❌ |
| Analytics | — (read-only) | N/A |
| Monitoring | Redis flush | ❌ |
| Legal docs | update | ❌ |
| FAQs | create, update, delete | ❌ |

**Coverage: 6 / ~80 write operations (7.5%)**

---

## 4. Security Configuration Snapshot

### 4.1 Backend

| Property | Value | Status |
|---|---|---|
| JWT algorithm | HS256, pinned in `algorithms=["HS256"]` | ✅ |
| Admin access token TTL | 1 hour (`ADMIN_ACCESS_TOKEN_TTL_HOURS=1`) | ✅ |
| Refresh token TTL | 30 days | ✅ |
| bcrypt rounds | 12 | ✅ |
| Password min length (change) | 12 characters | ✅ |
| Password min length (create) | Not enforced on staff creation | ⚠️ |
| Login rate limit | 5/min per IP (slowapi) | ✅ |
| MFA (TOTP/hardware key) | **Not implemented** | ❌ |
| IP restriction for admin | **Not implemented** | ❌ |
| Failed login alerting | **Not implemented** | ❌ |
| CORS wildcard in prod | Blocked at startup | ✅ |
| `token_version` revocation | Implemented for non-`admin-001` | ✅ |
| Redis flush restricted to allowlist | Yes (`_FLUSHABLE_PREFIXES`) | ✅ |

### 4.2 Frontend (Next.js)

| Property | Value | Status |
|---|---|---|
| `X-Frame-Options` | `DENY` | ✅ |
| `X-Content-Type-Options` | `nosniff` | ✅ |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains; preload` | ✅ |
| `Content-Security-Policy` | Set, but `script-src 'unsafe-inline' 'unsafe-eval'` | ⚠️ |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | ✅ |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` | ✅ |
| `admin_token` cookie | SameSite=Lax, Secure (non-localhost) — **JS-readable** (no `HttpOnly`) | ⚠️ |
| Refresh token storage | `sessionStorage` (not `localStorage`) | ✅ |
| Access token storage | In-memory only (not persisted) | ✅ |
| `next` redirect sanitisation | Same-origin check in `/login?next=...` | ✅ |
| Next.js middleware route guard | `admin_token` cookie checked | ✅ |

---

## 5. Test Coverage Snapshot

| Test file | Lines | Scope |
|---|---|---|
| `test_admin_routes_auth.py` | 75 | Auth endpoints |
| `test_admin_stats.py` | 203 | Stats/earnings |
| `test_corporate_admin_routes.py` | 26 | Corporate admin |
| `__tests__/store/authStore.test.ts` | ~60 | Frontend auth store |
| `__tests__/login.test.tsx` | ~100 | Login page |

**Uncovered admin domains (no dedicated test file):** drivers, rides, service_areas, settings, promotions, support, documents, vehicle_fleet, wallet, messaging, subscriptions, faqs, legal_documents, maintenance, monitoring.

---

## 6. Preliminary Findings Summary

The following issues are flagged at inventory level (not yet deep-dived). Severity grades are provisional pending Phase 2–4 confirmation.

| # | Finding | Area | Provisional Severity |
|---|---|---|---|
| F-01 | MFA not implemented for admin accounts | Auth | HIGH |
| F-02 | Admin access cookie (`admin_token`) is JS-readable (no `HttpOnly`) | Session | MEDIUM |
| F-03 | `monitoring.py` not under `admin_router` — bypasses `require_module()` | RBAC | MEDIUM |
| F-04 | Password min length not enforced on staff creation, only on change | Auth | MEDIUM |
| F-05 | CSP `script-src` includes `unsafe-inline` and `unsafe-eval` | Headers | MEDIUM |
| F-06 | No IP restriction / allowlist for admin surface | Auth | MEDIUM |
| F-07 | Audit log coverage: 7.5% of write operations (61 of ~74 write endpoints unlogged) | Audit | HIGH |
| F-08 | No failed-login alerting / anomaly detection | Auth | MEDIUM |
| F-09 | `admin-001` super-admin has no `is_active` check (env-only account) | Auth | LOW |
| F-10 | `monitoring.py` Redis flush has no module restriction (any admin role) | RBAC | MEDIUM |

---

## 7. Next Phase Gates

- **Phase 1 (SAST):** Run ESLint, TSC strict, `npm audit`, `ruff`, `pip-audit` against both surfaces
- **Phase 2 (Auth deep-dive):** Trace full login flow, token lifecycle, revocation paths, session timeout behaviour
- **Phase 3 (DAST):** Spin up stack; walk all 25+ dashboard routes with both valid and invalid tokens; test RBAC enforcement; test input validation
- **Phase 4 (Backend security):** Input validation on all Pydantic models; SQL injection surface; file upload security; PII in responses
- **Phase 5 (Privacy/logging):** PII in audit logs; PIPEDA compliance; data retention; log hygiene
- **Phase 6 (Performance/UX/a11y):** N+1 DB queries; pagination; WCAG 2.1 AA; i18n
- **Phase 7 (Report + Remediation plan):** Consolidated findings, risk-ranked remediation plan

---

*Pause point: present this inventory for user review before Phase 1.*
