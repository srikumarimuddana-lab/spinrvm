# P3 — Admin Hardening: Post-Launch Stability and Resilience

These 12 items are MED/LOW severity hardening fixes for the first sprint after public launch. They tighten admin auth (bcrypt the super-admin password, IP whitelist, forgot-password, MFA-on-promotion), test coverage on the dashboard pages and admin auth security paths, leak-free error handling, and the cosmetic-but-tracked `audit_logs` schema rationalization that supports the writes added in P1.

Source audit: `reports/audits/2026-04-25-admin-panel-audit-v1.txt`
Branch: `claude/audit-continuation-batch-2`

**Estimated total effort:** ~38 hours.

---

## A-P3-1 · Hash `ADMIN_PASSWORD` Env Var with bcrypt at Startup

**What's wrong:** `backend/routes/admin/auth.py:182` does `body.password == settings.ADMIN_PASSWORD` — direct string comparison against the env var. If the env var leaks (CI log, deploy manifest, `/env` debug endpoint), the plaintext is exposed. All other staff passwords use bcrypt cost=12 correctly.

**File to fix:** `backend/routes/admin/auth.py:182` + `backend/core/config.py:52`

**How to fix:** At startup, hash `ADMIN_PASSWORD` with bcrypt (cost 12). Store the hash on `Settings`. Compare with `bcrypt.checkpw(body.password.encode(), settings.admin_password_hash)`. The plaintext env var is read once and replaced in memory immediately.

**Regression test:** `test_super_admin_login_uses_bcrypt` — set `ADMIN_PASSWORD=correct`, login with correct → 200; with wrong → 401; assert `Settings.admin_password_hash` is not equal to the plaintext.

**Why it matters:** Defense in depth. With A-P0-1 + A-P0-2 the access surface is narrower, but the env var is still a high-value target.

**Effort:** 2 h · **Severity:** MEDIUM · **Risk score:** 12 · **Audit ref:** 02-6

---

## A-P3-2 · Login Rate Limit Too Permissive (5/min = 300/hr)

**What's wrong:** `backend/routes/admin/auth.py:36–40` rate-limits at 5 requests/minute/IP — 300/hour. Rider OTP enforces 5/hour with a 24-hour lockout; admin accounts (higher value) should be stricter.

**File to fix:** `backend/routes/admin/auth.py:36–40`

**How to fix:** 3 attempts per 30 minutes per IP, plus account-level lockout: 5 failed attempts in 1 hour locks the account (`admin_staff.locked_until`) for 24 hours. Both are enforced; IP-rate limit returns 429, account lockout returns 423.

**Regression test:** 5 failed logins in 1 h on the same account → next attempt returns 423 with `unlock_at` timestamp.

**Why it matters:** Online brute force becomes mechanically infeasible. Pairs with A-P2-1 (MFA) — even without MFA, a 24-hour lockout is enough to make stolen-password attacks visible.

**Effort:** 3–4 h · **Severity:** MEDIUM · **Risk score:** 8 · **Regulations:** SOC2 · **Audit ref:** 02-7

---

## A-P3-3 · No IP Whitelist Per Staff Member

**What's wrong:** `backend/routes/admin/auth.py:177–178` logs `client_ip` but never enforces. Admin login is reachable from any IP globally with no VPN requirement. The most privileged accounts in the system have no network-layer guard.

**File to fix:** `backend/routes/admin/auth.py:177–178` + new column `admin_staff.allowed_ips JSONB`

**How to fix:**
```python
allowed = staff.get("allowed_ips") or []
if allowed and client_ip not in allowed:
    raise HTTPException(403, "ip_not_allowed")
```
Default empty list = no restriction (gradual rollout). super_admin must have at least one entry. CIDR support (`192.0.2.0/24`) for office ranges.

**Regression test:** Set `allowed_ips=["10.0.0.0/8"]`, login from `203.0.113.5` → 403; from `10.0.0.1` → 200.

**Why it matters:** Network-layer defense in depth. Even with stolen credentials and broken MFA, the attacker still needs to be on the corporate network.

**Effort:** 4–6 h · **Severity:** MEDIUM · **Risk score:** 12 · **Audit ref:** 02-8

---

## A-P3-4 · No Forgot-Password Flow for Admin Staff

**What's wrong:** `/admin/auth/change-password` exists for authenticated sessions, but no self-service reset. A locked-out or forgotten-password admin requires manual DB intervention by a super_admin.

**File to fix:** new endpoints in `backend/routes/admin/auth.py`

**How to fix:**
1. `POST /admin/auth/forgot-password {email}` → if email matches a staff row, send a JWT-signed reset link (15 min TTL) to the email on file. Always return 200 to avoid email enumeration.
2. `POST /admin/auth/reset-password {token, new_password}` → validate token, set new password (bcrypt), invalidate refresh tokens, send confirmation email.
3. Rate limit: 3 resets per email per hour, 10 globally per IP per hour.

**Regression test:** Request reset → consume link → login with new password → old refresh token rejected.

**Why it matters:** Operational hygiene. Without it, every locked-out admin is a P1 ticket for the on-call.

**Effort:** 6–8 h · **Severity:** MEDIUM · **Risk score:** 8 · **Audit ref:** 02-9

---

## A-P3-5 · Manual `super_admin` Check on `create_staff` Not Repeated on Update/Delete

**What's wrong:** `backend/routes/admin/staff.py:99` manually checks `admin.get("role") != "super_admin"` on `create_staff`, but `update_staff` and `delete_staff` lack the same guard. A "finance" role staff member could call `PUT /staff/{id}` to update their own role to `super_admin` if RBAC enforcement isn't fully wired (A-P1-1 closes this, but belt + suspenders).

**File to fix:** `backend/routes/admin/staff.py` — every mutation endpoint

**How to fix:** Add `Depends(require_role("super_admin"))` to update and delete (in addition to `Depends(require_module("staff"))` from A-P1-1). One factory function:
```python
def require_role(role: str):
    async def _dep(admin = Depends(get_admin_user)):
        if admin.get("role") != role:
            raise HTTPException(403, f"role_required:{role}")
        return admin
    return _dep
```

**Regression test:** Issue a "finance" token, call `PUT /staff/{id} {"role":"super_admin"}` → 403.

**Why it matters:** Trust-but-verify on the most sensitive privilege ladder.

**Effort:** 1 h · **Severity:** MEDIUM · **Risk score:** 8 · **Audit ref:** 03-4

---

## A-P3-6 · super_admin Promotion Has No Re-Auth or Audit

**What's wrong:** A super_admin can promote any staff to super_admin with one `PUT /staff/{id}` call. No password re-entry, no email confirmation to the promoted user, no audit-log entry until A-P1-6 lands. A compromised super_admin account silently elevates anyone.

**File to fix:** `backend/routes/admin/staff.py:167–189`

**How to fix:**
1. Require password re-entry in the request body for any promotion *to* super_admin.
2. Verify with bcrypt against the actor's stored hash.
3. Send notification email to the promoted user with a 24-hour "I didn't authorize this" revoke link.
4. Audit-log entry already covered by A-P1-6; this layer adds the human-loop check.

**Regression test:** Promote without `password_confirmation` → 422; with wrong password → 401; with right password → 200 + email sent.

**Why it matters:** Privilege-escalation resistance even when the actor's session is compromised. Cheap, high signal.

**Effort:** 3 h · **Severity:** MEDIUM · **Risk score:** 12 · **Regulations:** SOC2 · **Audit ref:** 03-5

---

## A-P3-7 · No Vitest Unit Tests for Admin Dashboard Pages

**What's wrong:** `admin-dashboard/src/__tests__/` (4 files, 248 lines) covers only login + auth store. All 25+ dashboard pages have zero unit tests. E2E Playwright suite checks page-load on 4 pages.

**File to fix:** `admin-dashboard/src/app/dashboard/**/page.tsx`

**How to fix:** Vitest unit test per page covering pagination state, filter state, table rendering, form submission against a mock API. Reach 60% coverage on dashboard pages.

**Regression test:** Itself; CI gate at 60%.

**Why it matters:** Front-end regressions today are caught by hand-testing. Trades hours of ops pain for hours of test code.

**Effort:** 8–16 h · **Severity:** MEDIUM · **Risk score:** 8 · **Audit ref:** 06-3

---

## A-P3-8 · No Security Tests for Admin Auth Scenarios

**What's wrong:** `backend/tests/test_admin_routes_auth.py` lacks tests for brute-force lockout, staff-deactivation token rejection, logout token persistence, role-escalation prevention. The most critical security properties are all untested.

**File to fix:** `backend/tests/test_admin_routes_auth.py`

**How to fix:** Add:
- `test_login_lockout_after_5_failures` (paired with A-P3-2)
- `test_deactivated_staff_token_is_rejected` (paired with A-P1-2)
- `test_support_role_cannot_access_wallet` (paired with A-P1-1)
- `test_only_super_admin_can_create_staff` (paired with A-P3-5)
- `test_logout_invalidates_access_token` (paired with A-P1-7)
- `test_idle_timeout_after_30_min` (paired with A-P2-2)

**Regression test:** Itself.

**Why it matters:** Each P1/P2 fix gets a test that prevents silent regression. Pure leverage.

**Effort:** 4 h · **Severity:** MEDIUM · **Risk score:** 12 · **Audit ref:** 06-4

---

## A-P3-9 · `documents.py` Silently Swallows Requirement Lookup Failure

**What's wrong:** `backend/routes/admin/documents.py:183` — `except Exception: req_name = None` continues silently when fetching the document requirement row fails. The legacy column mapping is then skipped without surfacing — a driver's expiry doesn't propagate, and they may be unable to go online with no error visible to the admin.

**File to fix:** `backend/routes/admin/documents.py:183`

**How to fix:**
```python
try:
    req = await db_supabase.get_one("document_requirements", {"id": req_id})
    req_name = req["name"]
except DatabaseError as e:
    logger.error("document req lookup failed",
                 extra={"req_id": req_id, "original": e.details.get("original")},
                 exc_info=True)
    raise HTTPException(503, "doc_req_unavailable")
```
If the silent-fallback was actually a deliberate compensating control, document it explicitly with a comment that names the upstream invariant — but the audit's reading is that it's a genuine silence.

**Regression test:** Mock the lookup to raise; assert 503 + `logger.error` captured.

**Why it matters:** CLAUDE.md explicit ground rule: "Never silently swallow errors."

**Effort:** 0.5 h · **Severity:** MEDIUM · **Risk score:** 8 · **Audit ref:** 07-3

---

## A-P3-10 · Internal Error Details Leaked in `HTTPException.detail`

**What's wrong:** `backend/routes/admin/documents.py:163` — `raise HTTPException(status_code=500, detail=f"Failed to update document: {e}")`. DB exception messages contain table names, column names, constraint names, sometimes query fragments — useful information for an attacker. Multiple admin routes share this pattern.

**File to fix:** `backend/routes/admin/documents.py:163` (and any sibling)

**How to fix:**
```python
except DatabaseError as e:
    logger.error("doc update failed", extra={"doc_id": doc_id,
                  "original": e.details.get("original")}, exc_info=True)
    raise HTTPException(500, "Document update failed. Please try again.")
```
Generic detail in the response, full diagnostic in the log. Repeat the sweep across `routes/admin/*`.

**Regression test:** Force a DB failure; assert response detail contains no table/column/constraint name; assert the log entry has the full original error.

**Why it matters:** OWASP A05 (Security Misconfiguration) and SOC2 evidence. Cheap; effective.

**Effort:** 1 h · **Severity:** MEDIUM · **Risk score:** 8 · **Regulations:** OWASP A05 · **Audit ref:** 07-4

---

## A-P3-11 · Subscriptions Page CRUD Wiring Incomplete

**What's wrong:** `admin-dashboard/src/app/dashboard/subscriptions/` exists as a page; `backend/routes/admin/subscriptions.py` has plan CRUD; but the wiring is partial — driver subscription listing and management is missing. Audit found incomplete plumbing.

**File to fix:** `admin-dashboard/src/app/dashboard/subscriptions/*`

**How to fix:** Audit the page → confirm each backend endpoint is consumed. Add the missing driver subscriptions table and the create/edit modals for plans. Ensure delete is gated behind a confirmation modal.

**Regression test:** Playwright E2E: create → list → edit → delete → list-shows-removal cycle.

**Why it matters:** Without it, subscription management requires direct DB access — broadens the privilege blast radius.

**Effort:** 3–4 h · **Severity:** LOW · **Risk score:** 6 · **Audit ref:** 01-3

---

## A-P3-12 · Standardize `audit_logs` Schema and Populate It from All Admin Actions

**What's wrong:** `admin-dashboard/src/app/dashboard/audit-logs/page.tsx:44–55` lists actions: `created`, `updated`, `deleted`, `login`, `status_change` — but the `audit_logs` table currently stores only `ride_declined` (from `routes/drivers.py:1870`). The audit-logs page shows empty results for everything else.

**File to fix:** `backend/migrations/NN_audit_logs_schema_standardization.sql` + sweep across `routes/admin/*`

**How to fix:**
1. Migration: ensure `audit_logs` has columns `entity_type, entity_id, actor_id, action, old_value JSONB, new_value JSONB, reason TEXT, amount NUMERIC, created_at TIMESTAMPTZ`. Add indexes on `(entity_type, entity_id)` and `(actor_id, created_at DESC)`.
2. Sweep every admin endpoint that mutates state — the writes added in A-P1-4/5/6 and A-P2-10 already conform. Backfill the historical `ride_declined` rows to the new shape.
3. Pair with B-P2-4 (append-only trigger) so entries are tamper-evident.

**Regression test:** After P1 + P2 land, perform one of each action; assert the audit-logs page shows all of them.

**Why it matters:** Without a standardized schema, the audit-log writes from P1/P2 won't render uniformly in the UI. Cosmetic but blocks the SOC2 evidence story.

**Effort:** 3 h · **Severity:** MEDIUM · **Risk score:** 8 · **Regulations:** SOC2 · **Audit ref:** 05-5

---

## Checklist

- [x] A-P3-1 bcrypt `ADMIN_PASSWORD` at startup (02-6)
- [x] A-P3-2 Tighter login rate limit + 24-hour account lockout (02-7)
- [x] A-P3-3 Optional IP whitelist per staff member (02-8)
- [x] A-P3-4 Forgot-password flow with 15-min reset link (02-9)
- [x] A-P3-5 `require_role("super_admin")` on staff update/delete (03-4)
- [x] A-P3-6 Re-auth + email notification on promotion-to-super_admin (03-5)
- [x] A-P3-7 Vitest unit tests for dashboard pages (06-3)
- [x] A-P3-8 Security test cases for auth (06-4)
- [x] A-P3-9 `logger.error` on document requirement lookup failure (07-3)
- [x] A-P3-10 Generic detail in HTTPException; full diagnostic in logs (07-4)
- [x] A-P3-11 Wire all subscription CRUD actions; add driver subscription management (01-3)
- [x] A-P3-12 Standardize `audit_logs` schema; populate from every admin action (05-5)

## After this file

- Move on to `admin-P4-future-features.md` (4 backlog items): document requirements UI, payouts page, password complexity + common-password blacklist, cursor pagination on pending documents.
