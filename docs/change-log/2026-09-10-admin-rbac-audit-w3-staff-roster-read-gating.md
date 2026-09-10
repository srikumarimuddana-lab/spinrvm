# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (admin portal security/RBAC audit, requested by user) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| Related issue or gap ID | `docs/audit/2026-09-10-admin-portal-security-rbac-audit.md`, finding W3 |

## 1. Issue / gap identified

`AVAILABLE_MODULES`' own comment in `backend/routes/admin/staff.py:63`
states `"staff",  # Only super_admin can access this`, but `GET /api/admin/staff`
(`list_staff`) and `GET /api/admin/staff/{id}` (`get_staff`) were gated only
by the router mount's `require_module("staff")` — a `custom`-role admin
granted just the `"staff"` module (not super_admin) could read every staff
member's email, role, and full `modules` grant. The mutating actions
(`create_staff`, `update_staff`, `mfa-reset`, `delete_staff`) already
correctly required `super_admin`; only the two read endpoints had the gap.

## 2. Root cause

`list_staff`/`get_staff` were written with a plain `Depends(get_admin_user)`
(or no admin dependency at all, for `get_staff`) instead of the
`Depends(require_role("super_admin"))` pattern this same file already uses
for `create_staff`/`reset_staff_mfa`/`delete_staff` — an inconsistency
within the file itself, not a deliberate design choice (the module comment
makes the intended access level unambiguous).

## 3. Fix / remediation

- `list_staff`: `admin: dict = Depends(get_admin_user)` → `Depends(require_role("super_admin"))` (the dependency factory already defined at the top of this file).
- `get_staff`: added `admin: dict = Depends(require_role("super_admin"))` (previously took no admin parameter at all).

Deliberately did **not** remove `"staff"` from `AVAILABLE_MODULES` even
though every staff-PII-touching endpoint now requires `super_admin`
regardless of module grant (making the module string effectively
vestigial, the same class of problem this file already documented and
fixed for `"heatmap"`/`"surge"`/`"pricing"`, lines 68-99). That's a larger,
separate cleanup outside this audit finding's scope — flagged here for a
follow-up `ACTION_ITEMS.md` entry rather than done silently as part of this
fix (surgical-change principle).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `list_staff`/`get_staff`.** Grepped every
  caller of both functions repo-wide — only test files (`test_admin_staff_coverage.py`,
  `test_admin_staff_mfa_reset.py`); no other backend module calls them
  directly. `list_modules()` (the third GET route in this file) was
  deliberately left unchanged — it returns static config (module names +
  role presets), not staff PII, so the audit's roster-disclosure concern
  doesn't apply to it.
- **What could regress:** nothing for the real (super_admin) caller — the
  admin-dashboard's Staff page is only ever reachable by/intended for
  super_admin per the module comment. `test_super_admin_can_list_staff`
  (pre-existing, HTTP-level) confirms this still works.

## 5. User-experience effect

**Internal admin only.** A `custom`-role admin who was granted just the
`"staff"` module (not super_admin) now gets a 403 on the staff roster
instead of being able to view it — closing unintended access, not removing
an intended capability (the module's own comment already said this was
never supposed to work).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/staff.py` | `list_staff` now uses `Depends(require_role("super_admin"))`; `get_staff` gained the same dependency (previously had no admin param at all) | Close the roster-disclosure gap, matching the module's own stated intent |
| `backend/tests/test_admin_staff_coverage.py` | Updated 2 existing direct-call tests to pass `admin=SUPER` instead of `admin=OPS`, matching how `create_staff`/`delete_staff` tests in this same file already do it (a direct function call bypasses FastAPI's `Depends()` resolution entirely, so this doesn't test the new gate — it just keeps the test data honest about who the real caller is) | Consistency with the file's own established convention |
| `backend/tests/test_admin_rbac.py` | 2 new HTTP-level (`TestClient`) tests: a `custom`-role admin holding only `["staff"]` gets 403 on both `GET /api/admin/staff` and `GET /api/admin/staff/{id}` | Prove the actual gap (module-held-but-not-super_admin) is closed — the 3 pre-existing tests in this class only covered roles that don't hold `"staff"` at all |

## 7. Before / after

```python
# Before
@router.get("/staff")
async def list_staff(
    response: Response,
    admin: dict = Depends(get_admin_user),
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    ...

# After
@router.get("/staff")
async def list_staff(
    response: Response,
    admin: dict = Depends(require_role("super_admin")),
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    ...
```

## 8. Rollback plan

`git revert`-safe. Pure authorization tightening, no data/schema change.

## 9. Verification performed

- [x] `pytest tests/test_admin_staff_coverage.py tests/test_admin_staff_mfa_reset.py tests/test_admin_staff_schema_cache_reload.py tests/test_admin_rbac.py tests/test_admin_routes_auth.py tests/test_admin_security.py -q` — 89 passed (87 pre-existing + 2 new), 0 failed.
- [x] `ruff check` + `ruff format --check` — clean on all 3 touched files.
- [x] Blast-radius grep performed (see §4) — only test files call these functions directly.
- [x] New HTTP-level tests specifically cover the gap this finding named (module-held-but-not-super_admin), not just re-confirming the pre-existing "doesn't hold the module at all" cases.

## What was NOT verified

- Not tested against live Supabase — mocked dependencies only.
- Whether any `custom`-role staff account in production currently holds the
  `"staff"` module (and would therefore lose access they previously had) —
  not checked, no production DB access from this session. If one exists,
  this is the intended, correct outcome per the module's own stated design.
- Full backend test suite not run in this pass; deferred to the final
  combined verification pass across all 9 audit fixes.
