# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-05 |
| Author | Claude Code (session-assisted) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin (SGI regulatory reporting) |
| PR / commit link | branch `claude/sgi-driver-export-issue-nkgvgk` (restarted from `main` after #3454 merged) |
| Related issue or gap ID | Operator: "the 2 PDF, one for driver and one for vehicle details, and the driver background criminal check jpeg or pdf, to be in zip" |

## 1. Issue / gap identified

Assembling an SGI filing took three separate downloads and manual repackaging: D00032 from one button, D00033 from the same button on a second pass, and the criminal record check from either the driver profile (one at a time) or the supporting-documents bundle (which returns per-driver folders, CSVs, and a README — far more than a filing needs, with the two files that matter buried inside).

Nothing produced the thing that actually gets emailed to SGI.

## 2. Root cause

Feature gap, not a defect. The tab's two capabilities were built for different jobs — `generate` fills forms, `documents` exports a data-transfer bundle — and neither is a submission. The operator was doing the assembly by hand every time.

## 3. Fix / remediation

New `POST /data-transfer/sgi-forms/package` returning exactly the filing, flat and predictably named:

```
SGI_Submission_20260805.zip
├─ SGI_D00032_Driver_Details.pdf
├─ SGI_D00033_Vehicle_Details.pdf
└─ criminal_record_checks/
   └─ Jane_Driver_background_check.pdf
```

Decisions worth recording:

- **Both forms in one call.** `_fill_both_forms` chunks each to its real row count (D00032 holds 10, D00033 holds 16), so a 12-driver selection yields `..._1.pdf` / `..._2.pdf` for D00032 and a single D00033 — mirroring what the tab already did client-side rather than refusing the selection.
- **Criminal record check only.** Not the five-type supporting set: a submission needs clearance proof, not the driver's whole file (PIPEDA data minimization).
- **Files are passed through untouched.** A JPEG check ships as a JPEG. The operator asked for "jpeg or pdf"; re-encoding a regulator-facing document would mean SGI receives something other than what the driver submitted. Explicitly *not* converting images to PDF.
- **Missing checks are named, not silently omitted.** A driver on the forms with no clearance file produces a `MISSING_CRIMINAL_RECORD_CHECKS.txt` entry inside the ZIP, `X-Checks-Missing` in the headers, a destructive toast, and an error log. The forms are still produced — the filing is flagged, not blocked, because the admin may be filing deliberately ahead of the clearance arriving.
- **`include_ride_gps=False`** — an eligibility filing has no business carrying trip coordinates.
- **`reason` required** (10–200 chars), matching the export route per PIA R-C.

## 4. Risk & impact on existing functionality

**Blast radius: additive.** One new endpoint; `generate_sgi_form` and `download_sgi_supporting_documents` are untouched and still work. `entity_export_service` / `sgi_form_filler` / `sgi_field_maps` are reused read-only, not modified.

The UI panel that previously offered "Download supporting documents (ZIP)" now offers the submission package instead. The supporting-documents **endpoint** remains, so nothing that calls it breaks — but it currently has no UI entry point. That is deliberate (the package is what operators actually need) and is called out here rather than left as a silent orphan; if the broader bundle is still wanted, the panel can carry both buttons.

Access control inherits `require_super_admin` from the router mount in `routes/admin/__init__.py`. Rate-limited with `data_transfer_export_limit` (10/hour) — same data, same threat model. The non-SGI regulator block from form generation applies here too: an Alberta driver cannot be assembled into an SGI package.

No schema, migration, background loop, or money/state-machine code.

## 5. User-experience effect

**Internal admin (super_admin) only.** No rider, driver, or corporate-admin impact; nothing visible mid-session in the apps.

The SGI Compliance Forms tab's second panel becomes "SGI submission package", showing the exact ZIP layout it will produce. One click yields a filing-ready archive. A missing clearance is reported in the toast, in the headers, and inside the ZIP.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/sgi_forms.py` | `SGI_PACKAGE_DOC_TYPE`, `SgiPackageRequest`, `_safe_filename_part`, `_fill_both_forms`, `download_sgi_submission_package`; `io`/`re`/`zipfile` imports | The submission package |
| `backend/tests/test_sgi_forms_route.py` | 9 tests | ZIP contents, JPEG passthrough, missing-check reporting, doc-type narrowing, non-SGI block, reason, empty selection, auth, row-limit splitting |
| `admin-dashboard/src/lib/api/data-transfer.ts` | `downloadSgiSubmissionPackage`, `SgiPackageResult` | Binary response needs the manual authed-fetch pattern |
| `admin-dashboard/src/lib/api.ts` | Re-exports | Barrel |
| `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx` | Panel retitled and repointed; `onDownloadPackage` replaces `onDownloadDocuments` | The UI |

## 7. Before / after

```
# Before — three downloads, manual assembly
click Generate (D00032 checked)          -> SGI_D00032_Driver_Details.pdf
click Generate (D00033 checked)          -> SGI_D00033_Vehicle_Details.pdf
click Download supporting documents      -> driver_<id>/documents.csv
                                            driver_<id>/rides.csv
                                            driver_<id>/raw_data.json
                                            driver_<id>/documents/background_check_*.pdf
                                            ... + 4 other document types
                                         -> then unzip, dig out the check, repackage

# After — one click
click Download SGI submission package    -> SGI_Submission_20260805.zip
                                            ├─ SGI_D00032_Driver_Details.pdf
                                            ├─ SGI_D00033_Vehicle_Details.pdf
                                            └─ criminal_record_checks/
                                               └─ Jane_Driver_background_check.pdf
```

## 8. Rollback plan

`git revert` is a complete rollback. Purely additive: no migration, no schema change, no live-data mutation, no existing endpoint's behavior altered. The only outputs are on-demand ZIPs.

The halves revert independently — reverting the UI leaves an unused endpoint (still gated and rate-limited); reverting the backend leaves a button that errors, so revert the UI first or both together. Reverting the UI alone also restores the supporting-documents button.

## 9. Verification performed

- [x] **Automated tests run.** `test_sgi_forms_route.py` 28 → **37 passed**. Full backend suite run separately.
- [x] **Real production build run** — `npm run build` exits 0. `tsc --noEmit` clean for the changed files; eslint 0 errors (the one warning is pre-existing on an untouched line).
- [x] **Backend lint/format** — `ruff check routes/admin/sgi_forms.py` clean; `ruff format` applied.
- [x] **ZIP contents asserted, not assumed** — tests unzip the response and check exact member names, that a JPEG stays a JPEG byte-for-byte, and that no `.csv` / `raw_data.json` / `README.txt` is present.
- [x] **Row-limit splitting exercised** with a 12-driver selection, confirming D00032 splits into two documents while D00033 stays one.
- [x] **Access control verified by reading the mount**, not assumed — `sgi_forms_router` carries `Depends(require_super_admin)`; a test asserts unauthenticated calls are rejected.

## What was NOT verified

- **Not tested against live Supabase.** Storage and DB are mocked throughout; no real ZIP was produced from a real bucket. `X-Checks-Included` on first real use is what confirms the checks resolve.
- **Not validated against an SGI submission checklist.** That the filing is "D00032 + D00033 + criminal record check" comes from the operator's description, not from an SGI document enumerating required attachments. If SGI also expects the abstract or licence, `SGI_PACKAGE_DOC_TYPE` becomes a list — a one-line change.
- **PDF form filling is mocked in these tests.** `fill_driver_details_form` / `fill_vehicle_details_form` are patched to fixed bytes, so the tests cover packaging, not that the PDFs are correctly filled — that is `test_sgi_form_filler.py`'s job and is unchanged.
- **No frontend test** for the new panel; `SgiFormsTab.tsx` has no test file. Covered by the production build and inspection.
- **The supporting-documents endpoint is now UI-orphaned** (see §4). Left in place deliberately, but nothing exercises it from the dashboard.
- **The 25-driver cap is unchanged and still unmeasured** — no load testing on package size or response time, and this endpoint now also embeds two PDFs.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — additive, with the orphaned endpoint called out
- [x] No silent behavior change: the panel swap is user-visible and documented in §5
