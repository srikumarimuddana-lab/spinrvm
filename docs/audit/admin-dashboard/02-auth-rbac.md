# Admin Dashboard Audit — Phase 2: Auth, Session & RBAC

**Date:** 2026-04-26

---

## 1. Authentication Flow — Full Trace

```
1. POST /api/admin/auth/login (rate-limited 5/min/IP)
   │
   ├─ admin-001 branch: plain string compare against ADMIN_EMAIL / ADMIN_PASSWORD (env vars)
   │   └─ No DB lookup. token_version fixed at 0. Never bcrypt — faster for env-var path.
   │
   └─ Staff branch: DB lookup admin_staff by email.lower()
       ├─ bcrypt verify (rounds=12, fallback from legacy SHA-256)
       ├─ is_active check → 403 if false
       └─ updates last_login timestamp

2. _mint_admin_access_token() → HS256 JWT
   Claims: user_id, email, role, modules, phone, token_version, iat, exp
   TTL: ADMIN_ACCESS_TOKEN_TTL_HOURS (default=1h, prod should set 1h)
   Key: settings.JWT_SECRET (shared with rider/driver tokens — same key)

3. issue_refresh_token() → opaque SHA-256 stored in refresh_tokens table
   TTL: 30 days. audience="admin" scoped — prevents rider token cross-exchange.
   Rotation: on /refresh, old token gets replaced_by + revoked_at stamped.

4. Client storage:
   - Access token: in-memory (Zustand) + admin_token cookie (JS-readable)
   - Refresh token: sessionStorage via Zustand persist (refresh only)

5. Scheduled proactive refresh: 5 min before exp via setTimeout
   Page reload: silentRefresh() in onRehydrateStorage
```

---

## 2. Session / Cookie Analysis

### Access token cookie (`admin_token`)
```javascript
// setAuthCookie() in authStore.ts
`admin_token=${encodeURIComponent(token)}; path=/; max-age=28800; SameSite=Lax[; Secure]`
```

| Attribute | Value | Note |
|---|---|---|
| `HttpOnly` | **ABSENT** | JS can read this cookie — XSS steals the access JWT |
| `SameSite` | `Lax` | CSRF protection for navigation requests |
| `Secure` | Conditional (`https:` only) | Correct |
| `max-age` | 28800s (8h) | Longer than access token TTL (1h) |

**Gap:** The comment in `authStore.ts` says the cookie exists "for the Next.js middleware at `src/middleware.ts`" but that file does not exist. The cookie is written but serves no server-side gate.

### Persist storage (sessionStorage)
The Zustand `persist` middleware stores `{ refresh_token, user, isAuthenticated }` in `sessionStorage`. Access token is correctly excluded (not persisted). sessionStorage is tab-scoped — cleared on tab close. Appropriate for refresh tokens.

---

## 3. Token Lifecycle & Revocation

| Scenario | Behaviour | Correct? |
|---|---|---|
| Staff deactivated via PUT /admin/staff/{id} | `token_version` bumped + all refresh tokens revoked | ✅ |
| `/logout-all` called | `token_version` bumped + all refresh tokens revoked | ✅ |
| `/logout` called | Specific refresh token revoked | ✅ |
| Old access token after `logout-all` | Rejected by `get_admin_user` DB check (token_version mismatch) | ✅ |
| Rider refresh token → admin `/refresh` | Rejected (audience check: `row.get("audience") != "admin"`) | ✅ |
| admin-001 `logout-all` | **Not supported** — endpoint rejects `user_id == "admin-001"` | ⚠️ |
| `/session` endpoint after deactivation | Returns `authenticated: true` until token expires (1h) | ⚠️ |
| Expired access token presented to `/session` | Returns `authenticated: false` (jwt.ExpiredSignatureError caught) | ✅ |

### admin-001 revocation gap
`admin-001` has no DB row, so `token_version` cannot be bumped. The `/logout-all` endpoint explicitly blocks `admin-001`. The only revocation mechanism is rotating `ADMIN_PASSWORD` in the environment, which kills future logins but doesn't invalidate in-flight tokens until they expire (1h). This is documented and accepted in the codebase.

---

## 4. RBAC Deep-dive

### Backend enforcement (correct)
`require_module(module)` is wired at `include_router` time in `routes/admin/__init__.py`. Every sub-router (except `auth_router` and `monitoring.py`) requires a module claim in the JWT. `super_admin` always passes.

### RBAC gaps identified

**Gap 1 — `monitoring.py` bypasses `require_module()`:**  
Mounted at `/api` directly (not inside `admin_router`). Per-endpoint `Depends(get_admin_user)` confirms admin role, but no module restriction. A `support` role admin with only `support` module can call `POST /api/admin/monitoring/redis/flush-prefix`.

**Gap 2 — Frontend module gate is display-only:**  
The sidebar filters nav items by `user.modules` but direct URL navigation to `/dashboard/drivers` works even if the user lacks the `drivers` module. The backend blocks the API calls, but the page renders (with errors) instead of redirecting to a "no access" screen.

**Gap 3 — Missing server-side route middleware:**  
`admin-dashboard/src/middleware.ts` does not exist. The cookie comment in `authStore.ts` says it is used for "the Next.js middleware" but that file was never created. Dashboard layout protection is entirely client-side (`useEffect` redirect hook). An unauthenticated request receives the page's initial HTML (though no sensitive data is present in it without API calls).

### RBAC matrix — confirmed enforced

| Role | Module example | API result without module |
|---|---|---|
| `support` | Calls `PUT /api/v1/admin/drivers/{id}` | 403 (requires `drivers` module) |
| `finance` | Calls `GET /api/v1/admin/staff` | 403 (requires `staff` module) |
| `super_admin` | Any endpoint | 200 (bypasses all `require_module`) |

---

## 5. Password Policy

| Check | Status |
|---|---|
| Min length on staff creation | ✅ 12 chars enforced (`staff.py:106`) |
| Min length on password change | ✅ 12 chars enforced (`auth.py:443`) |
| bcrypt cost factor | ✅ rounds=12 |
| Legacy SHA-256 transparent upgrade | ✅ on next login |
| Password exposed in any API response | ✅ `password_hash` stripped from all staff responses |
| Email format validation on login | ❌ `email: str` — no EmailStr/format constraint |
| Account lockout after N failed logins | ❌ Rate limit only (5/min per IP) — no per-account lockout |

---

## 6. Phase 2 New Findings

| ID | Finding | Severity |
|---|---|---|
| F-16 | `/session` endpoint decodes JWT without `is_active`/`token_version` DB check — deactivated staff seen as authenticated for up to 1h | LOW |
| F-17 | No `middleware.ts` — dashboard route protection is client-side only (all API calls still protected) | MEDIUM |
| F-18 | Cookie `max-age` 8h vs access token TTL 1h — stale cookie confuses future middleware if added | LOW |
| F-19 | No idle session timeout — unattended admin session stays active until 30-day refresh token expires | MEDIUM |
| F-20 | Frontend module gate is display-only — direct URL to a forbidden module renders page with API errors instead of "Access Denied" | LOW |
| F-21 | No per-account failed-login counter — brute-force from multiple IPs not detected | MEDIUM |
| F-22 | `LoginRequest.email` has no EmailStr validation — any string accepted, confusing error on lookup | LOW |
| F-23 | `admin-001` cannot force-invalidate its own tokens — only env rotation works | LOW (documented) |

---

## 7. What's Working Well

- bcrypt rounds=12, JWT algorithm pinned, audience-scoped refresh tokens
- Token rotation on every `/refresh` call (old token revoked)
- Deactivation immediately bumps `token_version` + revokes all refresh tokens
- Rate limits on all auth endpoints (5/min login, 3/min change-password)
- Access token never written to `sessionStorage` or `localStorage`
- `next` open-redirect sanitised in login page
- Refresh token audience check prevents rider→admin escalation
