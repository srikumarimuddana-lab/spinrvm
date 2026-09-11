# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (admin portal security/RBAC audit, requested by user) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| Related issue or gap ID | `docs/audit/2026-09-10-admin-portal-security-rbac-audit.md`, finding W2 (part 3 of 3 — migration_status) |

## 1. Issue / gap identified

Same class of gap as W2a/W2b: `migration_status_router` is mounted
`require_super_admin`, but had no independent per-handler role check as a
redundant layer. Read-only endpoint (no writes), lowest sensitivity of the
7 routers in this finding, but the existing test file already had a
`staff_admin_override` fixture explicitly documented as "a non-super_admin
who has somehow passed the router gate" — anticipating exactly this fix.

## 2. Root cause

Same as W2a/W2b — no per-handler `_require_super_admin()` helper existed.

## 3. Fix / remediation

Added the `_require_super_admin(admin)` helper (+ `HTTPException` import,
not previously imported in this file) to `migration_status.py`, called as
the first line of `admin_get_migration_status`.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one file's one handler.**
- **What could regress:** nothing — every legitimate caller is already
  `super_admin`; the existing `test_requires_super_admin` test (via HTTP,
  previously catching this only at the mount) still passes.

## 5. User-experience effect

None. No observable change for a legitimate super_admin caller.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/migration_status.py` | Added `HTTPException` import and `_require_super_admin()` helper; called in `admin_get_migration_status` | Close the missing-redundant-check gap |
| `backend/tests/test_admin_migration_status.py` | New direct-call test proving the guard fires independent of FastAPI routing/mount | Prove the redundant layer actually works |

## 7. Before / after

```python
# Before
@router.get("/migration-status")
async def admin_get_migration_status(admin: dict = Depends(get_admin_user)):
    """Read-only. Returns all 19 tool statuses in dependency order."""
    report = svc.get_migration_status()

# After
@router.get("/migration-status")
async def admin_get_migration_status(admin: dict = Depends(get_admin_user)):
    """Read-only. Returns all 19 tool statuses in dependency order."""
    _require_super_admin(admin)
    report = svc.get_migration_status()
```

## 8. Rollback plan

`git revert`-safe. Pure defense-in-depth addition, no data/schema involved.

## 9. Verification performed

- [x] `pytest tests/test_admin_migration_status.py tests/test_migration_status_service.py -q` — 34 passed, 0 failed.
- [x] `ruff check` + `ruff format --check` — clean.
- [x] Confirmed via direct-call test that the new per-handler check fires independently of the mount.

## What was NOT verified

- Not tested against live Supabase — mocked dependencies only.
- Full backend test suite not run in this pass; deferred to the final
  combined verification pass across all 9 audit fixes (this is the last
  of the W2 sub-fixes — that combined pass is next).
