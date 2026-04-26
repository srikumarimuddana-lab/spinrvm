# P1 — Admin Before-Beta: Fix Before Internal Beta Cohort

These 11 items are HIGH/MED severity gaps that must be closed before the first non-engineering admin (operations, support, finance) gets a dashboard login. They cover RBAC enforcement, terminated-staff token revocation, audit-log coverage for every mutating action, analytics correctness under volume, bulk push fan-out, and shipping the FAQ UI that matches the existing backend.

Source audit: `reports/audits/2026-04-25-admin-panel-audit-v1.txt`
Branch: `claude/audit-continuation-batch-2`

**Estimated total effort:** ~38 hours.

---

## A-P1-1 · Module-Level Access Defined But Never Backend-Enforced (RBAC bypass)

**What's wrong:** `backend/routes/admin/staff.py:49–61` defines `ROLE_PRESETS` like `support → ["dashboard","support","disputes","notifications","users"]`, and JWTs include the modules list. But `backend/dependencies/__init__.py:308–318` only checks that the role is some admin role; it never verifies the requested endpoint's module is in the user's `modules`. A "support" staff member can call `POST /admin/wallet/credit`, `GET /admin/analytics/*`, every other admin endpoint. The module system is purely cosmetic (frontend nav filtering).

**File to fix:** `backend/dependencies/__init__.py:308–318` + every router under `backend/routes/admin/`

**How to fix:**
```python
def require_module(module: str):
    async def _dep(admin = Depends(get_admin_user)):
        if module not in admin.get("modules", []):
            raise HTTPException(403, f"missing_module:{module}")
        return admin
    return _dep
```
Add `Depends(require_module("wallet"))` to `wallet.py`, `Depends(require_module("analytics"))` to `analytics.py`, etc. Place at router level in each module's router init so every endpoint inherits it.

**Regression test:** `test_support_role_cannot_credit_wallet` — issue a "support" token, POST `/admin/wallet/credit`, expect 403. Repeat parametrized over each role × each privileged endpoint.

**Why it matters:** Privilege escalation at the API layer with a known, documented role boundary that exists only in UI navigation. Any compromised low-privilege admin gets every privilege.

**Effort:** 8–10 h · **Severity:** CRITICAL · **Risk score:** 32 · **Regulations:** SOC2 CC6.3 · **Audit ref:** 03-1

---

## A-P1-2 · Deactivating a Staff Member Does Not Invalidate Their Token

**What's wrong:** `backend/routes/admin/staff.py:177–189` writes `is_active=False` to `admin_staff` but never bumps `token_version`. The JWT validator already checks `token_version` for staff tokens (`auth.py:183`), but no callsite increments it on deactivation. A terminated employee retains admin access for up to 12 hours.

**File to fix:** `backend/routes/admin/staff.py:177–189`

**How to fix:**
```python
await db_supabase.update_one(
    "admin_staff", {"id": staff_id},
    {"is_active": False, "token_version": current["token_version"] + 1},
)
```
Apply the same pattern to delete (A-P1-3) and to any role-change to a less-privileged role.

**Regression test:** `test_deactivated_staff_token_rejected` — issue token, deactivate, verify the same token returns 401 within one request.

**Why it matters:** Standard "fired employee, immediate revocation" expectation. Without this, HR coordination has to wait 12 hours for security to actually take effect.

**Effort:** 2 h · **Severity:** HIGH · **Risk score:** 24 · **Regulations:** SOC2 CC6.2 · **Audit ref:** 03-2

---

## A-P1-3 · Deleting a Staff Member Does Not Invalidate Their Token

**What's wrong:** `backend/routes/admin/staff.py:194–197` deletes the row from `admin_staff` but does not revoke active tokens. JWT is self-contained; access remains valid until TTL expires. A fired-and-deleted admin keeps ops access until their 12-hour token runs out.

**File to fix:** `backend/routes/admin/staff.py:194–197`

**How to fix:** Either (a) before delete, push the token's `jti` to a Redis blacklist with TTL = remaining token lifetime; or (b) preferred — depend on A-P0-2 reducing TTL to 1 h, *plus* keep a `revoked_admin_jti` Redis set checked in the auth dependency.

```python
async def delete_staff(staff_id: str, ...):
    staff = await fetch_staff(staff_id)
    await redis.setex(f"admin:revoked:{staff['last_jti']}", 3600, "1")
    await db_supabase.delete_one("admin_staff", {"id": staff_id})
```

**Regression test:** `test_deleted_staff_token_rejected_immediately` — issue token, delete row, send request with same token, expect 401.

**Why it matters:** Same shape as A-P1-2 but harder to reason about because the row is gone. SOC2 evidence requires bounded revocation latency; "wait for TTL" is not a procedure.

**Effort:** 3 h · **Severity:** HIGH · **Risk score:** 24 · **Regulations:** SOC2 CC6.2 · **Audit ref:** 03-3

---

## A-P1-4 · User Status Changes (Suspend/Ban) Are Not Audit-Logged

**What's wrong:** `backend/routes/admin/users.py:79–91` calls `db_supabase.update_one()` to ban or suspend a rider with no `audit_logs` write. Under PIPEDA, actions affecting personal data must be traceable; this leaves no record of who did what to whom.

**File to fix:** `backend/routes/admin/users.py:79–91`

**How to fix:**
```python
await db_supabase.insert_row("audit_logs", {
    "action": "status_change",
    "entity_type": "user",
    "entity_id": user_id,
    "actor_id": admin["id"],
    "old_value": old_status,
    "new_value": new_status,
    "created_at": datetime.utcnow(),
})
```
Pair with the append-only trigger from B-P2-4 so entries can't be erased after the fact.

**Regression test:** `test_user_ban_writes_audit_log` — ban a user, query `audit_logs` filtered by `entity_id`, assert exactly one row with the right actor.

**Why it matters:** Account suspension is a personal-data action; PIPEDA principle 9 (individual access) and SOC2 CC7.2 both require an actor record.

**Effort:** 2 h · **Severity:** HIGH · **Risk score:** 20 · **Regulations:** PIPEDA, SOC2 · **Audit ref:** 05-1

---

## A-P1-5 · Wallet Credit/Debit Are Not Audit-Logged

**What's wrong:** `backend/routes/admin/wallet.py:96–160` mutates rider wallets with no `audit_logs` write. If a rogue admin issues a fraudulent credit, the only evidence is `wallet_transactions` (if it exists) — there's no admin-action record linking the transaction to the staff actor.

**File to fix:** `backend/routes/admin/wallet.py:96–160`

**How to fix:**
```python
await db_supabase.insert_row("audit_logs", {
    "action": "wallet_credit",  # or wallet_debit
    "entity_type": "user",
    "entity_id": user_id,
    "actor_id": admin["id"],
    "amount": str(_d(amount)),  # Decimal-as-string per money rules
    "reason": reason,
    "created_at": datetime.utcnow(),
})
```

**Regression test:** `test_wallet_credit_writes_audit_log` — credit $50, assert `audit_logs` has the credit row with actor + amount intact.

**Why it matters:** Money mutation without actor log is the textbook insider-fraud failure mode. CRA + SOC2 evidence both require it.

**Effort:** 1 h · **Severity:** HIGH · **Risk score:** 24 · **Regulations:** SOC2, CRA · **Audit ref:** 05-2

---

## A-P1-6 · Staff Create/Update/Delete Not Audit-Logged

**What's wrong:** `backend/routes/admin/staff.py:95–197` allows a super_admin to create a backdoor account or elevate any account to super_admin with zero record. This is the highest-risk unlogged action in the system — privilege management with no actor trail.

**File to fix:** `backend/routes/admin/staff.py:95–197`

**How to fix:** Insert an `audit_logs` row on every create/update/delete with the diff:
```python
await db_supabase.insert_row("audit_logs", {
    "action": "staff_created",  # or _updated / _deleted
    "entity_type": "staff",
    "entity_id": new_staff_id,
    "actor_id": admin["id"],
    "old_value": json.dumps(prior),
    "new_value": json.dumps(after),
    "created_at": datetime.utcnow(),
})
```
The diff captures role and module changes, which is the regulator-relevant payload.

**Regression test:** `test_staff_promotion_writes_audit_log` — promote A from "support" to "super_admin", assert exactly one row with old_value containing "support" and new_value containing "super_admin".

**Why it matters:** Without this, a single compromised super_admin can backdoor the platform and leave no trace. SOC2 CC1.4 explicitly requires it.

**Effort:** 2 h · **Severity:** HIGH · **Risk score:** 24 · **Regulations:** SOC2 · **Audit ref:** 05-3

---

## A-P1-7 · Logout Does Not Invalidate the Current Access Token

**What's wrong:** `backend/routes/admin/auth.py:333–350` revokes the refresh token on `/logout` but never the access token. The access token remains valid until TTL expires. On a shared/compromised machine, an admin who logs out is still impersonatable until the window closes.

**File to fix:** `backend/routes/admin/auth.py:333–350`

**How to fix:**
```python
@router.post("/logout")
async def admin_logout(admin = Depends(get_admin_user), token: str = ...):
    decoded = jwt.decode(token, ...)
    await redis.setex(f"admin:revoked:{decoded['jti']}", decoded["exp"] - now(), "1")
    await revoke_refresh_token(admin["id"])
```
The dependency `get_admin_user` already pulls the JWT — extend it to consult the revocation set.

**Regression test:** `test_logout_revokes_access_token` — login → use token → logout → reuse same token → expect 401.

**Why it matters:** Logout-doesn't-mean-logout is the kind of UX bug regulators specifically test. With A-P0-1 + A-P0-2 in place, blast radius is bounded; without them this is the only revocation path.

**Effort:** 6 h · **Severity:** HIGH · **Risk score:** 20 · **Regulations:** SOC2 CC6.2 · **Audit ref:** 02-3

---

## A-P1-8 · Analytics Loads All Rides Into Memory; Filters by Date in Python

**What's wrong:** `backend/routes/admin/analytics.py:229` (`get_analytics_overview`) fetches all rides with `limit=10000, filter={}` and then filters in Python by `start_date`. Once the platform exceeds 10,000 rides, the "30d" view silently truncates — older rides fill the limit, new ones drop off the edge. Both a correctness bug and a performance one. Same shape applies to `get_cancellation_breakdown`.

**File to fix:** `backend/routes/admin/analytics.py:229` (+ other analytics endpoints with the same pattern)

**How to fix:**
```python
rides = await db_supabase.get_rows(
    "rides",
    {"created_at": {"$gte": start_date.isoformat()}},
    order="created_at", desc=True,
    limit=page_size,
)
```
Drop all in-Python date filtering. If aggregation is needed, push to SQL (see B-P2 patterns).

**Regression test:** Seed 15k rides spread across 60 days. Call `/admin/analytics/overview?range=30d`. Assert the count matches the DB count of rides with `created_at >= 30d ago` (not `min(10k, count)`).

**Why it matters:** Silent data truncation on the dashboard the executive team trusts. KPI numbers will look fine until they look impossibly stable, and root-cause investigation will take days.

**Effort:** 2 h · **Severity:** HIGH · **Risk score:** 20 · **Audit ref:** 08-2

---

## A-P1-9 · Bulk Cloud Message Send Is N+1 Push Calls in a Single Request

**What's wrong:** `backend/routes/admin/messaging.py:69–81` loads up to 10,000 user rows then calls `send_push_notification()` sequentially in a `for` loop. With 5,000 riders that's 5,000 sequential async calls within a single HTTP request — it will hit the request timeout and block the event loop for tens of seconds, taking down dispatch with it.

**File to fix:** `backend/routes/admin/messaging.py:69–81`

**How to fix:**
```python
@router.post("/admin/messaging/send")
async def admin_send_cloud_message(payload, background_tasks: BackgroundTasks, ...):
    job_id = await enqueue_bulk_send(payload)
    background_tasks.add_task(_dispatch_bulk, job_id)
    return {"job_id": job_id, "status": "queued"}, 202

async def _dispatch_bulk(job_id):
    sem = asyncio.Semaphore(100)
    async def _send_one(user):
        async with sem:
            await send_push_notification(user, ...)
    await asyncio.gather(*[_send_one(u) for u in users])
```
Track delivery status in a `cloud_message_jobs` table so the admin UI can poll progress.

**Regression test:** Send to 5,000 mock users; assert the HTTP response returns within 200 ms with status 202; assert all 5,000 push calls eventually fire.

**Why it matters:** This endpoint can take down the entire backend by saturating the event loop on a single request. Affects every other in-flight dispatch + payment.

**Effort:** 4 h · **Severity:** HIGH · **Risk score:** 24 · **Audit ref:** 08-3

---

## A-P1-10 · Analytics Endpoints Fall Back to Empty Data on DB Failure (CLAUDE.md violation)

**What's wrong:** `backend/routes/admin/analytics.py:53–55, 143, 230, 367` catch DB errors, log them, and continue with `rides = []`. CLAUDE.md is explicit: "Never `logger.warning` and continue on a DB error." Admins see empty charts that look like real data — there's no signal that the dashboard is broken.

**File to fix:** `backend/routes/admin/analytics.py:53,143,230,367`

**How to fix:**
```python
try:
    rides = await db_supabase.get_rows("rides", {...})
except DatabaseError as e:
    logger.error("analytics overview db failure",
                 extra={"original": e.details.get("original")},
                 exc_info=True)
    raise HTTPException(503, "analytics_unavailable")
```
Frontend renders an explicit "Analytics temporarily unavailable" state instead of a clean-looking empty chart.

**Regression test:** Mock `get_rows` to raise; call analytics endpoint; assert 503 + structured error log.

**Why it matters:** The dashboard powers business decisions. Silent zero-data is worse than a hard error — it's invisible drift in the source of truth.

**Effort:** 2 h · **Severity:** HIGH · **Risk score:** 20 · **Audit ref:** 07-1

---

## A-P1-11 · FAQ Management Backend Has No Admin UI

**What's wrong:** `backend/routes/admin/faqs.py` exposes full CRUD for FAQs but `admin-dashboard` has no `/dashboard/faqs` page. Customer-facing FAQ updates require direct API calls or DB edits — error-prone and untraceable in audit logs.

**File to fix:** new `admin-dashboard/src/app/dashboard/faqs/page.tsx` + `faqs/[id]/page.tsx`

**How to fix:** Standard list + edit form per the patterns in `/dashboard/promotions`. Wire to the existing FAQ endpoints. Include audit-logged save (paired with A-P1-6 schema).

**Regression test:** Playwright E2E: navigate to `/dashboard/faqs`, edit a FAQ, save, assert it appears in the rider/driver app.

**Why it matters:** Removes a workflow that requires API or DB access (which broadens the privilege blast radius). Closes the smallest of the four feature-gap items but the only one that's a HIGH severity.

**Effort:** 4 h · **Severity:** HIGH · **Risk score:** 12 · **Audit ref:** 01-1

---

## Checklist

- [ ] A-P1-1 `require_module()` dependency on every admin endpoint group (03-1)
- [ ] A-P1-2 Bump `token_version` on staff deactivation (03-2)
- [ ] A-P1-3 Token revocation on staff deletion (Redis blacklist) (03-3)
- [ ] A-P1-4 Audit-log every user status change (05-1)
- [ ] A-P1-5 Audit-log every admin wallet credit/debit (05-2)
- [ ] A-P1-6 Audit-log every staff create/update/delete (05-3)
- [ ] A-P1-7 Access-token revocation on logout (02-3)
- [ ] A-P1-8 DB-level date filter in analytics queries (08-2)
- [ ] A-P1-9 Bulk push as background task with `asyncio.Semaphore(100)` (08-3)
- [ ] A-P1-10 Analytics returns 503 on DB error, not empty data (07-1)
- [ ] A-P1-11 Build `/dashboard/faqs` page (01-1)

## After this file

- Move on to `admin-P2-before-launch.md` (12 items): TOTP MFA, idle timeout, Pydantic schemas for messaging/service-areas, Literal types for driver actions, range constraints on settings, backend admin tests, RBAC tests, Next.js security headers, ride-cancel audit log, SQL aggregation for promotions stats, ride-history pagination cap.
