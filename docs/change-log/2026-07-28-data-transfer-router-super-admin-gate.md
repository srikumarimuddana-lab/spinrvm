# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (B11/R-A follow-up on the Data Transfer PIA) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B11 / R-A (`docs/privacy/2026-07-28-pia-data-transfer-export.md`) |

## 1. Issue / gap identified

The PIA's R-A recommendation (and R-001 finding) assumed any admin holding the `bulk_operations` module flag — a set potentially broader than `super_admin` — could access the Data Transfer export/import/search/jobs/SGI-forms routes. Investigating before implementing R-A as literally written (create a narrower `data_transfer_pii_export` flag) found the premise didn't hold: `bulk_operations` does not appear anywhere in `AVAILABLE_MODULES` (`routes/admin/staff.py`) or `ALL_MODULES` (`routes/admin/auth.py`, `admin-dashboard/src/app/dashboard/staff/page.tsx`), nor in any `ROLE_PRESETS`, nor in any migration. The "custom" role grant path (`staff.py`) filters requested modules against `AVAILABLE_MODULES`, so `bulk_operations` can never be assigned to a non-super_admin through any current code path. The admin-dashboard sidebar's own comment confirms this was intentional: *"'bulk_operations' is granted to no staff role; the page + backend enforce strict super_admin."*

Effective access to these 5 routers was therefore already super_admin-only in practice — but only by omission from a list, not by an explicit code-level check. That's fragile: a future engineer adding `bulk_operations` to `AVAILABLE_MODULES` for an unrelated feature (plausible — it's a generic-sounding name) would silently reopen full-fidelity, unredacted PII export/import to every admin holding that flag, with no signal in that PR that anything data-transfer-related was affected.

## 2. Root cause

The original module build (see `routes/admin/__init__.py`'s prior comment, now replaced) deliberately chose `require_module("bulk_operations")` "rather than inventing a new module string that no staff role has been granted yet" — reusing an existing flag name for convenience rather than either (a) explicitly requiring `super_admin` in code, or (b) adding the new flag to the grantable list and consciously deciding who gets it. The implicit "nobody has this flag today" protection was never encoded as an explicit requirement anywhere.

## 3. Fix / remediation

Chosen approach (of three options presented to and selected by the user): add an explicit `require_super_admin` FastAPI dependency (new, in `backend/dependencies/__init__.py`, alongside the existing `require_module`) and use it at `include_router` time for all 5 Data Transfer sub-routers, replacing `Depends(require_module("bulk_operations"))`. This is functionally identical to today's real-world behavior (still nobody but `super_admin` can access these routes) but makes the boundary explicit and independent of what's in `AVAILABLE_MODULES`/`ALL_MODULES` — adding `bulk_operations` to those lists in the future can no longer silently affect this module.

Also removed the now-redundant per-endpoint `_require_super_admin()` checks that PR #2685 had added directly inside `data_transfer_jobs.py`'s three handlers — with the router-level dependency in place, those handler-level checks were dead-weight duplication of the same check, confusing to a future reader ("why is this checked twice?").

## 4. Risk & impact on existing functionality

- **What else reads/writes the same routers?** Only `routes/admin/__init__.py` registers these 5 sub-routers; no other file imports their handlers directly (grep-confirmed).
- **Could this regress a working flow?** No admin currently uses these routes as a non-super_admin (verified impossible per §1), so no real user loses access. The only behavioral difference from before this fix: if someone *had* added `bulk_operations` to `AVAILABLE_MODULES` between now and whenever this ships, this change closes that door — which is the point.
- **Blast radius:** isolated to `backend/dependencies/__init__.py` (additive — new function, no existing function changed) and `backend/routes/admin/__init__.py` (5 lines, dependency swap) and `backend/routes/admin/data_transfer_jobs.py` (removal of now-redundant checks).
- **Interaction with the 16 background loops / ride state machine / money:** none.

## 5. User-experience effect

None — no admin's actual access changes (see §4). Purely a hardening of the *mechanism* by which today's already-correct behavior is enforced.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/dependencies/__init__.py` | Added `require_super_admin` dependency function | Reusable, explicit super_admin-only gate for router-level use, matching `require_module`'s existing shape |
| `backend/routes/admin/__init__.py` | 5 `data_transfer_*`/`sgi_forms` router registrations switched from `require_module("bulk_operations")` to `require_super_admin`; comment rewritten to document why | Close the fragile-by-omission gap (§1) |
| `backend/routes/admin/data_transfer_jobs.py` | Removed the now-redundant `_require_super_admin()` helper and its 3 call sites; updated docstrings to reference the router-level gate instead | Avoid duplicated, confusing double-enforcement of the same check |
| `backend/tests/test_data_transfer_jobs.py` | Renamed `bulk_operations_admin_override` → `regular_admin_override` (a plain non-super_admin with no modules — there was never a real way to hold `bulk_operations`); updated module docstring | Keep the test fixture honest about what it's actually testing |

## 7. Before / after

```python
# Before (routes/admin/__init__.py)
admin_router.include_router(data_transfer_export_router, dependencies=[Depends(require_module("bulk_operations"))])
# ...4 more identical lines for import/search/jobs/sgi_forms

# routes/admin/data_transfer_jobs.py
def _require_super_admin(admin: dict) -> None:
    if admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Data Transfer job history requires super_admin")

@router.get("/data-transfer/jobs")
async def list_data_transfer_jobs(...):
    _require_super_admin(admin)
    ...
```

```python
# After (routes/admin/__init__.py)
admin_router.include_router(data_transfer_export_router, dependencies=[Depends(require_super_admin)])
# ...4 more identical lines

# backend/dependencies/__init__.py
async def require_super_admin(current_user: dict = Depends(get_admin_user)) -> dict:
    if current_user.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="This module requires super_admin")
    return current_user

# routes/admin/data_transfer_jobs.py — no per-endpoint check needed anymore
@router.get("/data-transfer/jobs")
async def list_data_transfer_jobs(...):
    ...
```

## 8. Rollback plan

`git-revert-safe` — pure access-control mechanism change, no data mutation, no migration. Reverting restores the module-flag-based gate, which (per §1) has the same real-world effect today regardless.

## 9. Verification performed

- [x] Automated tests: `tests/test_data_transfer_jobs.py` (8/8), `tests/test_data_transfer_export.py` (2/2), full `pytest -k "data_transfer or admin"` (518 passed, 1 skipped, 0 failed — no regressions across the wider admin surface).
- [x] `ruff check` clean on all 4 changed/new files.
- [x] Blast-radius grep performed: confirmed `bulk_operations` doesn't appear in any `AVAILABLE_MODULES`/`ALL_MODULES`/`ROLE_PRESETS`/migration; confirmed the 5 data-transfer routers have no other includers.
- [ ] Manual repro in staging — not performed; verified via FastAPI TestClient + `dependency_overrides` only.

## 10. What was NOT verified

- Did not check whether any internal script or support runbook calls these endpoints outside the admin-dashboard UI with a non-super_admin token — would already have been broken before this change too (per §1), so not a new gap, but not independently re-confirmed here.
- Did not verify against a real Supabase/staging admin session.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`).
- [x] Blast radius is stated, not assumed (§4, grep-verified).
- [x] No silent behavior change — none occurred for any real admin (§5); the fix is explicitly a hardening of enforcement mechanism, not a behavior change, and that distinction is stated plainly above rather than implied.
