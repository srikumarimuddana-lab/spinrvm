# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (admin portal security/RBAC audit, requested by user) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin, payments |
| Related issue or gap ID | `docs/audit/2026-09-10-admin-portal-security-rbac-audit.md`, finding B2 |

## 1. Issue / gap identified

`backend/features.py`'s `pricing_router` exposes `/api/v1/areas/{id}/fees`
(POST/PUT/DELETE), `/api/v1/areas/{id}/tax` (PUT), and
`/api/v1/drivers/{id}/area` (PUT) gated only by "holds any valid admin JWT"
— no module check, and 4 of the 5 handlers wrote no audit-log entry at all.
Any admin account, including one with no `service_areas`/`drivers` module
grant, could mutate live booking fees (changes every subsequent rider fare
in that service area) or reassign a driver's dispatch pool through this
path, untracked.

## 2. Root cause

These are dead-from-the-frontend duplicates of properly-gated, audited
endpoints that already exist: `routes/admin/service_areas.py` (fee CRUD +
tax, gated `require_module("service_areas")`) and `routes/admin/drivers.py`
(`admin_assign_driver_area`, gated `require_module("drivers")`). Confirmed
zero live callers repo-wide (grepped `admin-dashboard/src` for the `/api/v1/
areas/.../fees`, `/api/v1/areas/.../tax`, `/api/v1/drivers/.../area` path
shapes — none found; `admin-dashboard/src/lib/api/pricing.ts` calls only the
`/api/admin/...` twins). There is repo precedent for exactly this situation:
`docs/change-log/2026-08-12-a29-tax-config-audit-justification.md` already
found `update_area_tax` on this same router unreachable and deliberately
chose to harden it in place rather than delete it ("in case it gets wired up
later"). That prior fix closed the audit-log gap on `/tax` only — the other
4 routes (fee CRUD + driver-area assign) were never touched and still had
neither a module gate nor an audit log.

## 3. Fix / remediation

Following the same precedent (harden, don't delete), added to
`backend/features.py`:
- `create_area_fee`, `update_area_fee`, `delete_area_fee`, `update_area_tax`:
  added `dependencies=[Depends(require_module("service_areas"))]` to the
  route decorator, matching `service_areas.py`'s gate on the same tables.
- `assign_driver_area`: added `dependencies=[Depends(require_module("drivers"))]`,
  matching `drivers.py`'s gate.
- Added `log_admin_action` calls to all 4 previously-unaudited handlers,
  reusing the exact action names (`area_fee_created`/`area_fee_updated`/
  `area_fee_deleted`/`driver_area_assigned`) their UI-wired twins already
  use, so both paths produce a consistent audit trail.
- Added `require_module` to the module import (both branches of the
  dual-import pattern).

## 4. Risk & impact on existing functionality

- **Blast radius:** isolated to `pricing_router` in `backend/features.py`.
  Grepped every `.tsx`/`.ts` file in `admin-dashboard/src` and the whole
  repo for these 3 path shapes before changing anything — confirmed no
  live caller exists anywhere (frontend, scripts, tests, docs referencing
  an actual runtime call). The properly-gated twins
  (`routes/admin/service_areas.py`, `routes/admin/drivers.py`) are
  untouched by this change.
- **What could regress:** nothing observable — these routes had zero
  traffic before this change (confirmed above) and still have zero traffic
  after; the only behavior change is that a request to this specific
  `/api/v1/...` path shape now requires the right module grant and produces
  an audit-log row, matching what the equivalent `/api/admin/...` request
  already required.
- No schema change, no data migration.

## 5. User-experience effect

None — internal admin-only surface, and unreachable from any current UI.
No rider/driver/corporate-admin-facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/features.py` | Added `require_module` import; added `require_module("service_areas")`/`require_module("drivers")` route-level dependencies to 5 pricing_router routes; added `log_admin_action` to 4 previously-unaudited handlers | Close the unscoped-mutation + missing-audit-trail gap (finding B2) |
| `backend/tests/test_features.py` | New `TestPricingRouterModuleGating` class (8 tests, via real HTTP `TestClient` requests, not direct function calls, since `dependencies=` only runs on the actual FastAPI request path): module-check-enforced (403) for each mutation, success + correct audit action name with the right module, super_admin bypass | Prove the route-level dependency actually wires up — a direct-call test (this file's usual style) would silently pass regardless of the fix |

## 7. Before / after

```python
# Before
@pricing_router.post("/areas/{area_id}/fees")
async def create_area_fee(area_id: str, req: CreateAreaFeeRequest):
    ...
    await db_supabase.insert_one("area_fees", fee)
    return fee

# After
@pricing_router.post("/areas/{area_id}/fees", dependencies=[Depends(require_module("service_areas"))])
async def create_area_fee(area_id: str, req: CreateAreaFeeRequest, admin: dict = Depends(get_admin_user)):
    ...
    await db_supabase.insert_one("area_fees", fee)
    await log_admin_action(admin, "area_fee_created", "area_fees", fee["id"], {"service_area_id": area_id, "fee_name": fee["fee_name"]})
    return fee
```
(Same shape applied to `update_area_fee`, `delete_area_fee`, `update_area_tax`, `assign_driver_area`.)

## 8. Rollback plan

`git revert`-safe. Pure authz/audit-wiring addition — no data written or
migrated by this change itself. Reverting restores the unscoped/unaudited
routes (re-opens the gap, does not break anything since nothing calls them).

## 9. Verification performed

- [x] Automated tests run (unit): `pytest tests/test_features.py -q` —
  43 passed, 0 failed (8 new tests added for this fix). Run via the
  session's fresh `backend/venv`.
- [x] `ruff check` + `ruff format` clean on both touched files.
- [x] Blast-radius grep performed repo-wide for the 3 affected path shapes
  before deciding delete-vs-harden (see §2).
- [x] Reviewed against relevant CLAUDE.md convention and existing repo
  precedent (`2026-08-12-a29-tax-config-audit-justification.md`) — same
  "harden a dead-but-not-provably-permanently-dead endpoint" decision this
  codebase already made once for the neighboring `/tax` route on this same
  router.
- [ ] Manual repro against a running admin-dashboard — not performed, no
  staging environment available to this session (these routes have no UI
  to click through regardless).
- [ ] Real production build (`npm run build`) — not applicable; backend-only
  change.

## What was NOT verified

- Not tested against live Supabase — mocked `db_supabase` calls only.
- "Zero live callers" is a repo-wide grep result, not a runtime traffic
  analysis (e.g., no access-log/APM check for actual production hits on
  these paths) — if some external tool or script outside this repo calls
  these URLs directly, it would now need the `service_areas`/`drivers`
  module grant it previously didn't need. Given the prior A29 investigation
  reached the same "zero callers" conclusion via the same method and this
  audit's own file comment for `/tax` says the same, this is treated as
  reliable but is worth flagging as the fix's boundary.
- Full backend test suite not run in this pass (single-file run); deferred
  to the final combined verification pass across all 9 audit fixes.
