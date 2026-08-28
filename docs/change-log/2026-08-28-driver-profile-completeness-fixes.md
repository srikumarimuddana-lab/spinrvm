# Change Impact & Risk Log — driver profile completeness fixes

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | srikumarimuddana-lab (review fixes on PR #4645) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | drivers, admin |
| PR / commit link | Fixes for https://github.com/srikumarimuddana-lab/spinrvm/pull/4645 |
| Related issue or gap ID | Review findings 1–7 on PR #4645 |

## 1. Issue / gap identified

The driver profile-completeness feature introduced in PR #4645 could not report a
complete profile. It scored `vehicle_plate`, which is not a column on `drivers`
(the column is `license_plate`), so every driver was pinned at 90% regardless of
their actual data. Alongside it: the new API endpoint was registered outside the
`/drivers` namespace it was documented at, and the new "Profile" table column
advertised a sort the backend silently ignored.

Found by code review, not by CI or by the test suite — see §2.

## 2. Root cause

**Primary (the 90% cap).** `vehicle_plate` is the CSV/import-side *alias* that
`services/driver_import_service.py` maps onto the real column
(`:359` `("vehicle_plate", "license_plate")`, `:726`
`"license_plate": row.get("vehicle_plate", "")`). The scoring module took the
alias for the column.

**Why the 9 tests were green.** `_full_driver()` in
`tests/test_profile_completeness.py` used the same wrong key and then asserted
`score == 100`. The fixture agreed with the code but not with the schema, so the
suite was self-consistent and proved nothing about production. This is the real
root cause — the field-name typo would have been caught on day one by a fixture
built from actual column names.

**Secondary (name scoring).** `_USER_FIELDS` included `"name"`, but `users` has
no `name` column, so that lookup always missed and fell through to
`drivers.name` — the pre-migration-63 atom kept only for rollback. That column
can hold the `"Driver"` placeholder `admin_get_drivers` deliberately drops, and
`routes/drivers/profile.py`'s auto-create path writes the driver's **phone
number** into it when the account has no name. Both scored as a complete
"Full Name".

**Secondary (route path).** `router = APIRouter()` in `routes/admin/drivers.py`
carries no prefix; it mounts on `admin_router` (`prefix="/admin"`), itself
mounted at `/api`. A bare `@router.get("/completeness")` therefore resolved to
`/api/admin/completeness`, not the documented `/admin/drivers/completeness`.

**Secondary (sort).** `admin-dashboard`'s drivers table sorts at the DB
(`sort_by` → `_DRIVER_SORT_COLUMNS`); `const sorted = drivers` does no
client-side sorting. `profile_completeness_score` is not in that allowlist and
is not a column, so the key fell through to the `created_at` default while
`SortIcon` flipped and `aria-sort` announced a state the table was not in.

## 3. Fix / remediation

1. Score `license_plate`; rebuild the test fixture from real column names and add
   a schema-agreement test that pins the scored keys and fails on the alias.
2. Compose "Full Name" from `first_name`/`last_name` (account row first, driver
   mirror second, `"Driver"` placeholder rejected) instead of the legacy atom.
3. Move the endpoint to `/drivers/{driver_id}/completeness`, `driver_id` as a
   path param.
4. Run the bulk enrichment on the composed response rows rather than the raw
   `drivers` rows, so the score reflects the identity the row renders.
5. Compute `stats.incomplete_profiles` server-side over the full queue result set
   instead of counting the trimmed `items` page client-side.
6. Render "Profile" as a plain, non-sortable header.
7. Refetch after an in-panel save so the score reflects the field the admin just
   filled in; drop a no-op `.replace(/_/g,' ')` over strings that are already
   display labels.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to the admin drivers surface.** Grepped, and naming
every consumer rather than asserting "looks fine":

- `compute_profile_completeness` has exactly three call sites, all in
  `backend/routes/admin/drivers.py`: `_enrich_with_completeness` (line 81), the
  approval-queue item builder (line 1123), and the single-driver endpoint
  (line 4044). No other module imports it.
- `admin_get_drivers` has one internal caller, `admin_search_drivers`
  (`POST /drivers/search`, line 629), so the driver typeahead response carries
  the same enriched fields. Values change; shape does not.
- Frontend consumers of `profile_completeness_score` /
  `profile_missing_count` / `profile_missing_fields` / `incomplete_profiles`:
  `admin-dashboard/src/app/dashboard/drivers/page.tsx`,
  `.../drivers/queue/page.tsx`, `.../lib/api/driver-queue.ts`. None in
  rider-app, driver-app, or shared.

**No DB writes, no migration, no schema change.** The score is computed
per-request and never persisted, so there is no stored value to backfill or
correct.

**Not touched:** ride state machine, dispatch, wallet/allowance deltas, Stripe,
insurance-period rows, and all 40 background loops in `core/lifespan.py`. No
money arithmetic (pre-commit money check clean).

**What could regress.** `admin_get_drivers` is the drivers-list workhorse; the
enrichment call moved from before the response-composition loop to after it.
The loop itself is untouched and the enrichment only *adds* three keys via
`d[...] = ...` on the already-composed dicts, so the existing response shape is
unchanged — but this is the highest-traffic admin endpoint in the diff and is
where a regression would land. Covered by 177 existing admin-driver tests, all
passing.

**One removed route.** `/api/admin/completeness` no longer exists. It shipped
only in the unmerged PR #4645 and had zero callers (grepped across all four
surfaces), so nothing external breaks.

## 5. User-experience effect

**Internal admin only.** No rider, driver, or corporate-admin surface changes.
Nothing is visible mid-session to a rider mid-ride or a driver online.

Admin staff see three differences:

- The Profile badge now reports a true score. A driver who was permanently amber
  `Incomplete (90%)` will show green `Complete` if their data is in fact
  complete. **This changes what an existing screen says**, so ops should be told
  once: 90% was previously a floor, not a signal.
- The Profile column header is no longer clickable. It never sorted; the
  affordance was removed rather than the (broken) behaviour kept.
- After editing a driver, the completeness panel updates instead of showing the
  pre-edit score.

No new copy, no notifications, no i18n strings.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/profile_completeness.py` | `vehicle_plate` → `license_plate`; `full_name` resolved from first/last via `_FIELD_RESOLVERS`; `name` dropped from `_USER_FIELDS` | Score real columns; stop trusting the legacy name atom |
| `backend/tests/test_profile_completeness.py` | Fixture rebuilt on real column names; added schema-agreement, license-plate, name-resolution, and placeholder tests (9 → 16) | The old fixture encoded the same bug it was meant to catch |
| `backend/routes/admin/drivers.py` | Endpoint → `/drivers/{driver_id}/completeness`; enrichment moved after the composition loop; `stats.incomplete_profiles` added | Correct namespace; score what renders; page-independent count |
| `backend/tests/test_admin_approval_queue.py` | Two tests: full-set stat count under a page trim, and 100% reachable at the route layer | Route-level guard for the schema bug and the count-scope bug |
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | Profile header made non-sortable; no-op `.replace()` dropped; `loadDrivers()` returns the page; save handler re-syncs the sheet | Remove a false affordance and a stale-score read |
| `admin-dashboard/src/app/dashboard/drivers/queue/page.tsx` | Tab count reads `stats.incomplete_profiles` | Match the other three tabs' full-set semantics |
| `admin-dashboard/src/lib/api/driver-queue.ts` | `incomplete_profiles` added to the stats type | Type the new field |

## 7. Before / after

**Scoring — the 90% cap:**

```python
# Before — `vehicle_plate` is the import alias, not the column
("vehicle_plate", "License Plate", "vehicle"),
# a driver with every real field populated:
#   score 90, missing_required ['License Plate']

# After
("license_plate", "License Plate", "vehicle"),
#   score 100, missing_required []
```

**Name resolution:**

```python
# Before — users has no `name` column, so this always fell through to
# drivers.name, which may hold "Driver" or the driver's phone number
_USER_FIELDS = {"name", "phone", "email"}

# After — composed from first/last, account row wins, placeholder rejected
_USER_FIELDS = {"phone", "email"}
_FIELD_RESOLVERS = {"full_name": _resolve_full_name}
```

**Route path:**

```python
# Before -> /api/admin/completeness
@router.get("/completeness")
async def get_driver_completeness(driver_id: str = Query(...), ...):

# After -> /api/admin/drivers/{driver_id}/completeness
@router.get("/drivers/{driver_id}/completeness")
async def get_driver_completeness(driver_id: str, ...):
```

**Sort affordance:**

```tsx
// Before — sends a key the backend ignores; aria-sort announces a false state
<TableHead onClick={() => handleSort("profile_completeness_score")}
           aria-sort={...}>Profile<SortIcon col="profile_completeness_score" /></TableHead>

// After
<TableHead className="h-11"><span ...>Profile</span></TableHead>
```

## 8. Rollback plan

`git revert` is a complete rollback here, and this is one of the cases the policy
allows it for: **nothing in this change writes to the database.** The score is
computed per-request from rows the endpoint already fetched, there is no new
column, no migration, no cached or persisted value, and no money/ride/insurance
state is touched. Reverting restores the previous (incorrect) 90% ceiling
immediately on the next deploy, with no data-level remediation.

No feature flag: the change removes a broken affordance and corrects a displayed
number on an internal admin screen. Flagging a correction would mean keeping a
known-wrong score reachable in production.

## 9. Verification performed

- [x] **Automated tests (unit + route-level, all mocked).**
  `pytest tests/test_profile_completeness.py` — 16 passed (was 9).
  `pytest tests/test_admin_approval_queue.py` — 10 passed (was 8).
  `pytest tests/test_admin_drivers_coverage.py tests/test_admin_driver_search.py tests/test_admin_drivers_expiring.py tests/test_profile_completeness.py` — 177 passed.
- [x] **Real production build**, not just a typecheck: `npm run build` in
  `admin-dashboard` exited 0. `npx tsc --noEmit` also clean. `eslint` on the
  three touched files: 0 errors, warnings 47 → 46.
- [x] **Lint:** `ruff check` and `ruff format --check` clean on both new/changed
  backend files. (The 4 `B904`s in `routes/admin/drivers.py` are pre-existing on
  `main` — verified by running ruff against `main`'s copy of the file in place.)
- [x] **Route registration asserted, not assumed:** imported the drivers
  sub-router and confirmed the only completeness path is
  `/drivers/{driver_id}/completeness`, and that no bare `/completeness` remains.
- [x] **Blast-radius grep performed.** Searched for `compute_profile_completeness`,
  `_enrich_with_completeness`, `admin_get_drivers`, and each of
  `profile_completeness_score` / `profile_missing_count` /
  `profile_missing_fields` / `incomplete_profiles` across `backend/`,
  `admin-dashboard/src`, `rider-app`, `driver-app`, and `shared`. Consumers named
  in §4.
- [x] **Reviewed against `CLAUDE.md` conventions:** dual-import pattern preserved;
  no float money arithmetic (N/A — no money path); no PII added to logs or to the
  API response (labels and a score only, never field values); no state-machine or
  RLS surface touched.
- [x] **Feature flag:** not used, justified in §8.

## 10. What was NOT verified

- **Column names were established from application code, not from a live
  database.** The evidence for `license_plate` is `admin_update_driver`'s write
  allowlist, `driver_import_service.py`'s PostgREST projection (`:516`) and its
  alias map (`:359`, `:726`), and `guest_notification_service.py:206`. No
  `information_schema` query was run against production or staging. The whole
  fix rests on this — **confirm against the live `drivers` table before merge**,
  and while there, confirm `stripe_account_id`, `vehicle_color`, and
  `vehicle_vin` too.
- **Nothing was run against live Supabase.** Every test mocks `get_rows`; no
  integration or staging pass was performed.
- **No visual verification.** Per `CLAUDE.md` release gate 6 and
  `ACTION_ITEMS.md` B38, `admin-dashboard`'s Playwright visual-regression job has
  zero committed baselines and skips itself on every run, so there is no active
  visual coverage on this surface. The header and badge changes were reasoned
  about and build-verified, not screenshotted. Standing gap, not a new one.
- **The `< 100` threshold for the "Incomplete profiles" queue tab was left as
  the PR author set it.** It is literally correct for the tab's label, but the
  approval queue holds pending applicants who by definition have no
  `stripe_account_id` yet, so the tab will still match most rows. Whether the
  useful cut is `< 100` or `< 70` is a product call, deliberately not made here.
- **The `/drivers/{driver_id}/completeness` endpoint still has no caller.** Its
  path is now correct, but nothing in `admin-dashboard` fetches it; the detail
  sheet reads the enriched list row. Wiring it up or removing it is the author's
  call.
- **PR #4645's Phase 3 (daily Telegram report) is untouched and still absent
  from the branch.** `grep` for `daily-driver-profile-report` /
  `daily_driver_profile` returns nothing repo-wide. Not in scope for these fixes.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field
      filled in — §5 records that the Profile badge's meaning changes for admin
      staff and that the column header stops being clickable
