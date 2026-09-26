# 2026-09-26 — Admin driver edit lock on admin_edited_at (CONCURRENCY-001)

Follow-up to #5854; PR #5865. Owner decision 2026-09-25: "Add own timestamp".

### Issue/gap identified
Main (from #5854) locks admin driver edits on `drivers.updated_at`. A driver's own app bumps that column about every 3 s while online, so every edit of an online driver would get a false 409.

### Root cause
`updated_at` is a whole-row freshness marker written by driver location pings and `utils/stale_intent_reconciler.py`, not an "admin edited this" marker. #5854 was squash-merged about a minute before the owner-approved rework to a dedicated column reached its branch.

### Fix/remediation
- Lock on the new admin-only column `drivers.admin_edited_at` (migration 488, already applied to production).
- Every admin save stamps it, locked or not.
- The lock path no longer touches `updated_at`.
- If the `users` write fails after the drivers write committed, return `ERR_DRIVER_PARTIAL_SAVE`, which the dashboard maps to a readable message.

### Risk & impact on existing functionality
- **Writers of `admin_edited_at`:** only `admin_update_driver`. Other admin writers of `drivers` (bulk import, status changes) do not stamp it, so an edit form opened before one of those writes can still save over it. That was already true before #5854; it is logged as a follow-up.
- **Readers:** the dashboard driver edit form only. The drivers list returns `*`, so there is no API shape change.
- **`updated_at` consumers** (the stale-intent reconciler and location freshness) are now untouched by admin saves. Main briefly bumped `updated_at` in the lock path; that is removed.
- **Older dashboard builds** send no key and take the legacy path, which still stamps the marker.

### User experience effect
Internal admins only:
- An online driver can be edited normally again, with no false "changed by someone else".
- A real concurrent edit still shows the conflict toast with Reload.
- A users-row failure shows "Driver details were saved, but the name/email/phone update failed..." instead of a generic error.

No rider or driver change.

### Files modified
| File path | What changed | Why |
|---|---|---|
| backend/migrations/488_drivers_admin_edited_at.sql | New nullable `admin_edited_at TIMESTAMPTZ` (already applied to prod, sha256 `4ee96742...`) | Dedicated admin-only lock column |
| backend/routes/admin/drivers.py | Lock and stamp `admin_edited_at`; add the `ERR_DRIVER_PARTIAL_SAVE` sentinel | Stop false 409s; no silent partial write |
| backend/tests/test_admin_drivers_coverage.py | Lock test class rewritten, with partial-save and no-`updated_at` tests | Regression coverage |
| admin-dashboard/src/app/dashboard/drivers/page.tsx | Sends `expected_admin_edited_at` (null included) | Client half of the lock |
| admin-dashboard/src/app/dashboard/drivers/page.edit-lock.test.tsx | Tests for the new key and null | Coverage |
| admin-dashboard/src/lib/api/drivers.ts | Maps `ERR_DRIVER_PARTIAL_SAVE` to a readable error | The global 5xx sanitizer only passes `ERR_*` codes |
| admin-dashboard/src/lib/__tests__/api.test.ts | Error-mapping tests | Coverage |

### Rollback plan
- **Code:** revert this PR. That takes admins back to main's `updated_at` lock, with its false 409s. The better rollback is to revert both this PR and #5854's dashboard key, which gives no lock at all.
- **Column:** after a backend without it is live, run `ALTER TABLE public.drivers DROP COLUMN IF EXISTS admin_edited_at;`. It is nullable, has no default and nothing else reads it, so leaving it in place is harmless.

### Verification performed
- **Backend:** the `TestUpdateDriverOptimisticLock` class plus the related admin suites pass (311 tests), mocked Supabase.
- **Dashboard:** 3 edit-lock tests and 2 error-mapping tests pass. `tsc --noEmit` is clean and eslint shows 0 errors.
- **Production build:** `npm run build` compiled successfully.
- **Production database:** migration 488 is applied. Checked: the column exists, is nullable, has no default and 0 non-null rows.

### What was NOT verified
- Not run against real Supabase: `update_one` was mocked.
- The PostgREST timestamp round-trip (the format the dashboard reads back versus what it sends) is reasoned about, not observed live.
- The `dashboard-drivers` visual baseline captures only the landing render; the edit flow has no screenshot coverage.
