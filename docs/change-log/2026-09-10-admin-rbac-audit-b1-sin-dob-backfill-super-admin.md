# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (admin portal security/RBAC audit, requested by user) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| Related issue or gap ID | `docs/audit/2026-09-10-admin-portal-security-rbac-audit.md`, finding B1 |

## 1. Issue / gap identified

`POST /api/admin/legacy-drivers/sin-dob-backfill/commit` writes a real,
vault-encrypted SIN + date of birth onto driver rows, but was gated only by
`require_module("drivers")` — any staff account holding the ordinary
`"drivers"` module grant (e.g. the `operations` preset, not super_admin)
could commit government IDs onto driver records.

## 2. Root cause

The router was mounted with the same `"drivers"` module gate as its sibling
legacy-import routers (bulk driver import, vehicle-history backfill) because
it's part of the same 2026-08-27 migration-plan Phase 2 batch — but unlike
those siblings, it writes a government ID (SIN), which every *other*
SIN-writing endpoint in this codebase (`drivers.py`'s `reveal-sin`/
`update-sin`, `tax_id_import.py`) correctly requires `super_admin` for. This
one router was never given that stricter posture when it was added.

## 3. Fix / remediation

- `backend/routes/admin/__init__.py`: `legacy_sin_dob_backfill_router` mount
  changed from `dependencies=[Depends(require_module("drivers"))]` to
  `dependencies=[Depends(require_super_admin)]`.
- `backend/routes/admin/legacy_sin_dob_backfill.py`: added a `_require_super_admin(admin)`
  helper (identical pattern to `booking_import.py`/`stripe_import.py`) and
  called it at the top of both `validate_legacy_sin_dob_backfill` and
  `commit_legacy_sin_dob_backfill` — defense-in-depth so the guard survives
  a future re-mount under a weaker dependency, matching this repo's
  established convention for every other super_admin-class router.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one router.** Grepped
  `backend/routes/admin/__init__.py` and `legacy_sin_dob_backfill.py` for
  every reference — only the one mount line and the two handlers call into
  this router; no other module imports or calls its functions directly.
- **What could regress:** any admin workflow that relied on a non-super_admin
  `"drivers"`-grant account running this backfill. Checked `ROLE_PRESETS`
  in `staff.py` — no preset is named specifically for this one-time
  migration tool; it's a Phase-2 migration utility, not a day-to-day admin
  action, so restricting it to super_admin (already true for its sibling
  `tax_id_import.py`) is consistent with how the codebase treats this class
  of tool.
- No schema change, no data migration. Existing SIN/DOB values already
  written are untouched.

## 5. User-experience effect

- **Internal admin only, not rider/driver/corporate-admin facing.** A
  non-super_admin who previously could call this endpoint (if they held the
  `"drivers"` module) now gets a 403. No UI currently surfaces this
  endpoint outside the super_admin-only bulk-operations tooling, so no
  visible admin-dashboard change expected, but not independently confirmed
  against the live UI (see "What was NOT verified").

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/__init__.py` | Mount dependency for `legacy_sin_dob_backfill_router` changed from `require_module("drivers")` to `require_super_admin`; comment updated | Close the module-grant gap on a SIN/DOB-writing endpoint |
| `backend/routes/admin/legacy_sin_dob_backfill.py` | Added `_require_super_admin()` helper; called at the top of both handlers | Defense-in-depth, matching every other SIN-writing route in this package |
| `backend/tests/test_admin_legacy_sin_dob_backfill.py` | Added `regular_admin_override` fixture + 2 new tests asserting a `"drivers"`-module-only admin gets 403 on both `/validate` and `/commit`; corrected a stale fixture docstring | Pin the fixed behavior; the old docstring described the pre-fix `require_module("drivers")` gate |

## 7. Before / after

```python
# Before (backend/routes/admin/__init__.py)
admin_router.include_router(legacy_sin_dob_backfill_router, dependencies=[Depends(require_module("drivers"))])

# After
admin_router.include_router(legacy_sin_dob_backfill_router, dependencies=[Depends(require_super_admin)])
```

```python
# Before (backend/routes/admin/legacy_sin_dob_backfill.py) — no role check at all
async def commit_legacy_sin_dob_backfill(..., admin: dict = Depends(get_admin_user)):
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    ...

# After
async def commit_legacy_sin_dob_backfill(..., admin: dict = Depends(get_admin_user)):
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    ...
```

## 8. Rollback plan

`git revert`-safe. This is a pure authorization tightening — no data was
written or migrated by this change itself. Reverting restores the
`require_module("drivers")` gate (i.e. re-opens the gap); no data-level
remediation needed since nothing this commit wrote needs undoing.

## 9. Verification performed

- [x] Automated tests run (unit): `pytest tests/test_admin_legacy_sin_dob_backfill.py -q`
  — 11 passed (9 pre-existing + 2 new), 0 failed. Confirmed via a fresh
  `backend/venv` (`python3 -m venv venv && pip install -r requirements.txt`)
  since no venv existed in this session.
- [x] `ruff check` + `ruff format --check` on all 3 touched files — clean.
- [x] Blast-radius grep performed on the mount and the router file (see §4).
- [x] Reviewed against relevant CLAUDE.md convention: matches
  `tax_id_import.py`'s stated "same posture as reveal-sin/update-sin"
  pattern exactly (mount-level `require_super_admin` + per-handler
  `_require_super_admin` redundant check).
- [ ] Manual repro against a running admin-dashboard — not performed, no
  staging environment available to this session.
- [ ] Real production build (`npm run build`) — not applicable; this is a
  backend-only change, no admin-dashboard files touched.

## What was NOT verified

- Not tested against live Supabase — `mock_supabase_client`/in-memory fake
  store only, per this repo's unit-test convention.
- No confirmation that no admin-dashboard UI currently links to this
  endpoint for a non-super_admin user (would 403 harmlessly if so, but not
  independently checked against the frontend).
- The full backend test suite was not run in this pass (single-file run
  only) — full-suite confirmation is deferred to the final combined
  verification pass across all 9 audit fixes in this batch.
