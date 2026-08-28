# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude Code (migration-batch-readiness track) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (local commit, not pushed — isolated worktree track) |
| Related issue or gap ID | Follow-up flagged inline in `driver_import_service.py`'s Mongo-driver-import section (commit `5983399ea`, 2026-08-27) |

## 1. Issue / gap identified

`plan_legacy_sin_dob_import` and `plan_legacy_vehicle_history_backfill` (both in
`backend/services/driver_import_service.py`) only matched a Spinr driver whose
`legacy_import_metadata.source == "legacy_saskatoon_driver_import"`
(`IMPORT_SOURCE`). Drivers created by the newer Mongo-export importer
(`build_mongo_driver_import_plan`, `MONGO_IMPORT_SOURCE =
"legacy_mongo_driver_import"`) were silently skipped by both backfills — their
SIN/DOB (from `banks.csv`) and vehicle-registration history (from
`vehicle_details.csv`) would never be imported, even though both CSVs are part
of the same Mongo export and key off the same `drivers.csv` `_id`.

## 2. Root cause

The two backfills predate the Mongo importer (`IMPORT_SOURCE`-only check
shipped first) and the Mongo importer's own author deliberately deferred
generalizing that check — documented inline as a "known follow-up NOT done"
comment in the code, to avoid silently changing an already-shipped, tested
safety gate as a side effect of an unrelated change. This task closes that
flagged follow-up.

A second, non-obvious wrinkle (confirmed by reading
`build_mongo_driver_import_plan` before making any change): a driver touched
by the Mongo importer can exist in **three shapes**, not one:

1. **Directly created** (`drivers_to_insert`) — a brand-new driver row whose
   own top-level `legacy_import_metadata` is `{"source":
   "legacy_mongo_driver_import", "old_driver_id": <mongo _id>, ...}`.
2. **Linked** (`users_to_update`, sub-population 1) — an existing account
   (almost always a rider) gets a **new** driver row pointed at it. That new
   driver row's own `legacy_import_metadata` is built identically to shape 1
   (same code path, `driver_row["legacy_import_metadata"]` at
   `driver_import_service.py:1943`, unconditional on which branch got there) —
   confirmed by reading the code, not assumed. So shape 2 needs no handling
   beyond generalizing the top-level `source` check.
3. **Enriched** (`drivers_to_enrich`, sub-population 2) — **no new driver
   row**. An existing driver (created by some *other* importer, e.g.
   Saskatoon, or an organic signup) gets an **additive**
   `legacy_import_metadata.mongo_driver_history` list entry appended
   (`driver_import_service.py:1863-1868`). Its own top-level
   `legacy_import_metadata.source` is left completely untouched — it stays
   whatever created the driver *originally* (e.g. `"legacy_saskatoon_driver_
   import"`, or nothing at all for an organic driver). A naive `source in
   (IMPORT_SOURCE, MONGO_IMPORT_SOURCE)` check would silently miss this
   shape's Mongo lineage entirely for a driver with no matching top-level
   source. (In practice, most enriched drivers in the real export were
   originally Saskatoon-imported and would already pass the old check on
   `source == IMPORT_SOURCE` alone — but an organic driver enriched this way
   would not, and the code makes no promise it's always the Saskatoon case.)

## 3. Fix / remediation

Added one small private helper, `_has_mongo_driver_history_entry(meta,
old_id)`, that checks whether `old_id` (the crosswalk's Mongo `drivers.csv`
`_id`) appears in a driver's additive `mongo_driver_history` list. Both
backfills' gate changed from:

```python
if meta.get("source") != IMPORT_SOURCE:
```

to:

```python
if meta.get("source") not in (IMPORT_SOURCE, MONGO_IMPORT_SOURCE) and not _has_mongo_driver_history_entry(
    meta, old_id
):
```

This covers all three shapes: shapes 1 and 2 via the `source in (...)`
membership check (their own top-level source is one of the two known
values); shape 3 via the new helper checking the specific `old_id` this
crosswalk row resolved to against the driver's `mongo_driver_history` list.

Deliberately **not** reused: `_mongo_driver_already_linked` (the Mongo
importer's own resume/idempotency helper) already contains near-identical
logic, but it was left completely untouched — this change adds a new,
independent helper rather than modifying or calling into that
already-shipped, tested function, so this fix cannot alter that function's
existing behavior as a side effect.

The two `apply_*` counterparts (`apply_legacy_sin_dob_import`,
`apply_legacy_vehicle_history_backfill`) needed **no change** — confirmed by
reading both. Each already writes to the *real* Spinr `driver["id"]` resolved
during planning (via the phone crosswalk, `drivers_by_phone.get(row["phone"])`
→ `driver["id"]`), not to any legacy/old id, and that resolution logic is
identical for all three shapes.

### Crosswalk key verified before generalizing anything

`join_legacy_bank_sin_dob` / `join_legacy_vehicle_details` join `banks.csv` /
`vehicle_details.csv`'s `driver_id` column against `drivers.csv`'s `_id`
column — the **same** Mongo ObjectId id space `build_mongo_driver_import_plan`
keys everything on (its own `old_id = row.get("_id")`, same as `_mongo_driver_
already_linked`'s `old_id`/`old_driver_id` and this backfill's crosswalk
`old_driver_id`). Confirmed against the real export files found under this
session's scratchpad (`mongo_extract/Mongo_20260822-DrivelocLess/{drivers,
banks,vehicle_details}.csv`):

- `banks.csv`: 162/162 rows' `driver_id` resolve against `drivers.csv._id`
  (100%, matching the existing `join_legacy_bank_sin_dob` docstring's claim).
- `vehicle_details.csv`: 335/382 rows' `driver_id` resolve (87.7% — the
  remainder are pre-existing "no Spinr driver with this phone number"
  warnings already handled by the unchanged unmatched-phone path; this is a
  data-completeness fact, not a correctness issue with the join key itself).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these two backfill functions' matching gate.**
  Grepped the whole `backend/` tree for every caller of
  `plan_legacy_sin_dob_import`, `plan_legacy_vehicle_history_backfill`,
  `_mongo_driver_already_linked`, `IMPORT_SOURCE`, and `MONGO_IMPORT_SOURCE`.
  Both `plan_*` functions are only called from their own CLI entry points
  (`backend/scripts/`) and their own test files — no route, background loop,
  or other service calls them. `_mongo_driver_already_linked` has exactly one
  call site (`build_mongo_driver_import_plan` itself, lines 1821-1822) and was
  not touched.
- **What could regress:** a Saskatoon-only (`IMPORT_SOURCE`) driver's
  behavior must be byte-identical to before — verified with the pre-existing
  `test_plan_skips_driver_without_legacy_import_source` regression test in
  both files (unchanged, still passing) plus every other pre-existing test in
  both files (unchanged, still passing: 47/47 before, still 47 after, plus 6
  new tests added — see Verification).
- **No write-path change** — `apply_legacy_sin_dob_import` and
  `apply_legacy_vehicle_history_backfill` are unmodified; only which drivers
  get *planned* for a write changes, not how a write is made once planned.
- **Not a live/user-visible change** — both backfills are one-shot,
  human-run CLI scripts (`backend/scripts/`), never invoked from a request
  handler, background loop, or app UI. Nothing is visible mid-session to a
  rider or driver already using the app.
- **PII-adjacent but scope-preserving** — this changes *which existing
  drivers* are eligible for a SIN/DOB or vehicle-history backfill; it does
  not change what data is written, how it's encrypted, or the never-clobber
  guards. No new data category is introduced.

## 5. User-experience effect

None. Backend-only, CLI-invoked migration tooling; no rider/driver/admin-facing
surface changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/driver_import_service.py` | Added `_has_mongo_driver_history_entry` helper; broadened the `source` gate in `plan_legacy_sin_dob_import` and `plan_legacy_vehicle_history_backfill` to accept `MONGO_IMPORT_SOURCE` and the enriched-driver `mongo_driver_history` shape; updated the two functions' safety-rule comments and the Mongo-importer section's "known follow-up NOT done" comment to record closure | Close the deliberately-deferred follow-up so SIN/DOB and vehicle-history backfills reach drivers created by the newer Mongo importer |
| `backend/tests/test_legacy_sin_dob_import_service.py` | Added `MONGO_IMPORT_SOURCE` constant + 3 new tests (directly-created match, enriched-via-history match, enriched-history-mismatch-still-skips) alongside the existing unmodified regression test | Prove the new matching logic for both new shapes without weakening the existing Saskatoon-only regression coverage |
| `backend/tests/test_legacy_vehicle_history_backfill.py` | Same 3 new tests, mirrored for the vehicle-history backfill | Same reasoning, other backfill |

## 7. Before / after

```python
# Before (both plan_legacy_sin_dob_import and plan_legacy_vehicle_history_backfill)
meta = driver.get("legacy_import_metadata") or {}
if meta.get("source") != IMPORT_SOURCE:
    plan.warnings.append(...)
    plan.skipped_not_legacy_driver += 1
    continue
```

```python
# After
meta = driver.get("legacy_import_metadata") or {}
if meta.get("source") not in (IMPORT_SOURCE, MONGO_IMPORT_SOURCE) and not _has_mongo_driver_history_entry(
    meta, old_id
):
    plan.warnings.append(...)
    plan.skipped_not_legacy_driver += 1
    continue
```

## 8. Rollback plan

Pure code change to a one-shot CLI planning function — no migration, no
flag, no data mutation of its own (the backfills' own write guards are
unchanged). Rollback is `git revert` of this commit; nothing needs a
flag flip or data remediation since no `apply_*` (write) path was touched
and no batch has been run against production with this change yet.

## 9. Verification performed

- [x] Automated tests run (unit): `pytest backend/tests/test_legacy_sin_dob_import_service.py backend/tests/test_legacy_vehicle_history_backfill.py backend/tests/test_legacy_mongo_driver_import_service.py backend/tests/test_driver_import_service.py backend/tests/test_driver_import_service_coverage.py backend/tests/test_import_saskatoon_drivers.py -q --no-cov` → **177 passed**, 0 failed.
- [x] `ruff check` and `ruff format --check` on all 3 modified files: clean.
- [x] Manual repro: not applicable (no staging DB access from this session); relied on the unit-test fake-Supabase harness already established in both test files (documented as this codebase's convention for this module, since `db_supabase`/`repositories` aren't in the call path).
- [x] Blast-radius grep performed: `plan_legacy_sin_dob_import`, `plan_legacy_vehicle_history_backfill`, `_mongo_driver_already_linked`, `IMPORT_SOURCE`, `MONGO_IMPORT_SOURCE` across `backend/` — no route/loop/service callers found; CLI scripts + tests only.
- [x] Real-data spot check: read the actual `banks.csv`/`vehicle_details.csv`/`drivers.csv` export files found under this session's scratchpad and confirmed the `driver_id` → `_id` crosswalk key relationship with a script (162/162 and 335/382 rows resolve respectively) rather than assuming from column names/docstrings alone.
- [ ] Not a backend service/route/UI change, so no `npm run build` applies.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data touched).
- [x] Blast radius is stated: isolated to two CLI-only planning functions, no other callers.
- [x] No silent behavior change to an already-shipped flow — this only *adds* eligibility for two flows that were never run against production for Mongo-imported drivers before (the Mongo importer itself only shipped 2026-08-27); the Saskatoon-only path (already run/relied on) is regression-tested byte-identical.

## What was NOT verified

- **No live/staging Supabase access from this session** — all verification is against the unit-test fake-Supabase harness (`_FakeSupabase`/`_FakeQuery` in both test files), not a real Postgres/PostgREST round-trip. This mirrors the existing convention for this module (see both test files' own header comments) and is the same boundary every other test in these two files already has, not a new gap introduced by this change.
- **Did not run either backfill's CLI script end-to-end against the real export CSVs** — only spot-checked the crosswalk id relationship with a standalone Python script against the real files, and exercised the changed matching logic through unit tests with synthetic fixture rows. A full dry-run against the real `mongo_extract/Mongo_20260822-DrivelocLess/` export was out of scope for this track (no Supabase MCP/production access available in this session) and would be a reasonable follow-up before actually running either backfill's `--apply` against production for Mongo-imported drivers.
- **Did not verify how many real production drivers are currently in the "enriched" shape** (i.e. how many rows this change will actually newly pick up) — that requires a production `legacy_import_metadata` query this session could not run (no live Supabase access). The fix is scoped correctly regardless of that count, but the operator running the next `--apply` batch should expect a non-zero delta and review the dry-run report counts before committing.
