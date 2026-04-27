# Module: Admin Panel

**Status:** Audit complete (2026-04-26) — 54 findings, 3 resolved during audit, remediation plan filed  
**Root folder:** `backend/routes/admin/` + `admin-dashboard/` (Next.js 16)  
**Audit reports:** `docs/audit/admin-dashboard/` (phases 00–06 + REPORT.md + REMEDIATION_PLAN.md)  
**Applicable dimensions:** 16 (D01–D04, D07, D09–D12, D14–D15, D17–D22)

---

## Why Admin Panels Need Separate Audits

Admin panels are frequently the highest-risk attack surface in any system:
- They have access to every driver and rider's data
- Weak admin auth = complete data breach
- Rate limits and IP restrictions are often forgotten
- Audit logs are often missing
- Bulk operations (suspend all drivers, delete records) are irreversible

---

## Applicable Dimensions

| # | Dimension | Priority | Notes |
|---|---|---|---|
| 01 | Feature completeness | Required | What admin functions exist? |
| 02 | Authentication | CRITICAL | MFA required for admin accounts |
| 03 | Encryption & secrets | Required | Admin DB access should be least-privilege |
| 04 | Input validation | Required | Bulk operations with bad input = disaster |
| 09 | Test coverage | Required | Admin actions are rarely tested |
| 10 | Error handling | Required | Admin errors should never expose internals |
| 11 | Security headers | Required | Admin should be IP-restricted |
| 12 | Compliance | Required | Admin access to PII must be logged |
| 14 | Performance | Required | Bulk queries can bring down the DB |
| 15 | Accessibility | Required | Admin is web-based (Next.js); WCAG 2.1 AA applies |
| 17 | Observability | Required | Admin action logs, structured audit trail, SLIs for bulk ops |
| 18 | DR / BCP | Required | Admin-initiated bulk ops must be recoverable |
| 19 | Fraud | Required | Admin account takeover, insider threat, break-glass misuse |
| 20 | Financial reconciliation | Required | Admin-initiated payouts, refunds, wallet adjustments must reconcile |
| 21 | Threat model / STRIDE | CRITICAL | Blast radius = org-wide; insider threat, privilege escalation |
| 22 | Third-party risk | Required | Admin dashboard vendor chain (Next.js deps, analytics, support tools) |

---

## Audit Results (2026-04-26)

| Severity | Count | Key Examples |
|---|---|---|
| CRITICAL | 2 (1 fixed) | F-24 settings credential exposure, F-11 runtime crashes (fixed) |
| HIGH | 4 | F-01 no MFA, F-07 15% audit coverage, F-25 privilege escalation, F-26 surge cap bypass |
| MEDIUM | 25 | F-02 cookie not HttpOnly, F-17 no middleware.ts, F-37 wallet idempotency, F-41 unlogged exports |
| LOW/INFO | 23 | Session hardening, a11y, analytics performance, data residency |

**Resolved during audit:** F-11 (missing imports), F-12 (B904), F-13 (hono CVE)  
**Remediation:** See `docs/audit/admin-dashboard/REMEDIATION_PLAN.md` — P0 hotfixes estimated at 2 days, full remediation 3–4 weeks.

---

## Admin-Specific Security Checklist (Updated with Audit Results)

### Authentication
- [ ] Admin accounts require MFA (TOTP or hardware key) — SMS is not sufficient **(F-01: NOT DONE)**
- [ ] Admin session timeout: ≤ 30 minutes idle **(F-19: NOT DONE)**
- [ ] Admin login IP-restricted to office/VPN IP range **(F-06: NOT DONE)**
- [ ] Failed admin login generates an alert **(F-08, F-21: NOT DONE)**
- [x] Admin password policy: ≥ 12 chars, bcrypt cost=12 **(done; 12 chars not 16)**

### Authorisation
- [x] Role-based access control: super_admin vs support vs finance **(done — require_module() at router level)**
- [ ] Every admin action checks role — not just "is_admin=True" **(F-25: update/delete staff missing check)**
- [x] Audience-scoped refresh tokens prevent rider→admin escalation

### Audit Logging
- [ ] Every admin action logged: who, what, when, which record **(F-07, F-33: ~15% coverage)**
- [ ] Logs immutable — admin cannot delete their own audit trail **(F-46: RLS allows DELETE)**
- [ ] Bulk operations require second confirmation **(F-39: partial — only Redis flush has gate)**
- [ ] CSV/PDF exports logged with row count **(F-41: NOT DONE)**

### Data Access
- [ ] Credentials (Stripe, Twilio) masked in GET responses **(F-24: CRITICAL — not done)**
- [ ] Export endpoints server-side rate limited and logged **(F-41, F-43: NOT DONE)**
- [x] Search results paginated on users/drivers endpoints
- [ ] GPS coordinates not included in downloadable exports **(F-42: coordinates in PDF)**

---

## Known Admin Routes (21 files confirmed)

`__init__.py`, `auth.py`, `analytics.py`, `documents.py`, `drivers.py`, `faqs.py`,
`legal_documents.py`, `maintenance.py`, `messaging.py`, `monitoring.py`, `promotions.py`,
`rides.py`, `service_areas.py`, `settings.py`, `staff.py`, `subscriptions.py`,
`support.py`, `users.py`, `vehicle_fleet.py`, `wallet.py`

**Architecture note:** All sub-routers under `admin_router` with `dependencies=[Depends(get_admin_user)]` + `require_module()` except `monitoring.py` (mounted separately at `/api` — bypasses `require_module()`, F-03/F-10).
