# Admin Dashboard Security Audit — Final Report

**Audit period:** 2026-04-26  
**Phases completed:** 0 (Inventory) → 1 (SAST) → 2 (Auth/RBAC) → 3 (DAST) → 4 (Backend Security) → 5 (Privacy/Logging) → 6 (Perf/UX/A11y)  
**Total findings:** 54 (F-11 through F-54; F-11, F-12, F-13 resolved during audit)  
**Scope:** `backend/routes/admin/` (21 files, ~4,500 lines Python) + `admin-dashboard/` (Next.js 16, ~60 pages/components)

---

## Executive Summary

The Spinr admin dashboard has a solid architectural foundation — JWT auth with audience-scoped tokens, bcrypt password hashing, router-level dependency injection for auth gates, and correct use of Decimal arithmetic in the wallet and fare settlement paths. However, the audit uncovered **2 critical findings**, **3 high findings**, and **25 medium findings** that require attention before the platform can be considered production-hardened for a Canadian regulatory environment.

The two most urgent issues are:

1. **F-24 (CRITICAL):** `GET /settings` returns live Stripe secret keys, Twilio auth tokens, and Google Maps API keys in plaintext JSON to any admin with the `settings` module. These credentials allow arbitrary Stripe charges and account management.

2. **F-25 (HIGH):** Any admin with the `staff` module can promote themselves to `super_admin` by calling `PUT /staff/{id}` with `{"role": "super_admin"}` — no super_admin role check exists on the update or delete paths.

The surge cap bypass (F-26) is a third urgent issue with direct rider impact: `fare_service.py` reads `surge_multiplier` directly from the database without enforcing the 2.5× cap, allowing any `service_areas` module admin to charge riders 10× or more.

---

## Findings Summary

### CRITICAL (2)

| ID | Finding | File:Line |
|---|---|---|
| F-11 ✅ | Missing imports — 5 files crash at runtime (FIXED in `49c4594`) | `drivers.py`, `messaging.py`, `users.py`, `wallet.py`, `analytics.py` |
| F-24 | `GET /settings` returns Stripe secret + Twilio token in API response | `settings.py:47`, `settings_loader.py:22` |

### HIGH (3)

| ID | Finding | File:Line |
|---|---|---|
| F-01 | No MFA for admin accounts | Architecture |
| F-07 | Audit log coverage ~15% of write operations | Multiple |
| F-25 | `PUT/DELETE /staff/{id}` — privilege escalation to super_admin | `staff.py:181, 227` |
| F-26 | Surge cap bypass — `fare_service.py` reads uncapped surge_multiplier | `fare_service.py:148`, `service_areas.py:91` |

> Note: F-01 and F-07 were originally flagged in Phase 0 as HIGH; promoted to the HIGH section for completeness.

### MEDIUM (25)

| ID | Finding |
|---|---|
| F-02 | Admin cookie (`admin_token`) not HttpOnly — XSS steals JWT |
| F-03 | `monitoring.py` bypasses `require_module()` — no module restriction |
| F-05 | CSP includes `unsafe-inline` + `unsafe-eval` |
| F-06 | No IP restriction / allowlist for admin surface |
| F-08 | No failed-login alerting or anomaly detection |
| F-13 ✅ | hono HTML injection CVE (FIXED in `ed5acc8`) |
| F-17 | No Next.js `middleware.ts` — dashboard page protection is client-side only |
| F-19 | No idle session timeout |
| F-21 | No per-account failed-login lockout counter |
| F-27 | `PUT /settings` writes credentials with no audit log |
| F-28 | Mass push notification has no actor_id in audit trail |
| F-29 | Ticket replies hardcode `sender_id: "admin-001"` |
| F-30 | Promotions accept raw dict — negative discount_value accepted |
| F-31 | Location cleanup `days` param unvalidated — `days=0` destroys all GPS |
| F-36 | No per-operation rate limits on wallet credit, staff delete, mass notify |
| F-37 | Wallet credit/debit have no idempotency key — double-write on retry |
| F-38 | 28 admin endpoints accept raw `Dict[str, Any]` — no API-boundary validation |
| F-41 | All 5 CSV/PDF export paths have no server-side audit entry |
| F-42 | Ride invoice PDF includes exact GPS coordinates (PIPEDA data minimization) |
| F-44 | No `beforeSend` PII scrubbing in Sentry — error contexts sent to US servers |
| F-46 | `audit_logs` RLS allows DELETE/UPDATE — not append-only |
| F-48 | `GET /analytics/driver-acceptance` N+1 — up to 1,000 DB round trips |
| F-49 | Admin stats endpoint: 14 sequential DB calls + 2 unbounded limit=10000 fetches |

### LOW / INFO (24)

| ID | Finding |
|---|---|
| F-04 | Password min length not enforced on staff creation (was: only on change) |
| F-09 | `admin-001` has no `is_active` check |
| F-10 | Monitoring Redis flush has no module restriction |
| F-12 ✅ | B904 raise-from in analytics (FIXED) |
| F-14 | 594 `no-explicit-any` ESLint warnings (deferred) |
| F-15 | postcss transitive XSS (accepted/tracked) |
| F-16 | `/session` endpoint doesn't check `is_active`/`token_version` |
| F-18 | Cookie max-age 8h vs token TTL 1h |
| F-20 | Frontend module gate is display-only |
| F-22 | `LoginRequest.email` lacks EmailStr validation |
| F-23 | `admin-001` token force-invalidation not supported |
| F-32 | `resolved_by` in dispute resolution is caller-supplied |
| F-33 | Audit log missing on 9+ modules |
| F-34 | Float arithmetic in fee/earnings display paths |
| F-35 | `GET /users` handlers rely solely on router-level auth dep |
| F-39 | No confirmation gate for Stripe rotation, admin deletion, mass notify |
| F-40 | Settings credentials stored as plaintext in DB |
| F-43 | No row-count cap on bulk CSV exports |
| F-45 | `audit_logs` dual schema — `user_email` column stores PII |
| F-47 | Data residency not enforceable from code |
| F-50 | Analytics endpoints have no response caching |
| F-51 | Form inputs lack ARIA labels (WCAG 2.1 AA SC 1.3.1) |
| F-52 | Color-only status indicators (WCAG 2.1 AA SC 1.4.1) |
| F-53 | No `loading.tsx` Suspense boundaries |
| F-54 | `error.tsx` surfaces `error.message` directly |

---

## What Is Working Well

- **Token architecture:** HS256 JWTs with algorithm pinning, audience-scoped refresh tokens, rotation on every `/refresh`, `token_version` bump on deactivation/logout-all.
- **Password security:** bcrypt rounds=12, 12-char minimum enforced on both create and change paths, transparent legacy SHA-256 upgrade on login.
- **RBAC enforcement:** `require_module()` wired at router-mount time in `__init__.py` — all sub-routers require module claims. `super_admin` bypass is explicit and correct.
- **Wallet arithmetic:** `POST /wallet/credit` and `/debit` use `Decimal` throughout, with audit log entries on every operation.
- **SQL injection surface:** None. All DB access via PostgREST typed interface. Regex inputs use `re.escape()`.
- **Service role isolation:** No admin route uses the Supabase anon key. Service role use is correct given the authenticated admin dependency.
- **Rate limits on auth:** All 5 auth endpoints have specific per-IP limits (3–20/min).
- **Refresh token security:** SHA-256 stored hash, 30-day TTL, audience check prevents rider→admin cross-use.
- **Fare cap in auto-mode:** `SURGE_CAP = 2.5` enforced in `surge_engine.py`'s auto-mode tier calculation.
- **Security headers:** `Strict-Transport-Security`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` are set. Referrer-Policy and Permissions-Policy are present.

---

## Findings by Domain

### Authentication & Session
F-01, F-02, F-08, F-09, F-16, F-17, F-18, F-19, F-21, F-22, F-23

### Authorization / RBAC
F-03, F-06, F-10, F-20, F-25, F-35

### Input Validation
F-30, F-31, F-38

### Financial Security
F-26, F-37

### Audit & Forensics
F-07, F-27, F-28, F-29, F-32, F-33, F-41, F-45, F-46

### Credential / Secret Management
F-24, F-39, F-40

### Privacy & Data Protection (PIPEDA)
F-41, F-42, F-43, F-44, F-47

### Rate Limiting
F-36

### Performance
F-48, F-49, F-50

### Accessibility (WCAG 2.1 AA)
F-51, F-52, F-53, F-54

### Dependencies / Third-Party
F-05, F-13 ✅, F-14, F-15

---

## Risk Heat Map

```
                     Impact
                Low    Medium    High    Critical
           ┌──────────────────────────────────────┐
Very High  │                      F-01    F-24     │
           │                      F-07    F-25     │
  High     │                      F-48    F-26     │
           │               F-27   F-49             │
 Medium    │   F-50        F-28   F-36             │
           │   F-43        F-31   F-41             │
  Low      │   F-47        F-38   F-44             │
           │   F-40        F-37   F-46             │
Very Low   │   many LOW    F-30                    │
           └──────────────────────────────────────┘
```

---

## Regulatory Exposure

| Requirement | Status | Findings |
|---|---|---|
| PIPEDA — audit trail for admin PII access | ❌ ~15% coverage | F-07, F-33, F-41 |
| PIPEDA — data minimization in exports | ❌ GPS at 5dp in PDF | F-42 |
| PIPEDA — cross-border transfer disclosure | ⚠️ Sentry US-hosted | F-44, F-47 |
| Saskatchewan Transportation Act — GPS retention min 3yr | ⚠️ `days=0` bypass | F-31 |
| SGI insurance period accuracy | ✅ Period table append-only (confirmed) | — |
| Ride receipt GST/PST line items | ✅ Confirmed in fare_service | — |

---

## Findings Resolved During Audit

| ID | Fix | Commit |
|---|---|---|
| F-11 | Missing imports — 5 files would crash at runtime | `49c4594` |
| F-12 | B904 raise-from in analytics exception handlers | `49c4594` |
| F-13 | hono ≤4.12.13 HTML injection CVE | `ed5acc8` |
