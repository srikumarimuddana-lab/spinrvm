# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-29 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Follow-up to the 08-22 legacy migration batch — admin asked how to identify migrated drivers/riders in the UI |

## 1. Issue / gap identified

Drivers and Users already show an inline "Imported" badge next to legacy-imported
records (shipped 2026-08-19), but there was no way to *filter* either list down to
just imported or just non-imported records — an admin had to scan the page or run
SQL directly.

## 2. Root cause

Not a bug — a missing feature. The badge was additive-only when it shipped; no
query param or dropdown was ever added alongside it.

## 3. Fix / remediation

- **Backend**: added an optional `legacy_import: bool` query param to
  `GET /api/admin/drivers` and `GET /api/admin/users`. `true` filters to
  legacy-imported records only, `false` to non-imported only, omitted applies no
  filter (unchanged default behavior). `legacy_import_metadata` is
  `JSONB NOT NULL DEFAULT '{}'::jsonb` on both tables — "not imported" is the
  default-value row, not a NULL one — so the filter compiles to
  `{"$ne": {}}` / `{"$eq": {}}` against the repository filter layer, the same
  `$eq`-against-`{}` shape `utils/legacy_rides.py`'s `EXCLUDE_LEGACY_RIDES`
  already uses and has test coverage proving the `{col: None}` trap it avoids.
- **Frontend**: added a "Legacy Import" dropdown (All / Imported only / Not
  imported) to both the Drivers and Users list pages, next to the existing
  vehicle-type/role filters, wired through `getDrivers()`/`getUsersPaginated()`.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** New optional query param on two existing list
  endpoints — omitted (the default for every existing caller, including the two
  "Legacy Driver Import"/backfill tools that don't pass it) behaves identically
  to before. Grepped both `admin_get_drivers` and `admin_get_users` — no other
  backend caller invokes either function directly; both are pure HTTP routes.
- The new filter key does not collide with the existing `$or`-based `search` /
  `missing_license` filters on the drivers endpoint — it's applied as a separate
  top-level filter key, ANDed at the query layer like every other filter already
  on that route.
- 6 new backend tests (3 per endpoint: omitted/true/false) assert the exact
  filter dict shape reaching `db_supabase.get_rows`, not just a 200 status.
- `npx tsc --noEmit` clean; `npm run build` succeeded — `/dashboard/drivers` and
  `/dashboard/users` both compiled.

## 5. User-experience effect

- **Internal admin only.** Purely additive — a new dropdown next to filters that
  already exist on both pages. No existing filter's behavior changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/drivers.py` | New `legacy_import: Optional[bool]` query param on `admin_get_drivers`, filter compiled to `$eq`/`$ne` against `{}` | Server-side filter for the new dropdown |
| `backend/routes/admin/users.py` | Same, on `admin_get_users` | Same |
| `backend/tests/test_admin_drivers_coverage.py` | New `TestAdminGetDriversLegacyImportFilter` (3 tests) | Lock in filter-dict shape |
| `backend/tests/test_admin_users_management.py` | New `TestListUsersLegacyImportFilter` (3 tests) | Same |
| `admin-dashboard/src/lib/api/drivers.ts` | `getDrivers()` gains `legacy_import?: boolean` | Client param plumbing |
| `admin-dashboard/src/lib/api/users-wallet.ts` | `getUsersPaginated()` gains `legacy_import?: boolean` | Same |
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | New `legacyFilter` state + dropdown UI, wired into `loadDrivers()` | Filter dropdown |
| `admin-dashboard/src/app/dashboard/users/page.tsx` | New `legacyFilter` state + dropdown UI, wired into `fetchUsers()` | Filter dropdown |

## 7. Before / after

```python
# Before — no way to filter by legacy-import status
filters = {}
if status: filters["status"] = status
...
drivers = await db_supabase.get_rows("drivers", filters, ...)
```

```python
# After
if legacy_import is not None:
    filters["legacy_import_metadata"] = {"$ne": {}} if legacy_import else {"$eq": {}}
```

## 8. Rollback plan

`git-revert-safe` — new optional query param + new UI dropdown, no schema
change, no change to any existing default behavior.

## 9. Verification performed

- [x] `pytest tests/test_admin_drivers_coverage.py tests/test_admin_users_management.py` — 163 passed (6 new).
- [x] `ruff check` / `ruff format --check` on both backend files — clean (4
      pre-existing, unrelated B904 findings in `drivers.py` confirmed via
      `git stash` diff to predate this change).
- [x] `npx tsc --noEmit` — clean.
- [x] `npm run build` (admin-dashboard) — real production build, succeeded.
- [x] Blast-radius grep: no other backend caller of either list function.

## What was NOT verified

- Not exercised against real production/Supabase (no live DB access in this
  environment) — verified via unit tests asserting the exact filter dict shape
  passed to the repository layer instead.
- No visual-regression tooling exists for admin-dashboard (`ACTION_ITEMS.md`
  B38) — the two new dropdowns were reasoned about against the existing
  vehicle-type/role dropdowns' rendered structure, not screenshotted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed (isolated new query param, no other callers)
- [x] No silent behavior change — the filter is opt-in via a new param; every
      existing caller that omits it gets identical behavior to before
