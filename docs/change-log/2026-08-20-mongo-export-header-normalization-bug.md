# CRITICAL: `read_csv`'s header normalization silently broke the SIN/DOB backfill's phone crosswalk

## 1. Issue/gap identified

`backfill_legacy_driver_sin_dob.py` (built earlier this session, already merged to `main`, and — as of
this session's rollout-decision `AskUserQuestion` — **approved by the product owner to run now**) would
silently do nothing if actually run: `plan_legacy_sin_dob_import` would resolve **0 of 157** real
`banks.csv` rows to a Spinr driver, producing a clean "0 updates, 0 errors" report that looks like a
successful no-op run rather than a broken one.

Found while building the vehicle-linkage backfill (Oct 30 checklist item #4, same session): its own
join function hit the identical failure mode against the real export, which is what surfaced this.

## 2. Root cause

`driver_import_service.read_csv()` runs every CSV header through `normalize_header()` — case-folding,
punctuation-to-underscore collapsing, and a hand-built alias table (`"name": "full_name"`, `"dob":
"date_of_birth"`, etc.). This was built for and correctly tuned to the bespoke **Saskatoon driver
recruitment CSV** (a human-curated spreadsheet with inconsistent header spelling). `banks.csv`/
`drivers.csv`/`vehicle_details.csv` are a completely different file — the **raw MongoDB export** of the
old app, with Mongo's own field-naming convention (`_id`, `__v`).

Two independent corruptions, both from the same root cause:

1. `_id` → `normalize_header`'s regex (`re.sub(r"[^a-z0-9]+", "_", value).strip("_")`) treats the
   leading underscore as a stray-punctuation run and strips it, then `.strip("_")` removes it entirely
   → the key becomes `"id"`. Every join function that resolves a Mongo ObjectId reference reads
   `row["_id"]` — `join_legacy_bank_sin_dob` (SIN/DOB backfill) and `join_legacy_vehicle_details`
   (vehicle-history backfill, built in the same session as this fix, never shipped with the bug live).
   Both silently built an **empty** `phone_by_object_id` lookup dict against real `read_csv()` output,
   so every row's `driver_id` lookup missed and was reported as "no matching row in the Mongo drivers
   export" — indistinguishable from a genuinely unmatched row, which is exactly why this went unnoticed
   until someone diffed the real crosswalk numbers.
2. `vehicle_details.csv`'s `name` column (the vehicle's *make*, e.g. `"Toyota"`) collides with the
   alias table's `"name": "full_name"` entry (correct for the Saskatoon CSV's *person* name column) —
   would have silently mislabeled every vehicle-make value had this backfill shipped with the bug live.

**Why the existing SIN/DOB tests never caught this**: `test_legacy_sin_dob_import_service.py`
constructs its `_bank_row()`/`_mongo_driver_row()` fixtures as hand-written Python dicts with `_id`
already present — it never calls `read_csv()` at all, so the integration between the join logic and the
real CSV-parsing pipeline was never exercised. A unit test with a fully mocked dependency gave zero
real coverage of this specific, real integration bug — exactly the "stubbed-out component gives zero
real coverage" pattern CLAUDE.md's pre-merge gates warn about.

## 3. Fix/remediation

Added `driver_import_service.read_mongo_export_csv()` — a raw CSV reader that preserves column names
exactly as exported (mirrors `booking_import_service.read_csv`'s already-correct convention for the
same class of file — that module never had this bug, since it never routes through
`normalize_header`). Updated both CLI scripts that read a raw Mongo-export CSV to call it instead of
`read_csv`:

- `backfill_legacy_driver_sin_dob.py` — **the fix that matters for the already-approved rollout.**
- `backfill_legacy_vehicle_history.py` — this session's own new script; never shipped with the bug
  live, fixed before its first commit landed.

`join_legacy_bank_sin_dob`/`join_legacy_vehicle_details` themselves needed no change — their `row["_id"]`
logic was always correct, it was being fed the wrong input.

**Verified against the real cached export, not just reasoned about:**

| | Before (`read_csv`) | After (`read_mongo_export_csv`) |
|---|---|---|
| SIN/DOB phone crosswalk | 0/157 | 157/157 |
| Vehicle-history phone crosswalk | 0/355 | 308/355 (the rest are genuinely unmatched — orphan `driver_id`s or blank `drivers.csv` phones, expected) |
| `vehicle_details.csv`'s `name` field | `"full_name"` | `"name"` = `"Toyota"` (unmangled) |

`read_csv`/`normalize_header` themselves are **unchanged** — they remain correct for the Saskatoon CSV,
which is the only file type that should ever go through them. `read_mongo_export_csv` is the new,
correct entry point for anything reading `banks.csv`/`drivers.csv`/`vehicle_details.csv`/any future
raw-export file.

## 4. Risk & impact on existing functionality

**Blast radius grepped, not assumed.** Every caller of `driver_import_service.read_csv` across the
repo:
- `backend/scripts/import_saskatoon_drivers.py` (the original Saskatoon CLI) — **correctly unaffected**,
  reads the bespoke CSV `read_csv`/`normalize_header` was actually built for. No change.
- `backend/routes/admin/driver_import.py` (admin dashboard upload flow) — reads via `read_csv_text`
  (same normalization, admin-upload path for the same bespoke CSV). **Correctly unaffected** — this
  flow has never accepted a raw Mongo export.
- `backfill_legacy_driver_sin_dob.py` — **fixed** (this change).
- `backfill_legacy_vehicle_history.py` — **fixed** (built and fixed in the same session, never shipped
  broken).
- No other caller of `read_csv`/`read_mongo_export_csv` exists in the backend as of this fix.

**This does not affect anything already committed to production.** No `--apply` run of the SIN/DOB
backfill has ever happened (confirmed throughout this session's own tracking docs) — the bug was caught
before it could silently no-op a real run, not after.

## 5. User experience effect

None directly — backend CLI tooling only, not reachable by any rider/driver/admin-facing flow.
**Operationally significant**, though: `docs/runbooks/legacy-backfill-scripts-rollout.md`'s "Decision
recorded" section already told the product owner the SIN/DOB backfill is approved to run now. Without
this fix, that run would have appeared to succeed (clean report, 0 errors) while silently accomplishing
nothing — the worst kind of failure for an operator to catch, since nothing looks wrong. Updated that
runbook (see below) to state the fix explicitly rather than let the existing "safe to run" framing stand
unqualified.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/services/driver_import_service.py` | New `read_mongo_export_csv()` function. `read_csv`/`normalize_header` unchanged. | Correct reader for raw Mongo-export CSVs. |
| `backend/scripts/backfill_legacy_driver_sin_dob.py` | `svc.read_csv` → `svc.read_mongo_export_csv` for both CSV args. | Fixes the already-approved SIN/DOB backfill's silent no-op. |
| `backend/scripts/backfill_legacy_vehicle_history.py` | Same fix, in the same commit as the script's own creation. | Never shipped with the bug live. |
| `backend/tests/test_mongo_export_csv_reader.py` | New, 4 tests — locks in the `_id`/`name` preservation and documents *why* `read_csv` is the wrong function for this file class. | Regression coverage this bug class never had. |
| `docs/change-log/2026-08-20-mongo-export-header-normalization-bug.md` | This file. | Change Impact Log. |

## 7. Before/after snippet

Before (`backfill_legacy_driver_sin_dob.py`):
```python
bank_rows = svc.read_csv(args.banks_csv)
driver_rows = svc.read_csv(args.drivers_csv)
```

After:
```python
bank_rows = svc.read_mongo_export_csv(args.banks_csv)
driver_rows = svc.read_mongo_export_csv(args.drivers_csv)
```

## 8. Rollback plan

`git-revert-safe` — this is a pure bug fix to code that has never been `--apply`'d against any
environment; there is no live data to roll back.

## 9. Verification performed

- Re-ran the real crosswalk against the actual cached export (table above) — not reasoned about,
  measured directly, before and after.
- Full `driver_import`/`vehicle_history`/`sin_dob`-scoped test suite (145 tests across 6 files) passes
  after the fix.
- 4 new regression tests specifically pin the `_id`/`name` preservation behavior and document why
  `read_csv` must never be used for this file class again.
- `ruff check` clean on all touched files.
- `python3 -m py_compile` clean on all touched files.

## 10. What was NOT verified

- **No live Supabase.** The 157/308 crosswalk-resolution counts above are against the raw CSVs only
  (phone-number join), not against a real Spinr `drivers` table — the actual number of rows that would
  update once `--apply` runs depends on how many of those phones match a real, already-imported Spinr
  driver, which can only be known live.
- **No live Stripe cross-check** — not applicable, no money touched.
