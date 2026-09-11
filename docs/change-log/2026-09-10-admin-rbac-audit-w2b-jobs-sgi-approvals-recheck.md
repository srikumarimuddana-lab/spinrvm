# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (admin portal security/RBAC audit, requested by user) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| Related issue or gap ID | `docs/audit/2026-09-10-admin-portal-security-rbac-audit.md`, finding W2 (part 2 of 3 — jobs/sgi-forms/export-approvals) |

## 1. Issue / gap identified

Same class of gap as W2a (see `2026-09-10-admin-rbac-audit-w2a-data-transfer-recheck.md`):
`data_transfer_jobs_router`, `sgi_forms_router`, and `export_approvals_router`
are mounted `require_super_admin`, but had no independent per-handler role
check as a redundant layer, unlike 13 sibling super_admin-class routers.
`data_transfer_jobs.py`'s download-link endpoint is the most sensitive of
the three job routes (hands back a live signed URL into another admin's PII
export bundle); `sgi_forms.py` handles SGI regulator-filing PDFs and
criminal-record-check documents; `export_approvals.py` is the dual-approval
gate that protects the Data Transfer export routers themselves.

## 2. Root cause

Same as W2a — no per-handler `_require_super_admin()` helper was added to
these 3 files when written.

## 3. Fix / remediation

Added the same `_require_super_admin(admin)` helper to all 3 files, called
as the first line of each handler:
- `data_transfer_jobs.py`: `list_data_transfer_jobs`, `get_data_transfer_job`, `regenerate_job_download_link`
- `sgi_forms.py`: `generate_sgi_form`, `sgi_removal_queue`, `download_sgi_supporting_documents`, `download_sgi_submission_package`
- `export_approvals.py`: `list_pending_export_approvals`, `approve_export_request`, `deny_export_request`

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these 3 files' 10 handlers.** No other module
  calls these functions directly (grepped).
- **What could regress:** nothing for any legitimate caller — every
  existing caller must already be `super_admin` to pass the mount.

## 5. User-experience effect

None. No observable change for a legitimate super_admin caller.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/data_transfer_jobs.py` | Added `_require_super_admin()` helper; called in all 3 handlers | Close the missing-redundant-check gap |
| `backend/routes/admin/sgi_forms.py` | Same helper; called in all 4 handlers | Same |
| `backend/routes/admin/export_approvals.py` | Same helper; called in all 3 handlers | Same |
| `backend/tests/test_data_transfer_jobs_route.py` | New direct-call test proving the guard fires independent of FastAPI routing/mount | Prove the redundant layer actually works |
| `backend/tests/test_sgi_forms_route.py` | Same, for `sgi_removal_queue` | Same |
| `backend/tests/test_export_approvals_routes_http.py` | Same, for `list_pending_export_approvals` (alongside the pre-existing HTTP-level mount test) | Same |

## 7. Before / after

```python
# Before (export_approvals.py)
@router.get("/export-approvals/pending")
async def list_pending_export_approvals(request: Request, admin: dict = Depends(get_admin_user)):
    """The approval queue -- oldest pending request first."""
    return await approvals.list_pending()

# After
@router.get("/export-approvals/pending")
async def list_pending_export_approvals(request: Request, admin: dict = Depends(get_admin_user)):
    """The approval queue -- oldest pending request first."""
    _require_super_admin(admin)
    return await approvals.list_pending()
```
(Same shape applied to the other 9 handlers across the 3 files.)

## 8. Rollback plan

`git revert`-safe. Pure defense-in-depth addition, no data/schema involved.

## 9. Verification performed

- [x] `pytest tests/test_data_transfer_jobs_route.py tests/test_sgi_forms_route.py tests/test_export_approvals_routes_http.py tests/test_admin_export_approvals.py tests/test_admin_sgi_forms_coverage.py tests/test_data_transfer_jobs.py -q` — 93 passed (90 pre-existing + 3 new), 0 failed.
- [x] `ruff check` + `ruff format --check` — clean on all 6 touched files.
- [x] Confirmed via direct-call tests that the new per-handler checks fire independently of the mount.

## What was NOT verified

- Not tested against live Supabase — mocked dependencies only.
- Full backend test suite not run in this pass; deferred to the final
  combined verification pass across all 9 audit fixes.
