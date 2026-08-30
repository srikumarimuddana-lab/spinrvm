# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Operator-reported: migrated riders/drivers all show today's date as their "Joined" date on the admin dashboard |

## 1. Issue / gap identified

The 214 riders imported via `rider_import_service.py` earlier today, and the 692 driver rows created by the orphaned-account repair tool, all show today's date (the import/repair date) as their `created_at` on the admin Users/Drivers "Joined" column — instead of their real legacy signup date.

## 2. Root cause

- **Riders**: `rider_import_service.py`'s `build_plan()` hardcoded `created_at: datetime.now(timezone.utc).isoformat()` for every new user row, ignoring the legacy Mongo `customers.csv`'s own `created_at` column (epoch-ms), which the driver importer already correctly parses elsewhere.
- **Backfilled driver rows**: `backfill_orphaned_legacy_driver_rows()` (2026-08-29) correctly stamps the new `drivers` row's `created_at` as the repair's own run time — the driver PROFILE row genuinely didn't exist until then — but that's the wrong value for a "Joined" display. The driver's real join date is already sitting correctly on their linked `users.created_at` (set when the original `mongo_driver_history` link attempt ran, before the atomicity bug orphaned the driver row).

**Confirmed scope via direct production SQL before writing any code** — this is a precisely-bounded, one-time-affected issue, not a systemic one:
- 214 riders (today's import) + 692 backfilled driver rows: affected.
- 918 pre-existing riders (an earlier batch): **not** affected — confirmed via `docs/change-log/2026-08-17-rider-provenance-backfill-executed.md` that batch only ever stamped `legacy_import_metadata`, never touched `created_at`.
- 187 rows from the original Saskatoon driver CSV tool: **not a bug** — that CSV format has no historical date column at all; the tool is an ongoing "add today's real applicant" tool, not a backdated migration, so `now()` is correct there.
- The primary ~925-row Mongo driver import: **not affected** — already correctly parses the legacy epoch timestamp.
- Blast radius of `created_at` itself: confirmed via grep it feeds only the admin-dashboard "Joined" column/sort/filter on Users and Drivers pages — not surfaced on rider-app or driver-app (no "member since" UI exists there), and not read by any revenue/growth/retention KPI SQL function (`admin_analytics_overview` and the retention-purge migrations all bucket by `rides.created_at`, not `users.created_at`).

## 3. Fix / remediation

- **Code fix**: `rider_import_service.py`'s `build_plan()` now parses the CSV's own `created_at` (epoch-ms, via a new `_parse_legacy_epoch_ms` helper duplicated from `driver_import_service.py`'s, matching this file's own duplication convention) and falls back to import time only when the row has none — same graceful-fallback pattern the driver importer already uses.
- **One-time repair, riders**: new `find_rider_created_at_corrections()` / `apply_rider_created_at_corrections()` + `POST /api/admin/riders/created-at-backfill` — re-uploads the same CSV, matches by phone, and corrects any `users.created_at` mismatch. `apply=false` (default) only reports.
- **One-time repair, backfilled drivers**: new `find_backfilled_driver_created_at_mismatches()` / `apply_driver_created_at_corrections()` + `POST /api/admin/legacy-drivers/backfill-created-at` — no file needed, matches every driver row stamped `backfill_reason: orphaned_by_2026-08-29_commit_atomicity_bug` against its own user's `created_at`. `apply=false` (default) only reports.
- **No new "migration date" field was needed**: every affected row already carries `legacy_import_metadata->>'imported_at'` (riders) or `->>'backfilled_at'` (backfilled drivers) — the real timestamp of when the system ingested/repaired the record — and neither is rendered anywhere in the admin dashboard, driver app, or rider app. Correcting `created_at` to the real signup date doesn't lose that record; it was never displayed and stays intact.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `find_rider_created_at_corrections`/`apply_rider_created_at_corrections` have exactly one caller each (the new route); same for the driver-side pair. Grepped to confirm.
- **Idempotent by construction**: both finder functions compare by epoch-ms (not string — Postgres/PostgREST trims trailing zero fractional digits, so a string compare would false-positive on every already-correct row) and only report genuine mismatches. Re-running after a partial apply only touches what's still wrong.
- **Read-only preview, write only on apply=True** — same dry-run-first convention as every other tool this session.
- **No other column touched** by either backfill — both `UPDATE`s set only `created_at`.
- **build_plan()'s change is behavior-preserving for CSVs without a `created_at` column** — falls back to the exact same `now()` value as before; only CSVs that carry the column get the new behavior. All 21 pre-existing `test_admin_rider_import.py` tests pass unmodified.
- 82 tests pass across the three affected test files (28 in `test_admin_rider_import.py`, 36 in `test_legacy_mongo_driver_import_service.py`, 18 in `test_admin_legacy_driver_import.py`); `ruff check`/`format` clean.
- `npx tsc --noEmit` clean; `npm run build` succeeded — both admin-dashboard pages compiled.

## 5. User-experience effect

- **Internal admin only.** Before: migrated riders/drivers showed today's date as "Joined," making tenure/cohort questions on the admin Users/Drivers pages misleading. After: shows their real legacy signup date. No rider, driver, or corporate-admin-facing surface is affected — confirmed no "member since"/"joined" UI exists in rider-app or driver-app.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/rider_import_service.py` | `build_plan()` now parses the CSV's real `created_at`; new `find_rider_created_at_corrections()`/`apply_rider_created_at_corrections()` | Fix the root cause + repair already-imported rows |
| `backend/routes/admin/rider_import.py` | New `POST /riders/created-at-backfill` route | Sanctioned production-write path (admin API, not raw SQL) |
| `backend/services/driver_import_service.py` | New `find_backfilled_driver_created_at_mismatches()`/`apply_driver_created_at_corrections()` | Repair for the 692 backfilled driver rows |
| `backend/routes/admin/legacy_driver_import.py` | New `POST /legacy-drivers/backfill-created-at` route | Sanctioned production-write path |
| `backend/tests/test_admin_rider_import.py` | 7 new tests (legacy-date parsing, fallback, backfill dry-run/apply/no-op/non-rider-skip/auth) | Lock in the fix |
| `backend/tests/test_legacy_mongo_driver_import_service.py` | 4 new tests (mismatch found/skipped/ignored, apply writes) | Lock in the driver-row repair |
| `backend/tests/test_admin_legacy_driver_import.py` | 4 new HTTP-level tests | Lock in the route's contract |
| `admin-dashboard/src/lib/api/imports.ts` | New `adminBackfillRiderCreatedAt()`/`adminBackfillDriverCreatedAt()` clients + result types | Frontend clients for the two new routes |
| `admin-dashboard/src/lib/api.ts` | Re-export the four new symbols | Keep the barrel export in sync |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | New "Fix Rider Join Dates" section (Preview → Apply, CSV re-upload) | Give the operator a button to run the rider repair |
| `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx` | New "Fix Backfilled Driver Join Dates" section (Preview → Apply, no file needed) | Give the operator a button to run the driver-row repair |

## 7. Before / after

```python
# Before — always import time
"created_at": datetime.now(timezone.utc).isoformat(),
```

```python
# After — the CSV's own legacy signup date, falling back to import time
created_at = _parse_legacy_epoch_ms(row.get("created_at", "")) or now_iso
...
"created_at": created_at,
```

## 8. Rollback plan

**Code fix**: `git-revert-safe` — a CSV without a `created_at` column behaves identically to before; only CSVs carrying the column get the new value.

**Backfill routes/UI**: `git-revert-safe` — new, additive routes + UI sections. If a corrected `created_at` ever needs to be undone, the old value is knowable (it was the row's `imported_at`/`backfilled_at` timestamp, still on file) and a targeted `UPDATE` could restore it — no other side effects to unwind (no WS event, no notification, no money path reads `users.created_at`/`drivers.created_at`).

## 9. Verification performed

- [x] Root-caused via direct production SQL — confirmed exact affected populations (214 riders, 692 drivers), confirmed the 918/187/925-row populations are unaffected, confirmed `created_at`'s blast radius (admin Users/Drivers "Joined" only, no KPI function reads it).
- [x] `pytest tests/test_admin_rider_import.py tests/test_legacy_mongo_driver_import_service.py tests/test_admin_legacy_driver_import.py` — 82 passed.
- [x] `ruff check` / `ruff format --check` — clean.
- [x] `npx tsc --noEmit` — clean.
- [x] `npm run build` (admin-dashboard) — real production build, succeeded.
- [x] Blast-radius grep: each new service function has exactly one caller (the corresponding route).

## What was NOT verified

- Not yet run against real production — previewing, then applying, both backfills is the operator's next step once this deploys; production will be re-checked directly via SQL afterward, same rigor as every other verification this session.
- No live OSRM/Google/Supabase network access in this environment to exercise the routes end-to-end; the new tests use the same in-memory fake-Supabase pattern already established in these test files.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`; the old value is recoverable from `imported_at`/`backfilled_at` if ever needed)
- [x] Blast radius is stated, not assumed (each new function has one caller; `created_at`'s only consumer is the admin "Joined" column, confirmed by grep)
- [x] No silent behavior change to any live-tested flow — admin-dashboard "Joined" column display only; no rider/driver-facing surface, no KPI, no money path touched
