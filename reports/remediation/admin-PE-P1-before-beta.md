# Phase E · P1 — Admin Before-Beta (Phase E Findings)

These items are HIGH severity findings from the Phase E audit (D17–D23) of the admin panel. They must be resolved before the first non-engineering admin (operations, support, finance) gets a dashboard login.

Source audit: `reports/audits/2026-04-26-admin-panel-v2-phase-e.txt`
Branch: `claude/plan-deferred-tasks-qtT8I`

**Estimated total effort:** ~12–18 hours.

---

## Already Implemented in This Session

| Finding | Title | Status |
|---------|-------|--------|
| [17-2] | deploy-admin already chains to backend-deploy | ✅ Already correct in CI |
| [21-3] | Sentry beforeSend PII scrubber | ✅ Already existed; extended with driver_id/rider_id/ride_id keys |
| [21-1] | Sentry blockAllMedia | ✅ Fixed: `blockAllMedia: true` |
| [21-2] | Sentry tracesSampleRate raised to 1.0 | ✅ Fixed |
| [21-4] | Sentry surface tag | ✅ Fixed: `Sentry.setTag('surface', 'admin')` |
| [21-6] | Sentry release tag | ✅ Fixed: `release: NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA` |
| [22-1] | Vercel region pin yyz1 | ✅ Already in vercel.json |
| [23-1] | CSP header | ✅ Already existed; enhanced with form-action, base-uri, object-src |
| [23-2] | X-Frame-Options: DENY | ✅ Already existed |
| [23-3] | HSTS | ✅ Already existed |

---

## A-PE-P1-1 · Refresh Token Persisted in sessionStorage — XSS Stealable

**What's wrong:** `admin-dashboard/src/store/authStore.ts:65, 194` — Zustand `partialize` correctly excludes the access token but includes `refresh_token`. An XSS attacker can steal the refresh token and mint new access tokens for the full 30-day TTL.

**Finding:** `[18-3]` · Severity: HIGH · Risk score: 18 · Regulations: OWASP A01

**File to fix:** `admin-dashboard/src/store/authStore.ts` + `backend/routes/admin/auth.py` (login + refresh endpoints)

**How to fix:**
1. Backend `/admin/auth/login` sets a `Set-Cookie: admin_refresh=...; HttpOnly; Secure; SameSite=Strict; Path=/api/admin/auth/refresh` header.
2. Backend `/admin/auth/refresh` reads the cookie (not a JSON body field).
3. Frontend `authStore` removes `refresh_token` from persisted state; the refresh call becomes a cookie-credentialed request.
4. Coordinate with [18-2] (HttpOnly cookie) in the same PR so both land together.

**Effort:** 4–6 h · **Audit ref:** 18-3

---

## A-PE-P1-2 · Cookie SameSite=Lax on Admin Token

**What's wrong:** `admin-dashboard/src/store/authStore.ts:25` — Token cookie sets `SameSite=Lax`, allowing cross-site GETs. The admin has no cross-site navigation use case that requires Lax.

**Finding:** `[18-1]` · Severity: HIGH · Risk score: 18 · Regulations: OWASP A01

**File to fix:** `admin-dashboard/src/store/authStore.ts:25` (and backend Set-Cookie once [A-PE-P1-1] lands)

**How to fix:** Change `SameSite=Lax` to `SameSite=Strict` in the client store. Once the backend issues the cookie (per [A-PE-P1-1]), enforce Strict server-side.

**Effort:** 0.5 h (once [A-PE-P1-1] is in flight) · **Audit ref:** 18-1

---

## A-PE-P1-3 · Sentry beforeSend Does Not Scrub URL Path Entity IDs

**What's wrong:** Admin URLs contain entity IDs (`/dashboard/drivers/abc123`, `?driver_id=xyz`). The existing `beforeSend` strips query strings but not path segments. Sentry events for page loads capture the full URL.

**Finding:** `[21-3]` (partial) · Severity: HIGH (PIPEDA) · Regulations: PIPEDA

**File to fix:** `admin-dashboard/sentry.client.config.ts` + `sentry.server.config.ts`

**How to fix:**
```typescript
// In beforeSend, after query_string scrub:
if (event.request?.url) {
  event.request.url = event.request.url.replace(/\/[a-f0-9-]{8,}/gi, '/[id]');
}
```

**Effort:** 1 h · **Audit ref:** 21-3 (path scrub extension)

---

## Checklist

- [ ] A-PE-P1-1 Refresh token to HttpOnly cookie (18-3)
- [ ] A-PE-P1-2 Cookie SameSite=Strict (18-1)  
- [ ] A-PE-P1-3 Sentry URL path entity-ID scrub (21-3 extension)
