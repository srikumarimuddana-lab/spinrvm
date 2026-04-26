# P4 — Admin Future Features: Backlog After Stabilization

These 4 items are LOW-priority backlog: feature-completeness gaps and a password-policy hardening item that aren't gating beta, launch, or post-launch hardening, but each removes a workflow that currently requires API or DB access. Schedule once P0–P3 are landed and the admin panel is operationally stable.

Source audit: `reports/audits/2026-04-25-admin-panel-audit-v1.txt`
Branch: `claude/audit-continuation-batch-2`

**Estimated total effort:** ~13–19 hours.

---

## A-P4-1 · Document Requirements Configuration UI

**What's wrong:** `backend/routes/admin/documents.py` exposes endpoints to create/update/delete document requirement types (`/admin/documents/requirements`) but `admin-dashboard` has no UI to configure them. From the admin's perspective, document types are effectively hardcoded — adding a new requirement (e.g. winter tire certificate) requires direct API calls.

**File to fix:** new section in `admin-dashboard/src/app/dashboard/documents/` + a `requirements/page.tsx` sub-route

**How to fix:** Standard list + edit form per the patterns in `/dashboard/promotions`. Columns: name, applies_to (driver/vehicle), required (bool), expiry_required (bool), active. Modal-based create/edit; soft-delete (set `active=false`) instead of row delete to preserve historical references. Pair with A-P1-6 (audit log) so changes are traceable.

**Regression test:** Playwright E2E: navigate to `/dashboard/documents/requirements`, create a new requirement, verify it appears as an option in the driver onboarding flow.

**Why it matters:** Removes a workflow that requires backend API access. Today, configuring a new document type means a deploy or a hand-crafted curl — both broaden privilege blast radius.

**Effort:** 4–6 h · **Severity:** MEDIUM · **Risk score:** 6 · **Audit ref:** 01-2

---

## A-P4-2 · Payout Management Page

**What's wrong:** `backend/routes/admin/drivers.py` has `GET /payouts` and `/payouts/stats` endpoints, but `admin-dashboard` has no `/dashboard/earnings/payouts` page. Finance staff can't review, approve, or retry failed driver payouts from the panel — every payout intervention requires direct API access.

**File to fix:** new `admin-dashboard/src/app/dashboard/earnings/payouts/page.tsx` + `[id]/page.tsx`

**How to fix:** List view with filters by status (pending / processing / paid / failed), driver search, date range. Drill-down per payout shows line items, Stripe transfer ID, retry button (calls existing retry endpoint). Failed-payout list defaults to top of page. Gated behind `Depends(require_module("payouts"))` (added by A-P1-1) and `require_role("finance")` for retry.

**Regression test:** Playwright E2E: filter to failed payouts, click retry on one, assert status flips to processing; assert audit-log row written.

**Why it matters:** Payouts are the core driver-trust feature; today a failed payout requires an engineer to debug. Pairs with A-P1-1 (module enforcement) and the audit-log family in P1/P3.

**Effort:** 6–8 h · **Severity:** MEDIUM · **Risk score:** 6 · **Audit ref:** 01-4

---

## A-P4-3 · Password Complexity + Common-Password Blacklist

**What's wrong:** `backend/routes/admin/auth.py:443` (`/change-password`) enforces ≥12 characters. The startup check for the super-admin password requires 20 characters. There's inconsistency — and neither path requires uppercase + digit + symbol, nor checks against a common-password list. `Password123!` passes both checks today.

**File to fix:** `backend/routes/admin/auth.py:443` + new `backend/utils/password_policy.py`

**How to fix:**
```python
# backend/utils/password_policy.py
COMMON_PASSWORDS = frozenset(_load("data/10k-most-common-passwords.txt"))

def validate_admin_password(pw: str) -> None:
    if len(pw) < 20:
        raise HTTPException(422, "password_too_short")
    if not (any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)
            and any(c in string.punctuation for c in pw)):
        raise HTTPException(422, "password_complexity_required")
    if pw.lower() in COMMON_PASSWORDS:
        raise HTTPException(422, "password_too_common")
```
Apply to `/change-password`, the forgot-password reset endpoint (A-P3-4), and the staff-create flow. Use the SecLists 10k-most-common file (Apache-2.0).

**Regression test:** `Password123!` → 422 (no symbol — wait, has !; complexity passes but length fails). Try `Aaaaaaaaaaaaaaaaaaaa1!` (20 chars, complexity ok, not common) → 200. Try `Passwordpassword1234!` (common-pattern) → 422 if in the blacklist.

**Why it matters:** Aligns admin password policy with the super-admin startup check and removes the "trivially guessable but technically valid" middle ground. Cheap once the blacklist is bundled.

**Effort:** 2–3 h · **Severity:** LOW · **Risk score:** 4 · **Audit ref:** 02-10

---

## A-P4-4 · Cursor Pagination on Pending Documents Endpoint

**What's wrong:** `backend/routes/admin/drivers.py:226` — `admin_get_pending_documents` fetches up to 10,000 pending documents per call. The endpoint accepts `limit`/`offset` Query params but the audit found the limit isn't strictly enforced down to the DB layer in every code path. As the driver base grows past a few hundred, this becomes slow and risks request timeout.

**File to fix:** `backend/routes/admin/drivers.py:226`

**How to fix:**
```python
@router.get("/drivers/pending-documents")
async def admin_get_pending_documents(
    limit: int = Query(50, ge=1, le=100),
    cursor: Optional[str] = None,
    ...
):
    docs = await db_supabase.get_rows(
        "driver_documents",
        {"status": "pending", **({"id": {"$gt": cursor}} if cursor else {})},
        order="id", limit=limit + 1,
    )
    next_cursor = docs[-1]["id"] if len(docs) > limit else None
    return {"items": docs[:limit], "next_cursor": next_cursor}
```
Cap at 100 per page; use `id`-based cursor (stable, indexed). Update the admin UI to call with `next_cursor` instead of offset.

**Regression test:** Seed 500 pending documents; call endpoint with default limit; assert response has 50 items + a `next_cursor`; second call with that cursor returns the next 50.

**Why it matters:** Closes the last unbounded admin query in the documents domain (A-P0-3 closed GPS, A-P2-12 closed driver ride history). Prevents the same "silently caps at N, then breaks" failure mode at scale.

**Effort:** 1 h · **Severity:** MEDIUM · **Risk score:** 6 · **Audit ref:** 08-5

---

## Checklist

- [ ] A-P4-1 Document requirements configuration UI (01-2)
- [ ] A-P4-2 Payout management page at `/dashboard/earnings/payouts` (01-4)
- [ ] A-P4-3 Admin password ≥20 chars + complexity + common-password blacklist (02-10)
- [ ] A-P4-4 Cursor pagination on pending documents endpoint (08-5)

## After this file

- All admin remediation buckets (P0–P4) are now scoped: 3 + 11 + 12 + 12 + 4 = 42 items total, ~157–187 hours.
- Next: admin Phase E (D17–D23) audit to surface the dimensions not covered by the existing 8-task admin audit (CI/CD, secrets, dependencies, supply chain, observability).
- Then: master rollup combining backend + rider + driver + admin findings into one cross-module priority view.
