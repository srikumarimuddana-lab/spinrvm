# Change Impact & Risk Log — Data Transfer module: export tab UI + format dispatch (Phase 4.1)

## Issue/gap identified
1. Phase 1's export route only ever built a ZIP — `body.format` didn't exist,
   so `tabular_writer.py`'s CSV/Excel/JSON writers (Phase 1.3) were dead code
   with no way to select them.
2. No UI existed to trigger an export at all.
3. (Found and fixed mid-subtask) `useEntitySelection` only stored bare row
   IDs, with no `entity_type` — but the export payload needs
   `{entity_type, entity_id}` per record, and a search scoped to "all"
   returns a mix of driver/rider rows with no reliable way to recover which
   is which from an ID alone.

## Root cause
1/2: deliberate phasing. 3: an oversight in Phase 3.2's initial design,
caught while building the component that actually needs typed refs.

## Fix/remediation
- Modified `backend/routes/admin/data_transfer_export.py`: `ExportRequest`
  gains a `format: "zip"|"csv"|"json"|"excel"` field (defaults to `"zip"`,
  preserving the existing behavior for any caller that omits it). A
  `_FORMAT_BUILDERS` dict maps each format to its writer function
  (`bundle_zip_builder.build_export_zip` or the three `tabular_writer`
  functions), file extension, and content-type; the route now calls
  `builder(bundles)` generically instead of hardcoding the ZIP path.
  `_upload_bundle` takes the file bytes/extension/content-type as
  parameters instead of assuming `.zip`/`application/zip`.
- Modified `admin-dashboard/src/components/data-transfer/useEntitySelection.ts`:
  changed `selectedIds: Set<string>` to `selectedRefs: Map<string,
  DataTransferExportEntityRef>`, storing the resolved `{entity_type,
  entity_id}` per row at selection time via a new `inferEntityType()`
  helper (driver-scoped search results have no `role` column so default to
  `"driver"`; rider/all-scoped results carry `role` from the `users` table,
  which is authoritative per CLAUDE.md's JWT-trust-model rule that role is
  always read from the `users` table). `toggle`/`toggleAll` now take the
  full row instead of a bare ID.
- Modified `admin-dashboard/src/components/data-transfer/EntitySearchTable.tsx`:
  one-line update to pass `row` instead of `row.id` to `selection.toggle`.
- Modified `admin-dashboard/src/lib/api.ts`: added `exportDataTransferEntities`
  wrapper + `DataTransferExportFormat`/`DataTransferExportEntityRef`/
  `DataTransferExportResult` types.
- New `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx`: format
  picker, per-document-type checkboxes (ZIP only), and the resolve+export
  flow. For "select all matching filter," `resolveSelection()` re-queries
  the search endpoint with the stored filter (capped at the same
  `MAX_ENTITIES_PER_EXPORT=100` the backend enforces) since the backend
  export route takes explicit entity refs, not a filter descriptor — the UI
  warns the admin if the filter matches more than the cap so they know to
  split into batches rather than silently truncating.
- Modified `admin-dashboard/src/app/dashboard/data-transfer/page.tsx`: wires
  `ExportTab` into the Export tab (replacing the placeholder).

## Risk & impact on existing functionality
Blast radius on the backend route: grepped for other callers of
`export_entities`/`_upload_bundle` — none exist yet (no frontend called this
route before this commit), so widening `ExportRequest` and
`_upload_bundle`'s signature breaks nothing. Default `format="zip"`
preserves the exact prior behavior for any caller that doesn't set it.
Blast radius on `useEntitySelection`: it's a new hook (Phase 3.2, same PR)
with exactly one consumer so far (`EntitySearchTable`), also modified in
this same commit to match the new signature — both files are updated
together so there's no split-brain state where one expects the old API.
`api.ts` again gets only additive exports.

## User experience effect
None for existing users — Export tab is reachable only via the still-unlinked
`/dashboard/data-transfer` URL (nav wiring is Phase 6.1).

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/routes/admin/data_transfer_export.py` | Format dispatch (`zip`/`csv`/`json`/`excel`) via `_FORMAT_BUILDERS` | Wire Phase 1.3's tabular writers into the route |
| `admin-dashboard/src/components/data-transfer/useEntitySelection.ts` | `selectedIds: Set<string>` → `selectedRefs: Map<string, EntityRef>`; `toggle`/`toggleAll` take full rows | Export needs `entity_type` per selection, not just IDs |
| `admin-dashboard/src/components/data-transfer/EntitySearchTable.tsx` | `selection.toggle(row.id)` → `selection.toggle(row)` | Match the new hook signature |
| `admin-dashboard/src/lib/api.ts` | +export wrapper + types (additive) | Typed client for the export route |
| `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx` | New: export UI | Format/doc-type selection + trigger |
| `admin-dashboard/src/app/dashboard/data-transfer/page.tsx` | Wires `ExportTab` into the Export tab | Replace placeholder |

## Before/after snippet
```python
# before
zip_bytes = bundle_zip_builder.build_export_zip(bundles)
signed_url, storage_path = await _upload_bundle(admin_id, zip_bytes)

# after
builder, ext, content_type = _FORMAT_BUILDERS[body.format]
...
file_bytes = builder(bundles)
signed_url, storage_path = await _upload_bundle(admin_id, file_bytes, ext, content_type)
```
```typescript
// before
toggle: (id: string) => void;
// after
toggle: (row: DataTransferEntityRow) => void;  // resolves + stores entity_type at selection time
```

## Rollback plan
Backend: revert `data_transfer_export.py` to the ZIP-only version (git revert
safe — no other caller depends on the `format` field yet). Frontend: revert
`useEntitySelection.ts`/`EntitySearchTable.tsx` together (they must move as a
pair) and delete `ExportTab.tsx`; revert `page.tsx`'s Export tab back to the
placeholder.

## Verification performed
- `python3 -m py_compile` on the modified backend route — passes.
- `npx tsc --noEmit -p tsconfig.json` across the whole admin-dashboard
  project — zero errors attributable to any file this subtask touched
  (grep-confirmed against the file names).
- Manually traced every call site of `selection.toggle`/`selection.toggleAll`/
  `selection.selectedIds` across the two files that use the hook
  (`EntitySearchTable.tsx`, the new `ExportTab.tsx`, and `page.tsx`) to
  confirm none still reference the old `Set<string>` shape after the rename.
- Verified `_FORMAT_BUILDERS`' four builder functions
  (`build_export_zip`/`write_csv`/`write_json`/`write_excel`) all share the
  same `(bundles) -> bytes` signature so the generic `builder(bundles)` call
  is valid for all four.

## What was NOT verified
- Not run in a browser — the export flow (format selection, doc-type
  checkboxes, "select all matching" resolution + truncation warning, actual
  file download) is untested against a live backend.
- The Excel/CSV/JSON export paths were never exercised end-to-end (no
  running backend with `openpyxl` installed and a real Supabase bucket in
  this session) — only the dispatch logic and existing writer functions were
  reasoned about separately, not together as a live request.
- `inferEntityType`'s fallback-to-"driver" behavior for rows with no `role`
  column is correct by construction (driver-scoped search never returns a
  `role` column) but has no unit test covering that inference explicitly.
