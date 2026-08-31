# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/route-regen-preview-mode` |
| Related issue or gap ID | Found while researching the Migration Checklist panel (`docs/change-log/2026-08-31-migration-checklist-status-panel.md`) |

## 1. Issue / gap identified

Route Map Snapshots and Route Backfill were the only two tools on the admin
Bulk Operations page that wrote immediately with no preview step, breaking
that page's own stated contract ("Every tool is dry-run first: validate,
review the report, then commit").

## 2. Root cause

Both endpoints (`POST /api/admin/rides/regenerate-imported-snapshots`,
`POST /api/admin/rides/regenerate-imported-routes`) were built as
direct-execute admin actions from the start — no oversight gap discovered
later, just never given a dry-run step when every CSV-upload tool on the
same page was.

## 3. Fix / remediation

- `backend/routes/admin/rides.py`: added `preview: bool = False` to both
  `RegenerateSnapshotsRequest` and `RegenerateRoutesRequest`. When
  `preview=True`, each endpoint runs its *existing, unmodified* eligibility
  query (the same `db.get_rows(..., {"legacy_import_metadata": {"$notnull": True}})`
  filter for snapshots, and the same `_needs_route()` filter for routes) and
  returns `{"total": N, "preview": True, "message": "..."}` before any
  render/upload/write work begins. The commit path itself is untouched.
- `admin-dashboard/src/lib/api/imports.ts`: `adminRegenerateImportedSnapshots`/
  `adminRegenerateImportedRoutes` gained a third `preview` parameter; both
  result types made their write-only fields (`success`/`failed`/`renderer`/
  `errors`) optional and added `preview?: boolean`.
- `admin-dashboard/.../bulk-operations/page.tsx`: both
  `SnapshotRegenerateSection`/`RouteRegenerateSection` gained a "Preview"
  button. "Regenerate" is disabled until a Preview has run for the current
  `force` setting (toggling `force` clears the stale preview result) — the
  same "must dry-run before you can commit" discipline every CSV-upload tool
  on this page already enforces.

## 4. Risk & impact on existing functionality

- **Blast radius, checked directly**: `RegenerateSnapshotsRequest`/
  `RegenerateRoutesRequest` and the two route handlers have no other
  callers — grepped the whole repo. The existing non-preview commit path
  (the actual render/upload/write loop) is completely unmodified; the new
  `if body.preview: return {...}` short-circuit sits before it and cannot
  be reached once execution continues past it.
- **No existing admin-dashboard caller breaks**: `preview` defaults to
  `false` on both the Pydantic request models and the new TS function
  parameter, so every existing call site (none exist yet outside this
  page) is unaffected; the two call sites this PR itself touches
  (`handleRegenerate` in both sections) explicitly omit the new third
  argument, preserving their exact prior behavior.
- **No data change of any kind** — this PR adds a read-only branch to two
  already-existing write endpoints; it does not change what either endpoint
  writes when `preview` is omitted or `false`.

## 5. User-experience effect

Admin-facing only (Bulk Operations page, super_admin only). Both sections
now require a Preview click before Regenerate is enabled — a real behavior
change for an admin currently using these two tools, but one that makes
them consistent with every other tool on the same page rather than an
unannounced new restriction; the page's own header text has always claimed
this was already true for every tool here.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/rides.py` | `preview` field on both request models; preview short-circuit in both handlers | Add the missing dry-run step |
| `backend/tests/test_admin_rides_coverage.py` | 2 new tests (preview mode makes no writes, for each endpoint) | Lock in the no-write guarantee |
| `admin-dashboard/src/lib/api/imports.ts` | `preview` param + optional result fields on both client functions | Client for the new param |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | Preview button + gated Regenerate button in both sections | Surface the dry-run step |

## 7. Before / after

```python
# Before (routes/admin/rides.py, regenerate-imported-snapshots)
if not rides:
    return {"total": 0, "success": 0, "failed": 0, "message": "No rides to process"}

loop = asyncio.get_event_loop()
# ... immediately renders/uploads/writes every matching ride
```

```python
# After
if not rides:
    return {"total": 0, "success": 0, "failed": 0, "message": "No rides to process"}

if body.preview:
    return {
        "total": len(rides),
        "preview": True,
        "message": f"Would attempt to regenerate {len(rides)} snapshot(s). No writes made.",
    }

loop = asyncio.get_event_loop()
# ... unchanged from here
```

## 8. Rollback plan

`git revert` — purely additive request fields and UI controls; the existing
non-preview path is byte-identical to before this change. No data or schema
change.

## 9. Verification performed

- [x] `pytest tests/test_admin_rides_coverage.py` — 105 passed, 0 regressions (includes the 2 new preview tests, asserting `update_one`/`log_admin_action` are never awaited when `preview=True`).
- [x] `ruff check` / `ruff format --check` on both touched Python files — clean.
- [x] `npx tsc --noEmit` — clean (only the 3 pre-existing, unrelated Storybook module-resolution errors remain, confirmed via a clean-baseline check with this change's files stashed).
- [x] `npm run build` (admin-dashboard) — real production build; Turbopack compile succeeds, same 3 pre-existing Storybook errors are the only TypeScript-step failures.
- [x] Blast-radius grep: both request models and route handlers have no other callers.

## What was NOT verified

- The live admin-dashboard UI was not screenshotted (no browser access in
  this session). No visual regression tooling exists for admin-dashboard.
- The actual preview/regenerate flow was not exercised against real
  production Supabase — verified via unit tests with mocked `db_supabase`
  calls only, matching this repo's standard test-tier convention for
  admin routes.
