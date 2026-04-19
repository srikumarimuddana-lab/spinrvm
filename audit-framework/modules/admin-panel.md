# Module: Admin Panel

**Status:** Not yet audited
**Root folder:** `backend/routes/admin/` + admin frontend (location TBD)

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
| 15 | Accessibility | If web-based | |

---

## Admin-Specific Security Checklist

### Authentication
- [ ] Admin accounts require MFA (TOTP or hardware key) — SMS is not sufficient
- [ ] Admin session timeout: ≤ 30 minutes idle
- [ ] Admin login IP-restricted to office/VPN IP range
- [ ] Failed admin login generates an alert
- [ ] Admin password policy: ≥ 16 chars, bcrypt cost ≥ 12

### Authorisation
- [ ] Role-based access control: super-admin vs. support vs. finance vs. operations
- [ ] Support cannot access payment card data or payout bank accounts
- [ ] Finance cannot modify driver status
- [ ] Every admin action checks role — not just "is_admin=True"

### Audit Logging
- [ ] Every admin action logged: who, what, when, which record
- [ ] Logs immutable — admin cannot delete their own audit trail
- [ ] Bulk operations (suspend all drivers in a city) require second confirmation
- [ ] Logs exported and stored outside the primary database

### Data Access
- [ ] PII fields (licence number, bank account) require elevated permission to view
- [ ] "View as driver/rider" feature shows only what the user sees — not raw DB
- [ ] Search results paginated — no "export all users" without approval
- [ ] Sensitive reports (earnings, tax) require finance role

---

## Known Admin Routes (from driver-app v4 audit discovery)

- `backend/routes/admin/faqs.py` — FAQ CRUD (confirmed exists)
- Other admin routes: TBD during audit

---

## Pre-Audit Setup

1. Map all files in `backend/routes/admin/`
2. Confirm admin frontend location (separate repo? Next.js in `/admin`?)
3. Identify admin roles and permissions in the DB schema
4. Run Dimension 02 first — confirm MFA is enforced
