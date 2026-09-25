# Change Impact & Risk Log — one Home / one Work per user, enforced by the database

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code agent session (follow-up agreed in PR #5815) |
| Surface(s) | backend (migration + `routes/addresses.py`). No rider-app / driver-app code change. |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/saved-address-unique-home-work` |
| Related issue or gap ID | Follow-up from `docs/change-log/2026-09-25-fix-saved-addresses.md` (§4 residual race, "Follow-ups": partial unique index) |

## 1. Issue / gap identified

The one-Home / one-Work rule from PR #5815 is enforced only in application code (read the oldest Home, delete the others, then write), so two concurrent saves from the same rider can still leave two Home (or two Work) rows. The main case is two devices saving the rider's first Home at the same time: both requests insert.

## 2. Root cause

`saved_addresses` has no constraint on `(user_id, icon)`. The route's read → delete → write sequence is several PostgREST calls, not one transaction, so another request can write between them. Delete-before-write (PR #5815) guarantees the rider never ends up with zero Homes, but it cannot stop a duplicate.

## 3. Fix / remediation

1. **Migration `485_saved_addresses_unique_home_work.sql`**: a pre-check `DO` block counts users with more than one `home` row and more than one `work` row, and `RAISE EXCEPTION`s with both counts if either is non-zero. The whole file runs in one transaction, so a dirty environment fails loudly and nothing is applied. Then `CREATE UNIQUE INDEX IF NOT EXISTS uq_saved_addresses_user_home_work ON public.saved_addresses (user_id, icon) WHERE icon IN ('home','work')`. Column names were confirmed against `backend/supabase_schema.sql` (`user_id TEXT NOT NULL`, `icon TEXT DEFAULT 'location'`). The index is not built CONCURRENTLY because the table has 264 rows.
2. **Driver-app icon normalisation** (`routes/addresses.py`): for any request that is not subject to the rider-app rule (`X-App-Platform: driver`, or no header and `is_driver`), an icon of `home`/`work` is stored as `location` on POST, and on PATCH when `icon` is sent. Other icons are unchanged. Old driver builds send `icon: 'home'` for every saved address. Without this, a driver's second save from one of those builds would hit the index and fail.
3. **23505 → 409**: `repositories/_base.py` already turns a Postgres unique violation into `DuplicateRecordError`, with the redacted PostgREST text in `details["original"]`. POST (the replace update and the insert) and PATCH now catch it, log at `logger.error` with the operation, `user_id`, address id and the redacted original error (the key values are replaced by `<redacted>`, and no address text is logged), and return **409** "Your Home or Work place was just changed on another device. Refresh and try again." It is never a 500 and never silent. Without this catch, the global `SpinrException` handler would already have returned 409 "Record already exists", but only with a warning-level log and a generic message.
4. **Tests**: the in-memory interleaving fake can now emulate the index (`unique=True`: a write that would duplicate a `(user_id, home|work)` key is rolled back and raises `DuplicateRecordError`). With the index, every interleaving of two concurrent Home promotes, promote vs. POST, and two first-Home POSTs ends with **exactly one** Home, and each request either succeeds or gets 409. The no-index variants are kept (the code deploys before the migration is applied) and now include a test showing the first-save duplicate the index closes. The swap test runs with and without the index (see §4). New tests cover driver-platform normalisation (POST home/work/HOME → location; other icons unchanged; rider keeps `home`; a driver PATCH without `icon` leaves it alone) and 23505 → 409 on the real route → db_supabase → `_base.py` path (insert, replace-update, PATCH), including a check that the log line has the IDs and constraint name but not the address, coordinates or key values.

### Design choice and alternative considered (release gate 10)

- **Chosen: a partial unique index plus 409 on violation.** It is the smallest change that makes a duplicate impossible at the source, and it matches the existing pattern (`rides_one_active_per_rider`, `refresh_tokens`, insurance periods all rely on 23505 from a unique index).
- **Alternative: a Postgres RPC that does read/delete/write in one transaction.** This would also close the swap row-loss below, but it is a larger change (new SECURITY DEFINER function, route rewrite) for a path with 0 app-created rows in production. Kept as a follow-up.
- **Alternative for old driver builds: a per-platform index, or no normalisation, letting old driver builds hit 409.** Rejected. The index cannot see the request's platform, and failing every second driver save would be a regression. The driver app never had Home/Work semantics, and its current build already sends `location`.
- **Not done: retrying a 409 server-side as a replace.** The task asked for a clean, retryable 409, and a retry loop adds another place for races. The client already shows the detail and the user can save again.

## 4. Risk & impact on existing functionality

Blast radius: **single-surface (backend)**. There is no ride state machine, money, dispatch or insurance-period interaction.

Every reader and writer of `saved_addresses` (from a grep of `saved_addresses` across `backend/`, plus `/addresses` callers in the apps):
- `backend/routes/addresses.py`: changed here. The rider POST/PATCH replace logic is unchanged. It only gains the 409 mapping.
- `backend/services/saved_address_import_service.py` and `backend/routes/admin/legacy_saved_address_backfill.py` (the admin legacy CSV import): these write rows directly with `icon` `home`/`work`/`location`. The planner de-duplicates only by `(user_id, address text)`, not by type. **After the index, a re-run that would add a second Home/Work for a rider fails with 23505.** The commit handler catches it and returns 502 "Backfill commit failed". The failure is loud, not silent, but it is **not atomic across 200-row batches**: earlier batches stay committed. The import already ran (all 264 production rows, 0 rows created in the last 30 days), so this only matters for a future re-run. Follow-up below. Those files are outside this change's ownership, so they were not edited.
- `backend/routes/users.py` (account deletion `delete_many` by user_id), and the `purge_pii_retention()` SQL (`DELETE FROM saved_addresses WHERE user_id = …`): deletes only, so the index has no effect.
- `backend/ai/tools_account.py`, `backend/routes/drivers/tax_exports.py`, `backend/services/migration_status_service.py`: read-only.
- `driver-app/app/driver/addresses.tsx` renders `address.icon === 'work' ? 'briefcase' : 'home'`, so a `location` row shows the **same home glyph** a `home` row did on both old and new builds. The only visible difference: a driver-platform save with `work` would now show the home glyph instead of the briefcase. No driver build sends `work` (old builds hard-code `home`, the new build sends `location`), so in practice nothing changes. The driver app has no PATCH/edit call.
- rider-app (`utils/savedPlaceIcon.ts`): a `location` row falls back to the exact-label rule. For a **dual-role** user, a driver-app address labelled exactly "Home"/"Work" is still treated as their rider Home/Work by the rider app, as it already was for the new driver build (PR #5815). What changes: old driver builds no longer create *typed* `home` rows, so the rider app no longer treats every driver-saved address as the Home. That removes part of PR #5815's documented dual-role trade-off. The backend's rider-side replace only looks for typed rows (`icon = 'home'`), so a label-only "Home" row from the driver app is not replaced by a rider Home save. The rider app then sees two Homes (typed row and label row), and its `find` picks the first. This case already existed with the new driver build. It is not introduced here and needs a label-named driver address from a dual-role user. Production has none.
- **Behaviour change under concurrency (rider-app):** without the index, two overlapping Home saves both returned 200 and could leave a temporary duplicate. With the index, the request that loses the race gets **409**. The rider app's Saved Places screen shows the detail in its "Save Failed" toast (`getApiErrorMessage`), and the driver app does the same.
- **Residual, still not closed: the Home↔Work swap across two devices.** The index does not fix this. The row loss comes from the replace's *delete* (making A the Work deletes the old Work B before B is re-typed), and a delete never violates a unique index. The outcome is exactly as documented in PR #5815: a lost row surfaces as 409/404, never a silent 200. The updated test pins this with and without the index, and also asserts there is never a duplicate. Closing it needs the read/delete/write to run in one DB transaction (RPC follow-up).
- **Deploy order:** the code works with or without the index (both are tested). The 409 path simply never triggers before the migration is applied. Normalising driver icons takes effect as soon as the backend deploys. It must be live **before** migration 485 is applied, otherwise old driver builds' second save would 409 in the window between them.
- Rows already stored: existing `icon='home'` rows saved from old driver builds are not rewritten (production has 0 app-created rows). If one existed for a user who also has a rider Home, the migration's pre-check would abort, which is the intended loud failure.

## 5. User-experience effect

- **Rider:** no visible change in normal use. Only when two of the rider's own devices save a Home/Work at the same moment does one of them now get "Your Home or Work place was just changed on another device. Refresh and try again." instead of silently creating a duplicate. This is visible mid-session only in that race. The copy is specific and says what to do next.
- **Driver:** no visible change. Saved addresses still show the home glyph, and multiple saves keep working on old and new builds.
- **Internal admin:** a future legacy-address import re-run that would create a second Home/Work now fails with the existing 502 message instead of inserting a duplicate (see §4).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/485_saved_addresses_unique_home_work.sql` | New: duplicate pre-check (`RAISE EXCEPTION` with counts), partial unique index `uq_saved_addresses_user_home_work`, index comment, rollback comment | Enforce one Home / one Work per user in the DB |
| `backend/routes/addresses.py` | `_driver_safe_icon` (non-rider `home`/`work` → `location` on POST/PATCH); `DuplicateRecordError` on POST/PATCH → logged 409 (`_home_work_conflict`); stdlib `logger` | Old driver builds keep working under the index; a unique violation is a clean, retryable 409 |
| `backend/tests/test_saved_addresses_home_work.py` | Interleaving fake emulates the unique index; with-index concurrency tests; first-save duplicate test; swap test parametrised over the index; driver normalisation tests; 23505 → 409 tests incl. log redaction | Cover the above |
| `docs/change-log/2026-09-25-saved-address-unique-home-work.md` | This log | Release gate |

## 7. Before / after

```python
# Before — routes/addresses.py
icon=place_type or request.icon,                     # driver "home" stored as "home"
...
await db_supabase.insert_one("saved_addresses", doc)  # a 23505 escaped to the global handler
row = await db_supabase.update_one("saved_addresses", {"id": address_id, "user_id": user_id}, update)
```

```python
# After (abridged)
icon=place_type or (request.icon if singletons else _driver_safe_icon(request.icon)),  # driver home/work -> "location"
...
try:
    ...  # unchanged replace-or-insert
    await db_supabase.insert_one("saved_addresses", doc)
except DuplicateRecordError as e:
    raise _home_work_conflict(e, "create", user_id, doc["id"]) from e  # logger.error + 409
```

Concrete scenario: a rider with no Home saves "Home" from a phone and a tablet at the same time. Before: both inserts succeed, so the rider has two Homes until the next save collapses them. After (index applied): one insert succeeds, the other gets 409 "…just changed on another device. Refresh and try again.", and the rider has exactly one Home.

## 8. Rollback plan

- **Index:** `DROP INDEX IF EXISTS public.uq_saved_addresses_user_home_work;` This is SQL only, with no deploy. The code keeps working without the index (tested). Only the duplicate race returns.
- **Route changes:** code revert and backend redeploy. The data effect of the driver normalisation is that driver-app rows saved meanwhile carry `location` instead of `home`. That is harmless (same glyph in the driver app) and needs no data fix. Do not drop the normalisation while the index exists, or old driver builds' second save will 409.
- **Migration failure on apply:** the pre-check aborts before anything is created. Nothing to roll back. De-duplicate and re-run.

## 9. Verification performed

- [x] `cd backend && python -m pytest -q -p no:cacheprovider -o addopts="" tests/test_saved_addresses_home_work.py`: **52 passed**.
- [x] `cd backend && python -m pytest -q -p no:cacheprovider -o addopts="" --ignore=tests/rls --ignore=tests/direct_pool -k "address or favorite or favourite or saved"`: **258 passed, 1 skipped** (16898 deselected).
- [x] Against the pre-change route (`origin/main`'s `routes/addresses.py` put back temporarily), **19 of the 52** tests fail. The driver-normalisation tests fail because the icon stays `home`. The 23505 insert/PATCH tests fail on the missing ERROR log line and message. The concurrency tests fail too, but partly for a technical reason: the fake takes `DuplicateRecordError` from the route module, which doesn't import it before this change. `test_replace_update_losing_the_race_is_409` passes on the old code as well, because the global `SpinrException` handler already turned that into a 409 with the generic message.
- [x] `ruff check` and `ruff format --check` on the changed `.py` files: clean.
- [x] Blast-radius grep: `saved_addresses` across `backend/` (routes, services, admin, ai, SQL purge functions), `icon` rendering in `driver-app/app/driver/addresses.tsx`, `savedPlaceIcon.ts` in rider-app.
- [x] PIPEDA: the only new log line carries operation, `user_id`, address id and the `_redact_pg_error`-redacted DB error (key values `<redacted>`). A test asserts that no address, coordinate or key value is in it.
- [x] Production facts used for the migration (read-only check, 2026-09-25, by the requester): 264 rows, 138 `home`, 60 `work`, 0 users with a duplicate Home, 0 with a duplicate Work, 0 rows created in the last 30 days.
- [ ] Not feature-flagged: the index is a data-integrity constraint, and the only user-visible effect is a 409 in a race that previously produced corrupt state. Rollback is one SQL statement.

## 10. What was NOT verified

- **Migration 485 was not applied anywhere**: not production, not staging, not a local Postgres. The SQL was reviewed, not executed. The `DO` block and `CREATE UNIQUE INDEX` syntax were not run against a real database.
- Not tested against live PostgREST. The 23505 path uses a mocked `execute()` raising the PostgREST error text through the real `_base.py` conversion. The real `APIError` shape was not exercised.
- The interleaving tests cover two concurrent requests, not three or more.
- No rider-app/driver-app change, so no app build was run. The claim that the driver app shows the same glyph for `location` comes from reading `driver-app/app/driver/addresses.tsx`, not from a screenshot (neither app has visual regression tooling).
- The legacy-import re-run failure mode (§4) was reasoned from code, not run.

## Rollout

1. Deploy the backend (driver icon normalisation and 409 mapping). No behaviour change for riders until the index exists.
2. Apply migration 485 with `run_migrations.py`. If the pre-check aborts, de-duplicate first.
3. No app release needed.

## Follow-ups (out of scope)

- Home↔Work swap row-loss across two devices: move read/delete/write into one transaction (Postgres RPC).
- `saved_address_import_service.py`: skip or merge a second Home/Work per user in the planner, so a re-run can't fail half-way on the new index.
- Optional client handling: on a 409 from `/addresses`, the rider app could refetch the list automatically before showing the toast (today the rider refreshes by reopening Saved Places).

## Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
