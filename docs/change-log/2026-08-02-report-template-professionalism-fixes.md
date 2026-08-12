# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | User review request: Compliance report templates "not professional," Airport Trips rider name missing, SGI/Knight Archer billing missing driver detail + serial numbers, document format on export/import |

## 1. Issue / gap identified

User asked for a design review of the Compliance report templates and found five concrete problems:
1. Airport Trips report never shows a rider name.
2. No report has a serial-number/row-count column.
3. SGI/Knight Archer billing reports have minimal driver detail (name only).
4. Overall report presentation reads as unpolished (no borders, no auto column widths, no frozen header, no Excel footer).
5. A hunch that document upload/export doesn't preserve format.

## 2. Root cause

1. **Airport Trips rider name**: `dict.get(key, default)`'s `default` only fires when the key is *missing*, not when it's present with value `None`. `users.first_name`/`last_name` are real columns that are frequently `NULL` in production (confirmed live) — `f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()` therefore evaluated to the literal string `"None None"` instead of falling back.
2. **No serial numbers**: never implemented — none of the shared report writers or the endpoints ever added a row-index field.
3. **Billing reports missing driver detail**: the row builder only ever selected/joined `name`/`first_name`/`last_name` from `drivers` — license number and vehicle were never fetched for this report (they were already fetched for Driver Roster and Airport Trips, just not reused here).
4. **Thin Excel presentation**: `write_branded_table`/`write_branded_grouped_table` only ever set fill+font on cells — no borders, no column-width computation, no freeze panes, no print-header repeat, no footer, unlike the PDF path which already has a footer (`render_branded_pdf_footer`).
5. **Document format issue — worse than suspected**: `bundle_document_uploader.py`'s `replay_documents` called `_validate_file_type(content, "application/octet-stream")` on every document re-upload during a Data Transfer import. `"application/octet-stream"` is not a member of `documents.py`'s `ALLOWED_MIME_TYPES` allowlist, so this call **always raised**, and the surrounding `except Exception: continue` silently swallowed it — meaning **every document in every bundle-import replay has always been skipped**, not merely mislabeled. Confirmed via a genuine (non-mocked) regression test that fails against the old code and passes against the fix. The export side (`entity_export_service.py`/`bundle_zip_builder.py`) was independently verified correct — it preserves original extension and raw bytes.

## 3. Fix / remediation

**`backend/routes/admin/compliance.py`:**
- Airport Trips: rider name lookup uses `.get(key) or ""` instead of `.get(key, "")`, then falls back to a PIPEDA-safe phone-last-4 (`"Rider •1234"`, never the full number) before the raw rider id.
- `_render_tabular_report` gained `serial_numbers: bool = True` (on by default for every existing caller — no call-site changes needed): prepends an `s_no` field to `fieldnames`/`rows` for every format. For a grouped (billing) report, only the parent "All phases" row is numbered; children get a blank cell so the numbering doesn't look broken once a trip group is expanded in Excel. The flat CSV/PDF/DOCX fallback for the same report numbers every row sequentially (no parent/child concept to preserve there).
- `_insurance_billing_detail_rows` (backs both SGI and Knight Archer billing) now also fetches and decrypts (`_decrypt_driver_pii`, the same vault-decrypt helper Driver Roster/T4A already use) `license_number`, plus `license_plate`/`vehicle_make`/`vehicle_model`/`vehicle_color` composed into a `vehicle` field — added to both the flat rows and the grouped parent/children.

**`backend/utils/report_branding.py`:**
- New `_finalize_worksheet()` helper, called at the end of both `write_branded_table` and `write_branded_grouped_table`: thin light-grey borders across the whole table, content-based column widths (capped 10–45 chars), `freeze_panes` at the row below the header, `print_title_rows` so the header repeats on every printed page, and a company-identity footer row (mirrors the existing PDF footer's `COMPANY_LINE`/`COMPANY_CONTACT_LINE`).

**`backend/services/data_transfer/bundle_document_uploader.py`:**
- New `_EXT_TO_MIME_TYPE` mapping (the same 6 extensions `documents.py`'s `ALLOWED_EXTENSIONS` already allows). `replay_documents` now derives the real content type from the document's extension and passes it to both `_validate_file_type` and `_upload_bytes`, instead of the always-failing `"application/octet-stream"`.

## 4. Risk & impact on existing functionality

- **Airport Trips rider-name fix**: isolated to one dict-construction line; only consumer is the Airport Trips report's `rider_name` column (display-only).
- **Serial-number column**: added centrally in `_render_tabular_report`, the single shared call path for every Compliance report (GST/PST, Driver Roster, T4A, SGI Billing, Knight Archer Billing, Airport Trips) — grepped every caller, all pass through this function, none bypass it. Purely additive (`s_no` is a new leading column); no existing field is renamed, removed, or reordered, so any downstream consumer keying off field *names* (not positional index) is unaffected. A consumer that assumed the *first* CSV column was `driver_name`/similar (positional) would break — no such consumer exists in this codebase (reports are terminal admin downloads, not re-ingested anywhere).
- **SGI/Knight Archer billing new fields**: additive (`license_number`, `vehicle`) — no existing field removed. Uses the same `_decrypt_driver_pii` helper already exercised by Driver Roster/T4A in production, so no new decryption code path risk.
- **Excel polish (`_finalize_worksheet`)**: purely presentational — no data values are changed, only formatting/layout metadata (borders, widths, freeze panes, print titles, a footer row two rows below the last data row). Applies to both `write_branded_table` and `write_branded_grouped_table`, the only two xlsx writers in this module — grepped for other callers, none exist outside `compliance.py`.
- **Document content-type fix**: this is the one fix with real behavior change beyond "was broken, now less broken" — **document replay on Data Transfer import will now actually happen** for the first time. Grepped every caller of `replay_documents`/`replay_new_documents`: only `entity_import_service.py`'s `commit_plan` (both the "new driver" and "update existing driver" branches). This is expected/intended behavior (the whole point of bundle import), not a side effect — same category of risk as the `data_transfer_export_jobs` FK fix earlier this session ("first time this code path has ever actually executed against real production data").

## 5. User-experience effect

**Internal admin only.** Compliance report downloads (all formats) gain a serial-number column and, in xlsx, visible borders/sizing/frozen header/footer. Airport Trips shows a real (or phone-fallback) rider name instead of "None None". SGI/Knight Archer billing xlsx/pdf/csv/docx downloads gain two new columns. Data Transfer imports will, for the first time, actually attach a re-uploaded driver's documents to their new driver record — an admin re-importing a bundle bound for a new environment will now see those documents show up under Documents, where previously nothing appeared (silently).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/compliance.py` | Rider-name fallback fix; `serial_numbers` param on `_render_tabular_report`; license/vehicle fields added to billing rows | User-reported bugs + gaps |
| `backend/utils/report_branding.py` | New `_finalize_worksheet` (borders, column widths, freeze panes, print titles, footer) called from both xlsx writers | Visual-presentation gap |
| `backend/services/data_transfer/bundle_document_uploader.py` | Real MIME-type derivation instead of hardcoded `application/octet-stream` | Document replay was always failing validation |
| `backend/tests/test_compliance_reports.py` | Updated one assertion for the two new billing-row fields | Keep pace with the fix |
| `backend/tests/test_compliance_reports_http.py` | New rider-name-fallback regression test; new serial-number regression test | Coverage for both bugs |
| `backend/tests/test_bundle_document_uploader.py` | Two new tests exercising `replay_documents` for real (not mocked) — genuinely fails against the old code | Coverage gap: this function had only ever been tested as a mock, which is exactly how the bug went unnoticed |

## 7. Before / after

```python
# Before — "None None" when first_name/last_name are present-but-NULL
rider_names[u["id"]] = f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or u["id"]

# After
name = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip()
if not name:
    phone = u.get("phone") or ""
    name = f"Rider •{phone[-4:]}" if len(phone) >= 4 else u["id"]
rider_names[u["id"]] = name
```

```python
# Before — always raises (not in ALLOWED_MIME_TYPES), silently caught, document skipped
_validate_file_type(content, "application/octet-stream")
url = await _upload_bytes(content, ext, "application/octet-stream")

# After
content_type = _EXT_TO_MIME_TYPE.get(ext, "application/octet-stream")
_validate_file_type(content, content_type)
url = await _upload_bytes(content, ext, content_type)
```

## 8. Rollback plan

`git revert` — no migration, no data written, no flag. Every change here is either a pure display/formatting fix or a validation-argument fix; none touch a database write path beyond the (already-existing, unmodified) `driver_documents` insert that document replay was always intended to perform.

## 9. Verification performed

- [x] `pytest backend/tests/test_compliance_reports.py backend/tests/test_compliance_reports_http.py backend/tests/test_report_branding.py backend/tests/test_compliance_rate_limit.py backend/tests/test_bundle_document_uploader.py backend/tests/test_period_distance_audit.py backend/tests/test_backfill_period_distances.py backend/tests/test_entity_import_service.py` — 118/118 passing.
- [x] `ruff check` on every touched file — clean.
- [x] Confirmed live via direct production query (Supabase MCP) that `users.first_name`/`last_name` are genuinely `NULL` (not just unselected) for real airport-trip riders — the bug's actual production trigger condition, not a hypothetical.
- [x] Both new `test_bundle_document_uploader.py` tests and the new rider-name test were verified to genuinely FAIL against the pre-fix code (git-stashed the fix, re-ran, confirmed red; un-stashed, confirmed green) — not just passing coincidentally.
- [x] Generated real sample `.xlsx` files with the new formatting and sent them to the user directly for visual confirmation (openpyxl-level verification of `freeze_panes`, `print_title_rows`, border styles, and footer cell content was also done programmatically).
- [ ] Not run against a live Supabase environment this session — all verification is via mocked-Supabase unit tests plus the one live read-only query cited above.
- [ ] The document-replay fix has not been exercised end-to-end against a real Data Transfer import in the live admin portal — the regression test proves the specific broken code path is now correct, but a full import → document-visible-on-new-driver flow has not been manually verified in a browser this session.
- [ ] No production build (`npm run build`) run — this batch of fixes is backend-only, no frontend files touched.

## 10. What was NOT verified / deferred

- End-to-end confirmation that a real Data Transfer import bundle, run through the actual admin UI, now shows the driver's documents attached after import (recommend as the next real-world check).
- Whether the Excel visual polish (borders/freeze panes/footer) looks right in Google Sheets specifically, as opposed to the openpyxl-level property checks and Excel-format assumptions made here — the user was sent sample files but hasn't yet confirmed back.
- The four driver-name construction sites (`compliance.py` lines ~497/657/810/1022) have the same underlying `.get(key, "")` anti-pattern as the rider-name bug, but are guarded by an outer `d.get("name") or ...` and `drivers.name` is reliably populated in production — left unchanged as a deliberate scope decision (fixing the confirmed live bug only, not every theoretically-similar pattern) rather than an oversight.
