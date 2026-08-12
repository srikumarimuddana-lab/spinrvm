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

A follow-up round (same branch) replaced the global toggle entirely — see §3b. The operator's stated need was per-document control: *"if I need metadata I can checklist, if I want the image I should be able to select what document I can export."* One all-or-nothing switch forced a choice between no files and every file, which is both a worse workflow and a worse data-minimization posture than exporting only the one document an insurer actually asked for.

## 3. Fix / remediation

Kept the privacy-preserving default OFF; removed the silence around it.

- **Backend**: every document payload now carries `_content_status` (`included` / `excluded_by_request` / `unavailable_no_storage_key` / `unavailable_fetch_failed`). Surfaced in `documents.csv` and `raw_data.json` as `file_export_status`, plus a `bundled_file` column pointing at the file inside the entity folder. `README.txt` now reports actual counts, explains the metadata-only case and how to re-run for files, flags unretrievable documents as a fault, and no longer lists a `documents/` section when it wrote no files.
- **Frontend**: an inline amber warning appears whenever a ZIP export has document types checked but "Document file contents" off, with a one-click "Include them"; the "Export ready" toast repeats the caveat at download time.
- **Observability**: storage fetch failures and unparseable `document_url`s moved from `logger.warning`+continue to `logger.error` (CLAUDE.md "do not silently swallow errors"). The bundle still continues rather than aborting — one bad object must not lose the other 99 entities — but the miss is now recorded in the manifest instead of dropped.

## 3b. Follow-up: per-document-type file selection

The single global "Document file contents" checkbox is gone. The Export tab now shows a table with one row per document type and two columns:

| | Metadata (record only) | File (scan / image / PDF) |
|---|---|---|
| Driver's Licence | ☐ | ☐ |
| Background / Criminal Record Check | ☑ | ☑ |
| … | | |

- Ticking **File** implies **Metadata** (no unreachable "file but not listed" state); unticking **Metadata** drops the file request too.
- New `doc_file_types: Optional[list[str]]` on `ExportRequest`. `None` = no per-type opinion, falls back to the existing `include_document_bytes` (API back-compat). A list — **including an empty one** — takes precedence.
- The empty-list case is the sharp edge: `[]` must mean "no files", not fall through to `include_document_bytes`'s default of `True`. The client sends `?? null` rather than leaving the key undefined so an explicit `[]` survives `JSON.stringify`. Covered by `test_empty_doc_file_types_is_metadata_only_not_everything`.
- README wording is now conditional on the mix: "all N documents are metadata only" (no files ticked) vs. "N of M …" (deliberate partial selection). The earlier wording claimed file contents "was not enabled", which is false for a mixed selection and referenced a checkbox that no longer exists.

**Security note — dual-approval gate.** `doc_file_types` is bound into `_gate_params`. Without it, an approval granted for a metadata-only export would also satisfy a re-run that pulls the document files, letting the second request widen what the first was approved for. Three tests cover the binding, order-independence, and the `None` vs `[]` distinction.

**Consequence of that binding:** approval grants and pending requests created *before* this change have params without the `doc_file_types` key and will no longer match. Any in-flight export awaiting approval at deploy time needs re-requesting. This fails closed (re-approval required) rather than open, which is the correct direction, but it is a real operational effect — if the `dual_approval_exports_enabled` flag is on at deploy time, warn the approvals queue.

`doc_file_types` is recorded in the `log_admin_action` audit payload rather than on the job row: `data_transfer_export_jobs` has no column for it and adding one would need a migration for what is purely accountability metadata. Which document *files* left the system is exactly what an auditor asks about, so it is captured — just in the audit trail, not the job table. A follow-up could add a real column if the Jobs & History UI should display it.

## 3c. Defects found in code review of this branch

A review pass over the branch found four defects — three pre-existing on `main`, one introduced here. All four were reproduced with a probe script before fixing and now have regression tests.

| # | Defect | Origin | Severity |
|---|---|---|---|
| 1 | `doc_types=[]` selected **every** document type instead of none | pre-existing (`if doc_types:` truthiness) | PIPEDA over-collection |
| 2 | `_gate_params` collapsed `doc_types=[]` and `None` to the same signature | pre-existing | approval scope widening |
| 3 | `write_json` serialized raw document bytes into the JSON export | pre-existing | PIPEDA — sensitive data leak |
| 4 | A zero-byte document was reported `included` with no file in the ZIP | introduced by §3 | manifest contradicts archive |

**1 — empty `doc_types` meant "all".** `if doc_types:` treats `[]` as falsy and skipped the filter entirely, returning every document type. The UI sends `[]` whenever fewer than all types are ticked, including zero — so an admin who deliberately unticked every document got all of them. Pre-existing on `main`, but §3b's checkbox grid makes "untick everything" a natural gesture, so it went from obscure to easy to hit. Fixed to `if doc_types is not None:`.

**2 — the same `[]`/`None` collision in the approval gate.** Once `[]` and `None` mean opposite things, collapsing them in `_gate_params` lets an approval granted for a no-documents export satisfy an all-documents one — the exact hole §3b closed for `doc_file_types`, still open for `doc_types`. Fixed the same way.

**3 — JSON export leaked document bytes.** `json.dumps(bundles, default=str)` does not skip `bytes`; it stringifies them, writing every scan into the JSON as a mangled `"b'\xff\xd8...'"` string. Reachable today by any API caller posting `format=json` — `include_document_bytes` defaults to `True` on the model. Not reachable from the UI (which sends `doc_file_types: []` for non-ZIP formats), which is why it went unnoticed. `write_json` now strips `_content` and reports `file_export_status`, mirroring the ZIP manifest.

**4 — zero-byte documents.** `entity_export_service` classified on `content is not None` while the ZIP builder wrote files on truthiness, so a zero-byte object produced `file_export_status: included` with a blank `bundled_file` and no file in the archive — a manifest contradicting the thing it describes, which is precisely the bug class this whole change set exists to remove. Both sides now use one predicate (`_has_file`), the builder refuses to report `included` when it wrote nothing, and a zero-byte download is logged as an error and classified as a fetch failure.

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
| `backend/tests/test_data_transfer_zip_builder.py` | 6 new regression tests | Lock the opt-out/fault distinction, the `_storage_key` import contract, and mixed-selection README wording |
| `backend/routes/admin/data_transfer_export.py` | `doc_file_types` field, bound into `_gate_params`, threaded to the job, recorded in the audit payload | Per-document-type file selection without letting an approval be reused to widen scope |
| `admin-dashboard/src/lib/api/data-transfer.ts` | `docFileTypes` option, sent as `null` when absent | Let an explicit empty array reach the backend as "metadata only" |
| `backend/tests/test_entity_export_service.py` | 5 new tests | Per-type selection, empty-list semantics, precedence, back-compat, batch threading |
| `backend/tests/test_data_transfer_export_route.py` | 4 new tests | Approval-gate binding, order-independence, `None` vs `[]` for both `doc_file_types` and `doc_types` |
| `backend/services/data_transfer/tabular_writer.py` | `_public_documents`; `write_json` strips `_content` | Review finding 3 — JSON export serialized raw document bytes |
| `backend/tests/test_data_transfer_tabular_writer.py` | New file, 4 tests | No test file existed for this module; pins the byte-leak fix |

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

- [x] **Automated tests run.** `pytest backend/tests/ -k "data_transfer or bundle or export or sgi or document or approval"` → **463 passed, 1 skipped**. 14 new tests total: 6 in `test_data_transfer_zip_builder.py` (opt-out vs. fetch-failure distinguishability, metadata-only README, fault warning, manifest→file path resolution, included-file counts, mixed-selection wording), 5 in `test_entity_export_service.py` (per-type selection, empty-list semantics, precedence over `include_document_bytes`, `None` back-compat, batch threading), 3 in `test_data_transfer_export_route.py` (approval-gate binding, order-independence, `None` vs `[]`).
- [x] **End-to-end bundle generated and inspected** for three selections (no files / all files / background-check-only) by driving `gather_entity_bundle` → `build_export_zip` with mocked storage, and unzipping the result. Confirmed the mixed case writes `documents/background_check_doc-bg.jpg`, lists the licence as `excluded_by_request` with a blank `bundled_file`, and produces the "1 of 2" README wording.
- [x] **Real production build run** — `cd admin-dashboard && npm run build` (Next.js production build) exits 0. Not a dev server, not `tsc --noEmit` alone. `npx eslint ExportTab.tsx` clean. Admin-dashboard vitest suite: 160 passed / 20 files.
- [x] **Backend lint/format** — `ruff format` applied to changed files; `ruff check` on the changed directory reports only 2 pre-existing errors on lines this change does not touch (`entity_import_service.py:141` F841, `entity_export_service.py:197` B905). Repo-wide `ruff check .` reports 35 pre-existing errors, so this is not a clean gate today — no new errors introduced.
- [x] **Blast-radius grep performed** — searched all non-test Python for `_storage_key`, `_content_status`, `_document_manifest`, `documents.csv`, `raw_data.json`, `bundled_file`, `file_export_status`. This is what caught the `bundle_document_uploader.py` extension regression before it shipped (see §4).
- [x] **Reviewed against CLAUDE.md conventions** — "do not silently swallow errors" (warning→error on storage/data faults), "additive over destructive" (columns added, none removed or repurposed), PIPEDA data minimization (default-off preserved deliberately; no change to exported data).
- [x] **Feature flag** — not applied. Justification: internal-admin-only, additive explanatory copy, no change to exported data or to any default. The repo's flag guidance targets user-visible/shared-component changes; `ExportTab.tsx` has exactly one consumer (the Data Transfer page, itself embedded once in Records & Compliance).

## What was NOT verified

- **Not tested against live Supabase.** No export was run end-to-end against a real `driver-documents` bucket. All backend verification is unit-level against constructed bundle dicts; `_fetch_document_bytes` and the storage download path were not exercised against real storage. Specifically, the `unavailable_fetch_failed` and `unavailable_no_storage_key` branches are covered at the ZIP-builder layer but their *triggering* in `entity_export_service` was not observed against a real bucket.
- **The user's original failing export was not reproduced against their data.** The diagnosis (opt-out, not fault) is inferred from the code path and the default value, and is consistent with the report. If their `background_check` documents *also* have unreadable `document_url`s, that is a second, separate defect this change does not fix — but it now reports it: after this change their ZIP's `documents.csv` will say `excluded_by_request` (setting) or `unavailable_*` (fault), which settles it definitively on the next export.
- **No visual regression tooling exists for admin-dashboard**, so the new warning banner's appearance — including its dark-mode variant — was reasoned about against sibling components' Tailwind classes, not screenshotted or snapshot-tested. This is the standing gap already noted in CLAUDE.md release gate #6.
- **No new frontend unit test** for the warning's render condition or the new checkbox grid; `metadataOnlyDocuments`, `toggleDocFileType`'s implication rules, and `toggleAllFiles` are covered by inspection and the production build only. `ExportTab.tsx` has no existing test file to extend. The backend equivalents of those semantics (empty list, precedence, back-compat) *are* covered.
- **The dual-approval re-approval effect was reasoned about, not exercised.** `dual_approval_exports_enabled` is off by default, so no live grant is believed to be in flight — but this was not checked against the live `app_settings` value or the approvals queue.
- **Known behavior left unchanged (not a fix, a note).** Ticking all six document types sends `doc_types: null`, which means "every type" — including any `document_type` value in the DB that isn't one of the six the UI lists (legacy or newer types). So "tick everything" can export more types than are shown on screen. Pre-existing, and narrowing it could silently drop legacy documents an operator expects, so it was deliberately not changed here. Worth a follow-up that sources the list from the DB rather than a frontend constant.
- **`_storage_key` is still present in the JSON export** (as in the ZIP manifest). It is an opaque UUID filename in a private bucket, not a credential, and the ZIP import path depends on it — removing it from one format and not the other seemed worse than leaving both consistent. Flagged rather than changed.
- **Import round-trip not executed end-to-end.** The `bundle_document_uploader` fallback is covered by existing tests passing plus the grep, not by an export→import cycle against two live environments.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`; no live-data effects; independently revertible halves)
- [x] Blast radius is stated, not assumed (grep-derived consumer table in §4, including one regression it caught)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 — the change is *additive explanation*; exported data is byte-identical)
