# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (B11/R-B follow-up on the Data Transfer PIA) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B11 / R-B (`docs/privacy/2026-07-28-pia-data-transfer-export.md`) |

## 1. Issue / gap identified

The Data Transfer export had no way to exclude the two highest-sensitivity field groups (exact ride pickup/dropoff GPS coordinates, raw document file bytes) even when the export's actual purpose didn't need them — e.g. seeding a UI-only staging environment with realistic-looking driver profiles doesn't need real GPS history or document scans. Every export was all-or-nothing full-fidelity.

## 2. Root cause

Not applicable — new opt-out capability, not a fix for broken behavior.

## 3. Fix / remediation

Added two optional boolean flags to the export request, both defaulting to `True` (unchanged full-fidelity behavior): `include_ride_gps` and `include_document_bytes`. When `include_ride_gps=False`, `entity_export_service.gather_entity_bundle` strips `pickup_lat`/`pickup_lng`/`dropoff_lat`/`dropoff_lng` from each ride dict — ride rows themselves and all other ride fields (fare, status, timestamps, etc.) are still included, only the four coordinate fields are dropped. When `include_document_bytes=False`, the service skips calling `_fetch_document_bytes` entirely (not just discarding the result after fetching) — document metadata rows are still included with `_content: None`, using the exact same "no payload" shape the ZIP builder already handles for a failed fetch, so no new branch was needed downstream.

## 4. Risk & impact on existing functionality

- **What else calls `gather_entity_bundle`/`gather_entity_bundles`?** Only `routes/admin/data_transfer_export.py`'s `_run_export_job` (grep-confirmed; `entity_import_service.py` only references the function name in a comment, doesn't call it).
- **Could this regress a working flow?** No — both new parameters default to `True`, exactly matching every prior call's implicit behavior. An export request that doesn't send these fields at all still gets full-fidelity output (backend Pydantic defaults + frontend API client defaults both are `True`).
- **Blast radius:** isolated to the export gather path. Import (`entity_import_service.py`) and the SGI PDF form-filler are untouched — neither reads ride GPS or document bytes through this service.
- **Downstream builders:** `bundle_zip_builder.py`/`tabular_writer.py` were grepped for hardcoded assumptions about `pickup_lat`/`pickup_lng`/etc. or about every document having `_content` — none found; both already handle a ride dict or document dict generically / handle `_content: None`.

## 5. User-experience effect

- **Who sees a difference:** internal admin only (super_admin).
- **Mid-session visible?** The Export tab now shows two new checkboxes ("Exact pickup/dropoff GPS coordinates", "Document file contents"), both checked by default — an admin who doesn't touch them gets identical behavior to before. Only admins who deliberately uncheck one see a difference, and that's the intended, visible, opt-in behavior change.
- **API contract change:** additive-only — `include_ride_gps`/`include_document_bytes` are optional request fields with defaults matching prior behavior; no existing caller breaks.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/data_transfer/entity_export_service.py` | `gather_entity_bundle`/`gather_entity_bundles` take new `include_ride_gps`/`include_document_bytes` params (default `True`); new `_strip_ride_gps` helper; document-bytes fetch conditional on the flag | Core data-minimization logic |
| `backend/routes/admin/data_transfer_export.py` | `ExportRequest` gains the two fields; threaded through `_run_export_job`, the background-task call, and `log_admin_action` metadata | Wire the request through to the service and record what scope was used in the audit trail |
| `backend/tests/test_entity_export_service.py` | 4 new tests: GPS-stripped-but-rows-kept, GPS-default-kept, document-bytes-skipped-storage-never-called, flags-threaded-through-batch-gather | Cover the new branches |
| `admin-dashboard/src/lib/api.ts` | `exportDataTransferEntities` takes a new `DataTransferExportScopeOptions` object (`docTypes`, `includeRideGps`, `includeDocumentBytes`) instead of a bare `docTypes` positional param | Backward-compatible-in-spirit API surface for the growing option set |
| `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx` | Two new checkboxes, both defaulting to checked (`true`) | Let an admin opt out per-export |

## 7. Before / after

```python
# Before
async def gather_entity_bundle(
    entity_type: str, entity_id: str, doc_types: Optional[list[str]] = None
) -> dict[str, Any]:
    ...
    doc_payloads = []
    for doc in documents:
        storage_key = _extract_storage_key(doc.get("document_url") or "")
        content = await _fetch_document_bytes(storage_key) if storage_key else None
        doc_payloads.append({**doc, "_storage_key": storage_key, "_content": content})
    return {..., "rides": rides or [], ...}
```

```python
# After
async def gather_entity_bundle(
    entity_type: str,
    entity_id: str,
    doc_types: Optional[list[str]] = None,
    include_ride_gps: bool = True,
    include_document_bytes: bool = True,
) -> dict[str, Any]:
    ...
    if not include_ride_gps:
        rides = [_strip_ride_gps(r) for r in rides]

    doc_payloads = []
    for doc in documents:
        storage_key = _extract_storage_key(doc.get("document_url") or "")
        content = await _fetch_document_bytes(storage_key) if (storage_key and include_document_bytes) else None
        doc_payloads.append({**doc, "_storage_key": storage_key, "_content": content})
    return {..., "rides": rides or [], ...}
```

## 8. Rollback plan

`git-revert-safe` — additive-only API, no schema change, no migration.

## 9. Verification performed

- [x] Automated tests: `tests/test_entity_export_service.py` (11/11, 4 new), full `pytest -k "data_transfer or entity_export"` (40 passed, 1 skipped, 0 failed).
- [x] `ruff check` clean on all changed/new backend files (one pre-existing, unrelated `B905` finding on an untouched line in `entity_export_service.py` confirmed present on `main` before this change — not introduced here, not fixed here to keep this commit scoped).
- [x] `tsc --noEmit`: 0 new errors in the 3 changed frontend files.
- [x] `npm run build` (real production build): completed successfully.
- [x] Blast-radius grep performed: confirmed `gather_entity_bundle(s)` has exactly one real caller; confirmed no downstream builder hardcodes assumptions about the fields being stripped/skipped.

## 10. What was NOT verified

- Not tested against a real Supabase Storage bucket or real ride rows with actual GPS data — only mocked `db_supabase.get_rows` per this test file's existing convention.
- Did not screenshot the two new checkboxes in a running browser.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data dependency).
- [x] Blast radius is stated, not assumed (§4, grep-verified).
- [x] No silent behavior change — defaults preserve prior behavior exactly; the only visible change is the new opt-in UI, stated explicitly in §5.
