# Change Impact & Risk Log — Data Transfer module: export route (Phase 1.2)

## Issue/gap identified
Phase 1.1 added the gather/ZIP-build services but no HTTP surface — nothing
could actually trigger an export.

## Root cause
Deliberate phasing (CLAUDE.md's ≤3-file subtask rule): service layer first,
route second.

## Fix/remediation
- New `backend/routes/admin/data_transfer_export.py`: `POST /admin/data-transfer/export`.
  Accepts `{entities: [{entity_type, entity_id}], doc_types?}`, capped at 100
  entities/request (mirrors the 500-row cap pattern in `driver_import.py`).
  Records a `data_transfer_export_jobs` row (pending → completed/failed),
  gathers bundles via `entity_export_service.gather_entity_bundles`, builds
  the ZIP via `bundle_zip_builder.build_export_zip`, uploads to a new private
  `data-transfer-exports` bucket (mirrors `_upload_export_zip` in
  `tax_exports.py`), returns a 7-day signed download URL. Audit-logged via
  the existing `log_admin_action` helper.
- Modified `backend/routes/admin/__init__.py`: imports and registers the new
  router on `admin_router`, gated by `require_module("bulk_operations")` —
  reusing the existing module grant rather than introducing a new
  `"data_transfer"` module string that no staff role has been granted yet
  (that's a follow-up staff-permissions change, not blocking this route).

## Risk & impact on existing functionality
Blast radius: `backend/routes/admin/__init__.py` is the central admin router
registry — I only *added* two lines (an import + an `include_router` call);
no existing router registration, ordering, or dependency was touched. Grepped
`require_module("bulk_operations")` — the only other consumer is the
`bulk-operations` admin-dashboard page itself, so this route inherits an
already-established, narrowly-granted permission (currently only
`super_admin`/`admin` per the frontend's `isSuperAdmin` gate — same story
server-side since no staff role lists `bulk_operations` in its `modules`
claim). `db_supabase.update_one`/`insert_one` calls target only the new
`data_transfer_export_jobs` table (zero other readers/writers). No existing
route, table, or Storage bucket is touched.

## User experience effect
None yet — no frontend calls this endpoint. First reachable via `curl`/API
client only until Phase 4's UI lands.

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/routes/admin/data_transfer_export.py` | New: export route | HTTP surface for the export service |
| `backend/routes/admin/__init__.py` | +2 lines: import + `include_router` | Wire the new route into the admin router, module-gated |

## Before/after snippet
```python
# before: no such route existed
# after (routes/admin/__init__.py):
admin_router.include_router(
    data_transfer_export_router, dependencies=[Depends(require_module("bulk_operations"))]
)
```

## Rollback plan
Remove the two added lines in `routes/admin/__init__.py` and delete
`data_transfer_export.py` — no other code depends on this route yet (grep
confirmed). The `data-transfer-exports` Storage bucket and
`data_transfer_export_jobs` rows created during testing are orphaned but
inert (private bucket, no public access, 7-day TTL already tracked for the
purge loop landing in Phase 1.3).

## Verification performed
- `python3 -m py_compile` on both changed/new files — passes.
- Verified `db_supabase.update_one(table, filters, update)` and
  `log_admin_action(admin, action, resource, resource_id, details)` signatures
  against their real definitions in `repositories/_base.py` and
  `utils/audit_logger.py` before use.
- Confirmed `require_module`/`get_admin_user` dependency shapes against
  `dependencies/__init__.py`.

## What was NOT verified
- Full `python3 -c "import server"` was attempted but FastAPI isn't installed
  in this environment (no venv set up this session) — could not exercise the
  actual FastAPI app construction or route resolution, only static syntax
  checks and manual signature cross-referencing.
- No integration/unit test added yet for this route (planned alongside
  Phase 3's search endpoint tests, once there's a fuller slice to test
  against).
- The `data-transfer-exports` bucket has not been created/exercised against
  a real Supabase project.
