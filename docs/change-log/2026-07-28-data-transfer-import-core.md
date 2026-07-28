# Change Impact & Risk Log — Data Transfer module: import core (Phase 2.1)

## Issue/gap identified
Phase 1 shipped export (ZIP out) with no way to bring an exported bundle back
in on another environment — the stated goal was portal-to-portal onboarding,
which needs both directions.

## Root cause
Phased build; import was always Phase 2, deliberately after export so the
bundle format existed to import against.

## Fix/remediation
- New `backend/services/data_transfer/entity_import_service.py`: parses the
  exact ZIP shape `bundle_zip_builder.build_export_zip` produces (one folder
  per entity, `raw_data.json` + `documents/*`), resolves each entity to
  `new` / `existing_match` (already imported from this exact bundle source —
  matched on `legacy_import_metadata.old_driver_id` + a new
  `data_transfer_bundle_import` source string, same idempotency convention as
  `driver_import_service.py`) / `conflict` (phone already used by a
  *different*, non-bundle-sourced account). `commit_plan` inserts only the
  `new` users + driver profile rows — document re-upload and insurance-period
  replay are intentionally deferred to Phase 2.2 (`bundle_document_uploader`)
  so a document-upload failure can't silently roll back an already-created
  profile.
- New `backend/routes/admin/data_transfer_import.py`: `/data-transfer/import/validate`
  (dry-run, no writes) and `/commit` (re-validates, writes only if clean) —
  same two-step contract as `driver_import.py`. 200MB cap (vs. the CSV
  importers' 1MB) since bundles carry document files.
- Modified `backend/routes/admin/__init__.py`: registers the new router,
  gated by the same `bulk_operations` module as the export route.

**Deviation from the original plan**: the plan called for adding a
bundle-mode branch inside the existing `driver_import.py` file. I built a
fully separate route/service instead — `driver_import.py` handles a
hand-built CSV with its own column-mapping schema
(`REQUIRED_DRIVER_COLUMNS`, `normalize_header`, etc.); a ZIP bundle produced
by this module's own export has nothing in common with that format beyond
"creates users." Branching one file to parse two unrelated input formats
would have made `driver_import.py` harder to reason about for no benefit —
the existing CSV path is completely untouched by this change (confirmed:
zero lines of `driver_import.py` modified).

## Risk & impact on existing functionality
Blast radius: `entity_import_service.py`/`data_transfer_import.py` are new
files with zero existing callers. `routes/admin/__init__.py` again gets only
additive lines (2 imports + 1 include_router), same pattern as Phase 1.2 —
no existing router registration touched. `db_supabase.insert_one("users",
...)`/`insert_one("drivers", ...)` calls use the same helpers every other
route in the codebase uses; no new query pattern, no write to any table this
module doesn't own. Grepped for other readers of `legacy_import_metadata` —
`driver_import_service.py` is the only other writer/reader, and it filters by
its own `source` value (`legacy_saskatoon_driver_import`), so this module's
`data_transfer_bundle_import` source string can never collide with or be
mistaken for a Saskatoon-CSV-imported row.

## User experience effect
None yet — no frontend wired to these endpoints (Phase 4.2).

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/services/data_transfer/entity_import_service.py` | New: ZIP bundle parser + validate/commit plan | Core import logic |
| `backend/routes/admin/data_transfer_import.py` | New: validate/commit routes | HTTP surface for import |
| `backend/routes/admin/__init__.py` | +2 lines: import + `include_router` | Wire the new routes in, module-gated |

## Before/after snippet
```python
# after (routes/admin/__init__.py) — additive only:
admin_router.include_router(data_transfer_import_router, dependencies=[Depends(require_module("bulk_operations"))])
```

## Rollback plan
Remove the two added lines in `routes/admin/__init__.py`, delete
`data_transfer_import.py` and `entity_import_service.py`. No other code
depends on either yet (grep-confirmed). Any rows already committed via
`/commit` during testing are ordinary `users`/`drivers` rows and would need
manual cleanup like any other bulk-import mistake — same operational
reality as the existing `driver_import.py`/`rider_import.py` commit paths.

## Verification performed
- `python3 -m py_compile` on all three files — passes.
- Traced `legacy_import_metadata` usage across the codebase (grep) to confirm
  the new `data_transfer_bundle_import` source string can't collide with the
  existing `legacy_saskatoon_driver_import` source.
- Cross-checked `db_supabase.insert_one`/`get_rows` call shapes against their
  real signatures in `repositories/_base.py`.

## What was NOT verified
- No unit test yet for `entity_import_service.py` (`test_entity_import_service.py`
  planned once the document/insurance-period replay in Phase 2.2 completes
  the full commit path — testing a partial commit_plan now would need
  rewriting once 2.2 lands).
- Not exercised against a real ZIP produced by the Phase 1 export route (no
  running backend in this environment to produce one end-to-end).
- Conflict-detection logic (phone-collision check) is reasoned through code
  reading only, not exercised against real duplicate data.
