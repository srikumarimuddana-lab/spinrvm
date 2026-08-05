# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-05 |
| Author | Claude Code (session-assisted) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin (drivers/compliance-adjacent) |
| PR / commit link | branch `claude/sgi-driver-export-issue-nkgvgk` |
| Related issue or gap ID | Reported in-session: "Records & Compliance driver background/criminal document export produces a ZIP with metadata but no images" |

## 1. Issue / gap identified

Exporting a driver's Background Check document from **Records & Compliance → Search & Transfer → Export** produced a ZIP containing `documents.csv` metadata rows but no document files (scans/images/PDFs). Reported as a broken export.

The export was not broken — it was doing exactly what was asked, and there was no way to tell that from either the UI after the fact or the ZIP itself.

## 2. Root cause

Two compounding causes:

1. **`ExportTab.tsx` defaults `includeDocumentBytes` to `false`** (PIPEDA data minimization, PIA recommendation R-B / ACTION_ITEMS.md B11 — a deliberate, correct default). Nothing in the UI connects that unchecked box to the "Documents to include" checkboxes directly above it, so checking `background_check` reads as "give me the background check" while the export silently ships metadata only.
2. **The ZIP could not distinguish an opt-out from a failure.** `bundle_zip_builder` skipped any document with no bytes (`if not content: continue`) and `entity_export_service` set `_content = None` identically for *all* of: admin opted out, `document_url` unparseable, and storage download failed. All three produced a byte-identical metadata-only ZIP. Worse, `README.txt` unconditionally advertised `documents/<original filename>  Document files in their original format`, so the bundle actively promised files it had not written.

A genuine storage fault would have looked exactly the same as the reported case — this diagnosis is only possible now because the fix makes the two distinguishable.

## 3. Fix / remediation

Kept the privacy-preserving default OFF; removed the silence around it.

- **Backend**: every document payload now carries `_content_status` (`included` / `excluded_by_request` / `unavailable_no_storage_key` / `unavailable_fetch_failed`). Surfaced in `documents.csv` and `raw_data.json` as `file_export_status`, plus a `bundled_file` column pointing at the file inside the entity folder. `README.txt` now reports actual counts, explains the metadata-only case and how to re-run for files, flags unretrievable documents as a fault, and no longer lists a `documents/` section when it wrote no files.
- **Frontend**: an inline amber warning appears whenever a ZIP export has document types checked but "Document file contents" off, with a one-click "Include them"; the "Export ready" toast repeats the caveat at download time.
- **Observability**: storage fetch failures and unparseable `document_url`s moved from `logger.warning`+continue to `logger.error` (CLAUDE.md "do not silently swallow errors"). The bundle still continues rather than aborting — one bad object must not lose the other 99 entities — but the miss is now recorded in the manifest instead of dropped.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface (backend export + admin UI), but confined to the Data Transfer export/import path. No ride, dispatch, payment, wallet, auth, or insurance-period code touched. No DB schema or migration. No background-loop interaction.**

Grepped for every reader of the changed bundle fields (`_storage_key`, `_content_status`, `_document_manifest`, `documents.csv`, `raw_data.json`):

| Consumer | Effect |
|---|---|
| `services/data_transfer/bundle_document_uploader.py` | **Real regression caught during the blast-radius grep.** It derives each document's file extension from `doc["_storage_key"]` in `raw_data.json`. An earlier draft of this change stripped `_storage_key` from the manifest as an "internal key" — that would have made every import fall back to `.bin`, fail the `ALLOWED_EXTENSIONS` check, and **silently skip every document on import**. `_storage_key` is therefore deliberately retained (now documented as an export/import contract in `_INTERNAL_DOC_KEYS`), and the uploader additionally prefers the new `bundled_file` column with `_storage_key` as fallback. |
| `services/data_transfer/entity_import_service.py` | Reads `raw_data.json` `documents[]` and `documents/` files. Both keep their existing shape; the two new columns are additive and ignored by it. Round-trip verified by existing tests. |
| `routes/admin/data_transfer_export.py` | Unchanged. Calls `builder(bundles)` with one argument — status is carried on the payload rather than a new builder parameter specifically so this call shape (shared with the csv/json/excel builders in `_FORMAT_BUILDERS`) did not have to change. |
| `routes/drivers/tax_exports.py` (DSAR self-export) | Separate builder, separate manifest. Not touched, not affected. |
| `documents.py::_extract_storage_key` | Read-only use; unchanged. |

Regression risk considered and rejected: `_rows_to_csv` derives its header from the union of all row keys, so adding two columns cannot drop existing ones. Older bundles exported before this change have no `_content_status` and fall back to inferring status from `_content`, so they never mis-report an absent file as `included`.

## 5. User-experience effect

**Internal admin (super_admin) only.** Riders, drivers, and corporate admins see nothing. Not visible mid-session to anyone using the rider/driver apps.

For the internal admin on the Export tab:
- New amber inline warning + "Include them" shortcut when the selection would produce a metadata-only ZIP.
- The "Export ready" toast gains a caveat sentence in that same case.
- `README.txt` and `documents.csv` inside the ZIP gain explanatory text and two columns.

Copy is specific, non-technical, and actionable ("This ZIP will contain document metadata only — no scans, images, or PDFs"), naming the exact control to change.

**No change to what data any export contains.** Same defaults, same bytes, same PIPEDA posture — only the explanation is new.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/data_transfer/entity_export_service.py` | Added `DOC_STATUS_*` constants; per-document `_content_status`; `warning`→`error` on fetch failure and unparseable `document_url` | Make the reason for a missing file explicit and surface storage faults loudly |
| `backend/services/data_transfer/bundle_zip_builder.py` | `file_export_status` + `bundled_file` manifest columns; conditional, count-reporting README; extracted `_document_file_path` | Make the bundle self-describing; stop promising files it did not write |
| `backend/services/data_transfer/bundle_document_uploader.py` | Extension resolution prefers `bundled_file`, falls back to `_storage_key` | Harden the round-trip against the manifest shape change |
| `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx` | `metadataOnlyDocuments` state warning + "Include them" action + toast caveat | Fix the actual reported confusion at the point of decision and again at download |
| `backend/tests/test_data_transfer_zip_builder.py` | 5 new regression tests | Lock the opt-out/fault distinction and the `_storage_key` import contract |

## 7. Before / after

```python
# Before — entity_export_service.py: opt-out and storage failure are indistinguishable
content = await _fetch_document_bytes(storage_key) if (storage_key and include_document_bytes) else None
doc_payloads.append({**doc, "_storage_key": storage_key, "_content": content})

# After — the reason is recorded and travels into the manifest
if not include_document_bytes:
    content, status = None, DOC_STATUS_EXCLUDED
elif not storage_key:
    logger.error("data-transfer export: no storage key for document id=%s ...", doc.get("id"), ...)
    content, status = None, DOC_STATUS_NO_KEY
else:
    content = await _fetch_document_bytes(storage_key)
    status = DOC_STATUS_INCLUDED if content is not None else DOC_STATUS_FETCH_FAILED
doc_payloads.append({**doc, "_storage_key": storage_key, "_content": content, "_content_status": status})
```

```
# Before — README.txt always claimed files were present
  documents/<original filename>    Document files in their original format

# After — counts, and the metadata-only case named outright
Documents: 1 listed, 0 file(s) included in this ZIP.

NOTE: 1 document(s) are listed as metadata only, with NO file in this ZIP,
because "Document file contents" was not enabled for this export. That checkbox is
OFF by default for PIPEDA data minimization. To get the actual files (scans/images/PDFs),
run the export again with "Document file contents (not just metadata)" checked under
"Data to include".
```

## 8. Rollback plan

No feature flag, no migration, no live-data mutation — this change writes nothing to the database and alters no stored record. A `git revert` **is** a complete rollback here: the only artifacts produced are ZIP files generated on demand, and previously-generated ZIPs are unaffected (they are static objects in the `data-transfer-exports` bucket, purged on the existing 7-day schedule).

Partial rollback is available without touching the backend: reverting `ExportTab.tsx` alone restores the previous UI while keeping the self-describing bundle, and vice versa. The two halves are independent.

## 9. Verification performed

- [x] **Automated tests run.** `pytest backend/tests/ -k "data_transfer or bundle or export or sgi or document"` → **440 passed, 1 skipped**. Includes 5 new unit tests in `test_data_transfer_zip_builder.py` covering: opt-out vs. fetch-failure distinguishability, README metadata-only explanation, README fault warning, manifest→file path resolution, and included-file counts.
- [x] **Real production build run** — `cd admin-dashboard && npm run build` (Next.js production build) exits 0. Not a dev server, not `tsc --noEmit` alone. `npx eslint ExportTab.tsx` clean. Admin-dashboard vitest suite: 160 passed / 20 files.
- [x] **Backend lint/format** — `ruff format` applied to changed files; `ruff check` on the changed directory reports only 2 pre-existing errors on lines this change does not touch (`entity_import_service.py:141` F841, `entity_export_service.py:197` B905). Repo-wide `ruff check .` reports 35 pre-existing errors, so this is not a clean gate today — no new errors introduced.
- [x] **Blast-radius grep performed** — searched all non-test Python for `_storage_key`, `_content_status`, `_document_manifest`, `documents.csv`, `raw_data.json`, `bundled_file`, `file_export_status`. This is what caught the `bundle_document_uploader.py` extension regression before it shipped (see §4).
- [x] **Reviewed against CLAUDE.md conventions** — "do not silently swallow errors" (warning→error on storage/data faults), "additive over destructive" (columns added, none removed or repurposed), PIPEDA data minimization (default-off preserved deliberately; no change to exported data).
- [x] **Feature flag** — not applied. Justification: internal-admin-only, additive explanatory copy, no change to exported data or to any default. The repo's flag guidance targets user-visible/shared-component changes; `ExportTab.tsx` has exactly one consumer (the Data Transfer page, itself embedded once in Records & Compliance).

## What was NOT verified

- **Not tested against live Supabase.** No export was run end-to-end against a real `driver-documents` bucket. All backend verification is unit-level against constructed bundle dicts; `_fetch_document_bytes` and the storage download path were not exercised against real storage. Specifically, the `unavailable_fetch_failed` and `unavailable_no_storage_key` branches are covered at the ZIP-builder layer but their *triggering* in `entity_export_service` was not observed against a real bucket.
- **The user's original failing export was not reproduced against their data.** The diagnosis (opt-out, not fault) is inferred from the code path and the default value, and is consistent with the report. If their `background_check` documents *also* have unreadable `document_url`s, that is a second, separate defect this change does not fix — but it now reports it: after this change their ZIP's `documents.csv` will say `excluded_by_request` (setting) or `unavailable_*` (fault), which settles it definitively on the next export.
- **No visual regression tooling exists for admin-dashboard**, so the new warning banner's appearance — including its dark-mode variant — was reasoned about against sibling components' Tailwind classes, not screenshotted or snapshot-tested. This is the standing gap already noted in CLAUDE.md release gate #6.
- **No new frontend unit test** for the warning's render condition; the `metadataOnlyDocuments` logic is covered by inspection and the production build only. `ExportTab.tsx` has no existing test file to extend.
- **Import round-trip not executed end-to-end.** The `bundle_document_uploader` fallback is covered by existing tests passing plus the grep, not by an export→import cycle against two live environments.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`; no live-data effects; independently revertible halves)
- [x] Blast radius is stated, not assumed (grep-derived consumer table in §4, including one regression it caught)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 — the change is *additive explanation*; exported data is byte-identical)
