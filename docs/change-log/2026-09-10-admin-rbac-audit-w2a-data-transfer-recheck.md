# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (admin portal security/RBAC audit, requested by user) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| Related issue or gap ID | `docs/audit/2026-09-10-admin-portal-security-rbac-audit.md`, finding W2 (part 1 of 3 — export/import/search) |

## 1. Issue / gap identified

`data_transfer_export_router`, `data_transfer_import_router`, and
`data_transfer_search_router` are mounted `require_super_admin`, but — unlike
13 sibling super_admin-class routers in this package, which all also carry
an independent per-handler role check — these 3 relied solely on the mount.
These specific routes move full-fidelity, unredacted PII (documents, exact
GPS, for up to 100 entities/request) and back the dual-approval export gate,
so a single point of failure on their access control is a higher-than-usual
risk class.

## 2. Root cause

No per-handler `_require_super_admin()` helper was ever added to these 3
files when they were written, unlike `booking_import.py`, `wallet_import.py`,
`tax_id_import.py`, etc., which all have one. Not currently exploitable
(the mount does enforce it today) but fragile — a future "the mount already
covers this" edit removing what looks like a redundant check would silently
widen access.

## 3. Fix / remediation

Added the same `_require_super_admin(admin)` helper (role check, 403 if not
`super_admin`) already used across this package to all 3 files, called as
the first line of each handler:
- `data_transfer_export.py::export_entities`
- `data_transfer_import.py::validate_bundle_import` and `commit_bundle_import`
- `data_transfer_search.py::search_entities`

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these 3 files' 4 handlers.** No other module
  calls these functions directly (grepped).
- **What could regress:** nothing for any legitimate caller — every
  existing caller must already be `super_admin` to pass the mount, so the
  added handler-level check is a no-op for them. No behavior change for any
  currently-working request.

## 5. User-experience effect

None. No observable change for a legitimate super_admin caller.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/data_transfer_export.py` | Added `_require_super_admin()` helper; called in `export_entities` | Close the missing-redundant-check gap |
| `backend/routes/admin/data_transfer_import.py` | Same helper; called in `validate_bundle_import` and `commit_bundle_import` | Same |
| `backend/routes/admin/data_transfer_search.py` | Same helper (+ new `HTTPException` import); called in `search_entities` | Same |
| `backend/tests/test_data_transfer_export_route.py` | New direct-call test proving the guard fires independent of FastAPI routing/mount | Prove the redundant layer actually works, not just that the mount still blocks (mount alone was already provably sufficient before this fix) |
| `backend/tests/test_data_transfer_import_route.py` | Same, for `validate_bundle_import` | Same |
| `backend/tests/test_data_transfer_search_route.py` | Same, for `search_entities` | Same |

## 7. Before / after

```python
# Before
@router.get("/data-transfer/search")
async def search_entities(request: Request, ..., admin: dict = Depends(get_admin_user)):
    """..."""
    offset = (page - 1) * page_size
    ...

# After
@router.get("/data-transfer/search")
async def search_entities(request: Request, ..., admin: dict = Depends(get_admin_user)):
    """..."""
    _require_super_admin(admin)
    offset = (page - 1) * page_size
    ...
```

## 8. Rollback plan

`git revert`-safe. Pure defense-in-depth addition, no data/schema involved.

## 9. Verification performed

- [x] `pytest tests/test_data_transfer_search.py tests/test_data_transfer_export_route.py tests/test_data_transfer_export.py tests/test_data_transfer_search_route.py tests/test_data_transfer_import_route.py -q` — 52 passed (49 pre-existing + 3 new), 0 failed.
- [x] `ruff check` + `ruff format --check` — clean on all 6 touched files.
- [x] Confirmed via direct-call tests (bypassing FastAPI's dependency
  injection entirely) that the new per-handler check fires on its own —
  not just re-testing what the mount already caught.

## What was NOT verified

- Not tested against live Supabase — mocked dependencies only.
- Full backend test suite not run in this pass; deferred to the final
  combined verification pass across all 9 audit fixes.
