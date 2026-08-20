# Legacy vehicle-linkage backfill — driver_vehicle_history (Oct 30 checklist item #4)

## 1. Issue/gap identified

`docs/runbooks/legacy-migration-playbook.md`'s Oct 30 checklist item #4: "Vehicle-at-trip-time linkage
backfill using `vehicle_details.csv` into `driver_vehicle_history` (migration 157) — closes the
7-year driver/vehicle-linkage regulatory retention gap." No importer targeting `driver_vehicle_history`
from the old app's `vehicle_details.csv` existed. The vehicle fields already on `drivers` for
Saskatoon-imported drivers came from the bespoke Saskatoon recruitment CSV (a different, already-
completed import), not from the old MongoDB app's own vehicle records — those are a genuinely separate,
unaddressed source.

## 2. Root cause

`driver_vehicle_history` (migration 157) is populated by a live writer (`utils/vehicle_history.py`,
called from `routes/drivers.py`/`routes/admin/drivers.py` on every in-app vehicle edit) but had no
counterpart for the old app's pre-Spinr vehicle records — those 355 `vehicle_details.csv` rows (330
unique legacy drivers, 24 with more than one row) had no path into the audit trail at all.

## 3. Fix/remediation

Added `plan_legacy_vehicle_history_backfill`/`apply_legacy_vehicle_history_backfill` to
`backend/services/driver_import_service.py`, following the same plan/apply CLI pattern as the SIN/DOB
backfill (`backfill_legacy_driver_sin_dob.py`) already built this session, plus a new CLI wrapper
`backend/scripts/backfill_legacy_vehicle_history.py`.

- **Source/crosswalk**: `vehicle_details.csv` (make/model/colour/year/plate/VIN, keyed by a Mongo
  ObjectId `driver_id`) joined to `drivers.csv` (resolves that ObjectId to a phone) — the identical
  100%-joinable crosswalk the SIN/DOB backfill already established for this export.
- **Population**: only drivers already tagged `legacy_saskatoon_driver_import` in
  `legacy_import_metadata` — same phone-coincidence guard as the SIN/DOB backfill; an organic driver's
  history can never be touched by this script.
- **Target**: `driver_vehicle_history` only — deliberately never writes to `drivers`' own current
  vehicle_make/model/etc columns (matches the checklist item's literal wording; avoids overlapping with
  or clobbering whatever the driver, an admin, or the original Saskatoon import already set as the
  *current* vehicle).
- **Field vocabulary**: reuses `utils/vehicle_history.TRACKED_FIELDS` exactly (`vehicle_make`,
  `vehicle_model`, `vehicle_color`, `vehicle_year`, `vehicle_vin`, `license_plate`), minus
  `vehicle_type_id` — no reliable source column for it in `vehicle_details.csv`.
- **Before/after chain reconstruction**: a driver can have more than one legacy vehicle row (24/330).
  Rows are grouped per driver, sorted by the *legacy* row's own `created_at` (not import time, not CSV
  order), and a history row is written only when a field's value actually changed from the
  previously-known value — same semantic the live writer already uses, so a backfilled driver's
  timeline reads identically to one built from real edits. First-ever value per field gets
  `old_value = NULL`.
- **Idempotency**: plan-time checks each candidate `(driver_id, field, created_at, new_value)` tuple
  against what's already in `driver_vehicle_history` and skips an exact duplicate. Unlike the SIN/DOB
  backfill's UPDATE-based never-clobber guard, this table is INSERT-only — there is no column being
  conditionally overwritten, so there is no write-time race to close the way `.is_(col, "null")` does
  for SIN/DOB. Documented explicitly in the module (not silently assumed): a genuine concurrent
  double-run could at worst insert one harmless duplicate history row, never lose or overwrite data.
  Acceptable for a one-shot, human-run CLI script — same operational model as every other backfill this
  session built.
  - **Fixed same day (spinr-migration-reviewer finding):** the comparison originally used the raw
    `created_at` string, which would never have actually matched on a re-run — Postgres/PostgREST
    trims a `timestamptz`'s trailing zero fractional digits on output (`.123000` → `.123`), which
    never string-matches Python's zero-padded `isoformat()` for the same instant. This would have
    broken the documented "safe to re-run after a partial failure" guarantee on essentially every row
    (every epoch-ms-derived timestamp has microseconds that are an exact multiple of 1000). Fixed by
    comparing a canonicalized epoch-ms value (`_epoch_ms_from_iso`) on both sides instead of the raw
    string. New regression test manually rewrites a committed row's `created_at` into Postgres's
    trimmed form before re-planning, proving the fix survives real round-trip serialization (the
    original fake-store test only ever echoed back the literal Python dict, which couldn't catch
    this). Also added a deterministic secondary sort key (`old_vehicle_id`, the legacy Mongo
    ObjectId) for the same-reviewer's finding that two legacy rows sharing an identical `created_at`
    had no tiebreaker beyond arbitrary CSV-row order.
  - **Known, accepted gap (also flagged by review, not fixed):** `driver_vehicle_history` has no
    `legacy_import_metadata`-equivalent provenance column (unlike `drivers`/`rides`), so a backfilled
    row is schema-indistinguishable from any other automated "system"-authored write to this table.
    The only signal is the implicit heuristic that its `created_at` is anomalously old relative to
    when it was actually inserted — real, used correctly in this document's rollback-plan reasoning,
    but undocumented at the schema level. Acceptable for a one-shot backfill; flag as a follow-up if
    a second legacy-audit backfill against this table is ever proposed.
- **PII**: report items carry only `old_driver_id`/`old_vehicle_id`/field-name/generic message — never
  a raw phone, plate, or VIN value, matching every other importer's report convention in this codebase.

## 4. Risk & impact on existing functionality

**Blast radius grepped, not assumed:**
- `utils/vehicle_history.py` (the live writer) — untouched; this backfill writes to the same table via
  a completely separate code path (`driver_import_service.py`, not `vehicle_history.py`), using the
  same `TRACKED_FIELDS` vocabulary so both writers' rows are indistinguishable in shape.
- `routes/admin/drivers.py`'s `GET /admin/drivers/{driver_id}/vehicle-history` — the only reader of
  this table, a plain `get_rows` ordered by `created_at DESC`. Confirmed it needs zero changes: it will
  surface backfilled rows correctly interleaved with any later real edits, since `created_at` is the
  actual legacy event time (not import time).
- `admin-dashboard/src/app/dashboard/drivers/page.tsx`'s "Vehicle Change History" panel — confirmed it
  already renders `changed_by_role` as raw text (will show "(system)" for these rows, exactly the
  existing generic-system-change label class 157's own CHECK constraint defines) and `old_value`/
  `new_value` with a `|| "—"` null fallback — no frontend change needed.
- `drivers` table's own vehicle columns — never written by this backfill; zero interaction with
  anything that reads a driver's *current* vehicle (matching, dispatch, admin driver list).
- No migration needed — the target table and its RLS/grants (migration 157) are unchanged.

**PIPEDA note (spinr-security-auditor finding, not a new weakness):** migration 157 stores plate/VIN
as plaintext `TEXT` in `driver_vehicle_history.old_value`/`new_value` — the same convention the live
writer (`utils/vehicle_history.py`) already uses for every real-time vehicle edit. This backfill
inherits that existing design decision rather than introducing a new one, but does meaningfully
increase the volume of plaintext VIN/plate at rest in that table (up to ~355 legacy rows across up to
6 tracked fields each). Not a blocker for this PR (consistent with already-shipped behavior for the
same table), but worth surfacing here rather than silently: if the table's plaintext-storage decision
is ever revisited, this backfill's rows are part of that scope too.

## 5. User experience effect

None until `--apply` actually runs (still pending — no live Supabase credentials in this session, same
constraint as every other backfill). Once committed: an admin viewing an affected driver's profile will
see additional, older entries in the existing "Vehicle Change History" panel, correctly dated to when
the vehicle was actually registered in the old app — no new UI, no behavior change to any rider/driver-
facing flow.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/services/driver_import_service.py` | New section: `plan_legacy_vehicle_history_backfill`/`apply_legacy_vehicle_history_backfill`/`join_legacy_vehicle_details`/`print_vehicle_history_report`. | Implements the backfill. |
| `backend/scripts/backfill_legacy_vehicle_history.py` | New CLI wrapper, mirrors `backfill_legacy_driver_sin_dob.py`. | Operator entry point. |
| `backend/tests/test_legacy_vehicle_history_backfill.py` | New, 20 tests. | Coverage. |
| `docs/change-log/2026-08-20-legacy-vehicle-history-backfill.md` | This file. | Change Impact Log. |

## 7. Before/after snippet

Before: no code path existed from `vehicle_details.csv` to `driver_vehicle_history`.

After (`driver_import_service.py`, the core per-driver chain logic):
```python
for driver_id, rows in by_driver.items():
    rows_sorted = sorted(rows, key=lambda r: r["created_at"])
    last_value: dict[str, str | None] = {}
    for row in rows_sorted:
        for field_name, value in row["fields"].items():
            if not value:
                continue
            prev = last_value.get(field_name)
            if prev == value:
                continue  # no change from the previously-known value
            key = (driver_id, field_name, row["created_at"], value)
            if key in existing_keys:
                plan.skipped_already_backfilled += 1
            else:
                plan.rows_to_insert.append({..., "old_value": prev, "new_value": value, "created_at": row["created_at"]})
            last_value[field_name] = value
```

## 8. Rollback plan

No migration involved. Append-only, so there is no existing value this backfill could have clobbered —
reverting means deleting the specific `driver_vehicle_history` rows this run inserted. Every row's
`driver_id`/`field`/`old_driver_id`/`old_vehicle_id` is logged per apply-time report line, and
`created_at` for a backfilled row is always the legacy event time (years before this script's own run
date), which cannot collide with a real live edit's history row — the id set is unambiguous.

## 9. Verification performed

- `ruff check` on both new/touched Python files — clean.
- `python3 -m py_compile` — clean.
- 17 new unit tests: phone crosswalk, legacy-driver-only gate, all-tracked-fields staging, blank-field
  skip, unparseable-timestamp error handling, multi-vehicle before/after chain (including out-of-order
  input, same-value-no-duplicate, a deterministic same-`created_at` tiebreak on `old_vehicle_id`, and
  idempotency surviving Postgres's timestamp-fraction-trimming round-trip — see §3's "Fixed same day"
  note), apply-path insert shape (report-only keys stripped before the actual DB write),
  refuse-on-errors, no-op-with-nothing-to-apply, and a report-printer smoke test.
- Full `driver_import`-scoped suite (103 tests across 4 files) re-run after the change — all pass,
  nothing broken.
- Confirmed via grep that the one existing reader (`routes/admin/drivers.py`) and its frontend consumer
  (`admin-dashboard`'s driver detail page) need no changes.

## 10. What was NOT verified

- **No live Supabase.** All tests run against a fake in-memory Supabase client, same as every other
  importer in this session. No `--apply` run has happened against any environment.
- **Not re-run against the real cached `vehicle_details.csv`/`drivers.csv` export** the way the
  cancelled/failed booking import was dry-run-verified against the real CSV in its own task — this
  backfill's real-export row counts (330 unique legacy drivers, up to 6 tracked fields each, 24 with a
  multi-row chain) were computed directly from the cached export for design purposes (see the code
  comments) but a full `build_plan`-equivalent dry run against the real files, with an empty fake
  Spinr `drivers` table, was not performed for this task. Recommended before actually running `--apply`.
- **No live Stripe cross-check** — not applicable (no money touched).
- **No visual/snapshot regression tooling** — backend-only change; the admin-dashboard panel that will
  render these rows was reasoned about via grep (existing null-fallback rendering, generic
  `changed_by_role` text display), not screenshotted.
