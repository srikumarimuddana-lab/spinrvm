# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-05 |
| Author | Claude Code (session-assisted) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin (SGI regulatory reporting) |
| PR / commit link | branch `claude/sgi-driver-export-issue-nkgvgk` |
| Related issue or gap ID | Operator: "I'm saying about the SGI compliance export — I'm trying to export the image for the drivers" |

## 1. Issue / gap identified

An operator repeatedly could not export driver document scans from the **SGI Compliance Forms** tab. Several rounds of investigation went into the *Export* tab's document-bytes path instead.

**The actual gap: the SGI Compliance Forms tab has no document-export capability at all, and never had one.** `POST /data-transfer/sgi-forms/generate` fills the D00032/D00033 PDFs and returns a single PDF; it never reads `driver_documents`. An SGI submission needs the forms **and** the supporting evidence, and there was no way to get the second half from that tab.

This was a missing feature, not a defect. It was misdiagnosed for several rounds because the symptom ("export gives me no images") is identical to the Export tab's metadata-only bug that was live at the same time — a genuinely different problem on a different code path, now also fixed.

## 2. Root cause

Feature never built. The Data Transfer module split document export (Export tab) from regulator forms (SGI tab), and nothing bridged them, even though an SGI filing needs both. The two tabs share a driver selection (`useEntitySelection`), so the split is not visible from the UI — the same selection produces documents on one tab and only PDFs on the other, with nothing saying so.

## 3. Fix / remediation

New endpoint `POST /data-transfer/sgi-forms/documents` returning a ZIP of the selected drivers' supporting scans, plus a **Supporting documents** panel on the SGI tab that downloads it.

It reuses the Data Transfer export pipeline (`entity_export_service.gather_entity_bundles` → `bundle_zip_builder.build_export_zip`) rather than re-implementing document fetching. That path already resolves storage keys across every stored URL shape, records a per-document `file_export_status` so a missing scan is explained rather than silently absent, and carries the module's test coverage. A second implementation would have re-acquired every bug fixed on the first this week.

Deliberate scoping decisions:

- **Document types are narrowed** to the SGI eligibility set (licence, abstract, criminal record check, vehicle inspection, insurance) rather than everything on file — PIPEDA data minimization; a regulator package is not the driver's whole file. Overridable per request via `doc_types`.
- **`include_ride_gps=False`** — an eligibility submission has no business carrying trip coordinates.
- **`reason` is required** (10–200 chars), matching `/data-transfer/export` (PIA R-C). This moves real identity documents; there must be a contemporaneous record of why.
- **Returned inline, not backgrounded**, unlike `/data-transfer/export` — bounded by `MAX_DOCUMENT_BUNDLE_DRIVERS = 25` (D00032 holds 10 driver rows, D00033 holds 16, so 25 covers the largest single filing with headroom).
- **Counts returned in `X-Documents-Listed` / `X-Documents-Included` headers**, so the tab can say "12 of 14 included" without unzipping and a shortfall is stated outright — this is going to a regulator.

## 4. Risk & impact on existing functionality

**Blast radius: additive.** One new endpoint, one new UI panel, one new API client function. No existing endpoint, model, or component changed behavior.

| Touched | Effect |
|---|---|
| `routes/admin/sgi_forms.py` | New endpoint appended; `generate_sgi_form` untouched. New imports (`bundle_zip_builder`, `entity_export_service`, `data_transfer_export_limit`, `Request`) — all already used elsewhere in the module, no new dependency. |
| `entity_export_service` / `bundle_zip_builder` | **Read-only reuse; not modified.** A second caller of `gather_entity_bundles`, using the same parameters the Export tab already passes. |
| `SgiFormsTab.tsx` | New panel below the existing button; `runGenerate`/`onGenerate`/removal queue untouched. |
| `resolveDriverIds` (`SgiFormsTab.tsx`) | **Signature changed** — gained an optional `pageSize` defaulting to `MAX_ROW_LIMIT`. Existing callers pass nothing and behave identically. Needed because the forms cap at a form's row count (16) while the bundle allows 25; passing the form limit would have silently under-fetched drivers for a document download. |

Access control inherits `require_super_admin` — `sgi_forms_router` is mounted with it in `routes/admin/__init__.py`, so the new route is gated identically to every other Data Transfer route without a per-handler check. Rate-limited with `data_transfer_export_limit` (10/hour), the same limiter guarding the export route, since it moves the same data.

The out-of-scope (non-SGI) driver block from form generation is applied here too — an Alberta driver's documents must not be assembled into an SGI package either.

No schema, migration, background loop, ride/dispatch/payment/auth/safety code involved.

## 5. User-experience effect

**Internal admin (super_admin) only.** No rider, driver, or corporate-admin impact; nothing visible mid-session in the apps.

On Records & Compliance → Search & Transfer → SGI Compliance Forms, a new **Supporting documents** panel: a required reason field and a "Download supporting documents (ZIP)" button. Disabled with an explicit sentence when no drivers are selected or the reason is too short. Copy states plainly that the forms above are PDFs only and what the ZIP contains.

A shortfall is reported as a destructive toast naming the counts and pointing at `file_export_status` in the ZIP, rather than letting the admin discover a missing scan when the regulator asks.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/sgi_forms.py` | `SGI_SUPPORTING_DOC_TYPES`, `MAX_DOCUMENT_BUNDLE_DRIVERS`, `SgiDocumentBundleRequest`, `download_sgi_supporting_documents` | The missing capability |
| `backend/tests/test_sgi_forms_route.py` | 9 tests | ZIP contents, files-not-just-metadata, missing-scan counts, reason, empty selection, batch cap, non-SGI block, 404, auth |
| `admin-dashboard/src/lib/api/data-transfer.ts` | `downloadSgiSupportingDocuments`, `SgiSupportingDocumentsResult` | Binary response needs the manual authed-fetch pattern, like `generateSgiForm` |
| `admin-dashboard/src/lib/api.ts` | Re-exports | Barrel |
| `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx` | Supporting-documents panel, `onDownloadDocuments`, `resolveDriverIds` gains `pageSize` | The UI, and the truncation fix that came with it |

## 7. Before / after

```python
# Before — the SGI tab's only endpoint. Returns a PDF; never reads driver_documents.
@router.post("/data-transfer/sgi-forms/generate")
async def generate_sgi_form(body: SgiFormRequest, admin=Depends(get_admin_user)):
    ...
    return Response(content=pdf_bytes, media_type="application/pdf", ...)

# After — the evidence half of the submission, reusing the export pipeline.
@router.post("/data-transfer/sgi-forms/documents")
@data_transfer_export_limit
async def download_sgi_supporting_documents(body: SgiDocumentBundleRequest, request: Request = None, ...):
    bundles = await entity_export_service.gather_entity_bundles(
        [("driver", uid) for uid in body.driver_ids],
        doc_types=doc_types,
        include_ride_gps=False,   # eligibility package, not trip history
        doc_file_types=doc_types, # files, not just metadata rows
    )
    return Response(content=bundle_zip_builder.build_export_zip(bundles), media_type="application/zip", ...)
```

## 8. Rollback plan

`git revert` is a complete rollback. The change is purely additive: no migration, no schema change, no live-data mutation, no modification to an existing endpoint's behavior. Reverting removes a button and a route; nothing that exists today depends on either.

The backend and frontend halves revert independently — reverting the UI leaves an unused endpoint (harmless, still gated and rate-limited); reverting the backend leaves a button that errors, so prefer reverting the UI first or both together.

## 9. Verification performed

- [x] **Automated tests run.** `test_sgi_forms_route.py` 19 → **28 passed**; `test_admin_sgi_forms_coverage.py` unchanged and passing. Full backend suite run separately.
- [x] **Real production build run** — `cd admin-dashboard && npm run build` exits 0. `npx tsc --noEmit` clean for both changed files (pre-existing errors elsewhere are in unrelated test files). `npx eslint` 0 errors.
- [x] **Backend lint/format** — `ruff check routes/admin/sgi_forms.py`: "All checks passed"; `ruff format` applied.
- [x] **The regression that caused this whole investigation is explicitly guarded**: `test_documents_endpoint_requests_files_not_just_metadata` asserts `doc_file_types` is populated, so a future change that requests documents without their bytes fails the build rather than shipping another metadata-only ZIP.
- [x] **Access control verified by reading the mount**, not assumed — `sgi_forms_router` carries `Depends(require_super_admin)` in `routes/admin/__init__.py`, so the new route inherits it. A test asserts unauthenticated calls are rejected.

## What was NOT verified

- **Not tested against live Supabase.** All storage interaction is mocked; no real ZIP was produced from a real bucket. Whether the operator's specific drivers have retrievable scans is still unconfirmed — the `X-Documents-Included` header and the ZIP's `file_export_status` are what will answer that on first real use.
- **The operator's original failure is still not root-caused with evidence.** They report the scans are viewable in the driver table, which implies the storage key resolves and the object exists — so the earlier `_extract_storage_key` fixes, while real defects, were probably not their bug. This change gives them the capability they were asking for; it does not prove what the old path was doing.
- **No frontend test for the new panel.** `SgiFormsTab.tsx` has no test file; the panel, its disabled states, and the `resolveDriverIds` page-size change are covered by the production build and inspection only. The backend equivalents are tested.
- **The 25-driver cap is a judgement call, not a measured limit.** No load testing was done on bundle size or response time; a 25-driver bundle of large scans could be slow enough to warrant backgrounding like `/data-transfer/export`. Worth revisiting if operators hit it.
- **Not verified against a real SGI submission checklist.** `SGI_SUPPORTING_DOC_TYPES` is inferred from the driver-eligibility rules in CLAUDE.md's Saskatchewan Regulatory section, not from an SGI document that enumerates required attachments. If SGI expects a different set, the list needs correcting — it is a single constant.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — additive, with the one signature change called out
- [x] No silent behavior change to an existing flow; the new capability is documented in §5
