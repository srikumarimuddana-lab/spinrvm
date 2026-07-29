# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this commit) |
| Related issue or gap ID | Live-testing feedback batch: Data Transfer module + SGI Compliance Forms |

## 1. Issue / gap identified

Batch of fixes/features found during live testing of the Data Transfer module:
- SGI D00032 "Passenger for Hire — Driver Details" downloaded with the driver's **name blank**; D00033 "Vehicle Details" downloaded with **registered owner name blank** and **company name/SGI customer number blank on pages 2-3**.
- No way to filter Search & Select or the Export tab defaults by service area; Export defaulted to including exact GPS coordinates and raw document file contents.
- Search only filtered on explicit button click, not as the admin typed, and didn't rank close matches first.
- Insurance-Period Audit report exposed raw `driver_id`/`ride_id`, which the business doesn't want on the rendered report.
- Knight Archer (an SGI-partner insurance company) needed a driver-onboarding roster report that didn't exist.
- No way to email a generated report directly to a `@spinr.ca` address.

## 2. Root cause

- **D00032/D00033 blank fields**: `sgi_field_maps.py` read `driver.get("full_name")` and `driver.get("spinr_approved")` — neither column exists on the real `drivers` table (confirmed directly against `information_schema.columns` on the production project). The real columns are `name` and `is_verified`; the code silently fell through to empty-string/`False` defaults instead of erroring, so the bug shipped invisibly.
- **D00033 company info blank on pages 2-3**: D00033 is a 3-page AcroForm with an independent `CompanyName`/`SGICustomerNumber` field per page. The static PDF template only ships page 1 pre-filled (`CompanyName2`, `CompanyName3`, `SGICustomerNumber3` are blank in the template itself) — confirmed via `PdfReader.get_fields()`. Nothing in the fill code compensated for this.
- Everything else was new functionality requested directly, not a defect.

## 3. Fix / remediation

- `sgi_field_maps.py`: read `driver.get("name")` instead of `full_name` for both driver name and registered-owner name; `verified_driver_history`, `criminal_record_check_attached`, and `valid_inspection` now default to `True` (explicit product decision, since the Data Transfer selection flow already scopes to onboarded Spinr drivers).
- `sgi_form_filler.py`: explicitly force `CompanyName`/`CompanyName2`/`CompanyName3`/`SGICustomerNumber`/`SGICustomerNumber2`/`SGICustomerNumber3`/`StreetAddress` (D00033) and `Company name`/`Street address`/`SGI customer number` (D00032) on every generated form, rather than relying on the static template's baked-in (and incomplete) defaults. Company address updated to `#200, 1956 BROAD STREET, REGINA, SASKATCHEWAN, CANADA, S4P 1Y1`.
- `data_transfer_search.py` + `EntitySearchTable.tsx`: added a driver-only `service_area_id` filter. Search input now debounces (300ms) and auto-runs as the admin types, with client-side re-sort so name-prefix matches float to the top.
- `ExportTab.tsx`: "Exact pickup/dropoff GPS coordinates" and "Document file contents" now default **unchecked** (PIPEDA data minimization).
- `compliance.py`: Insurance-Period Audit report no longer renders `driver_id`/`ride_id` columns (filtering by `driver_id` query param still works; `driver_name` still identifies the driver on the report).
- `compliance.py`: new `GET /api/admin/compliance/knight-archer-driver-onboarding` report — driver name, license number, license class, status, onboarded date, for every driver regardless of status (deliberately not filtered to active-only), optional `status` filter. New "Knight Archer Driver Onboarding" tab on the Compliance & Tax Reporting page.
- `compliance.py` + `page.tsx`: added `email_to` query param (hard-validated to `@spinr.ca`) to all three Compliance reports, reusing `send_transactional_email`'s attachment support — sends the already-rendered report as an email attachment instead of streaming it to the browser.
- Added Radix `Tooltip` hints across Search & Select, Export, SGI Forms, and Compliance tabs at points that previously had undocumented defaults/behavior.

## 4. Risk & impact on existing functionality

- **`sgi_field_maps.py`/`sgi_form_filler.py`**: only consumed by `routes/admin/sgi_forms.py`'s `/data-transfer/sgi-forms/generate` endpoint — no other callers (grepped). The `_has_unexpired_date` helper became unused after the default-True change and was removed; nothing else referenced it.
- **`data_transfer_search.py`**: `service_area_id` filter is additive and driver-branch-only — the rider/mixed branches are untouched, so no regression there. `users` table has no `service_area_id` column, confirmed before adding.
- **`compliance.py`**: `_insurance_period_rows`'s row dict shape changed (dropped 2 keys) — updated the one unit test (`test_compliance_reports.py::test_joins_driver_name`) that asserted on the old shape. No other consumer of that function exists.
- **Export tab defaults**: `includeRideGps`/`includeDocumentBytes` only affect the ZIP export bundle contents; no other component reads these state variables.
- **Email delivery**: reuses `send_transactional_email` (already used by receipt/marketing email paths) — adds one new call site each in `compliance.py`'s 3 report endpoints, no changes to the shared function itself.

## 5. User-experience effect

Internal-admin-facing only (Data Transfer + Compliance & Tax Reporting pages, both admin-only, module-gated). Not visible to riders, drivers, or corporate admins, and not visible mid-session to anyone outside these two admin tools.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/data_transfer/sgi_field_maps.py` | Fixed `full_name`→`name` column bug; 3 fields default to `True` | Fix blank name bug; product decision |
| `backend/services/data_transfer/sgi_form_filler.py` | Force company name/address/customer number on every page | Fix blank company-info bug |
| `backend/routes/admin/data_transfer_search.py` | Added `service_area_id` param (driver-only) | Requested filter |
| `backend/routes/admin/compliance.py` | Dropped `driver_id`/`ride_id` from insurance audit; new Knight Archer report; `email_to` on all 3 reports | Requested changes |
| `backend/tests/test_sgi_form_filler.py` | Updated/added tests for the column-name fix and new defaults | Regression coverage |
| `backend/tests/test_compliance_reports.py` | Updated row-shape assertion | Match dropped columns |
| `backend/tests/test_compliance_reports_http.py` | New tests for Knight Archer report + `email_to` | Regression coverage |
| `admin-dashboard/src/lib/api.ts` | `serviceAreaId` param; Knight Archer download/email helpers; `emailComplianceReport` | Frontend wiring |
| `admin-dashboard/src/components/data-transfer/EntitySearchTable.tsx` | Service-area filter; debounced live search + relevance sort; hints | Requested UX changes |
| `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx` | GPS/document-bytes default unchecked; hints | Data minimization |
| `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx` | Hints | Requested UX help |
| `admin-dashboard/src/app/dashboard/compliance/page.tsx` | Knight Archer tab; email-to-spinr.ca control on all 3 reports; hints | Requested features |

## 7. Before / after

```python
# Before (sgi_field_maps.py) — silently blank on every real driver row
"full_name": driver.get("full_name", ""),          # column doesn't exist
"verified_driver_history": bool(driver.get("spinr_approved")),  # column doesn't exist

# After
"full_name": driver.get("name", ""),                # real column
"verified_driver_history": True,                     # explicit default
```

```python
# Before (sgi_form_filler.py) — page 2/3 company fields ship blank in the template
field_values: dict[str, Any] = {}

# After
field_values: dict[str, Any] = dict(_VEHICLE_COMPANY_FIELDS)  # forces every page
```

## 8. Rollback plan

`git revert` — every change is either a pure bugfix (restores previously-broken behavior, not previously-working behavior) or additive (new report endpoint/tab, new optional query params, new default values on previously-unused-correctly fields). No migration, no schema change, no data mutation. Reverting the ExportTab/SgiFormsTab default changes simply restores the prior (less privacy-conscious / buggier) defaults — no data loss.

## 9. Verification performed

- **Backend**: `pytest` on all touched/added test files — 78 passed (`test_sgi_form_filler.py`, `test_sgi_forms_route.py`, `test_compliance_reports_http.py`, `test_compliance_reports.py`, `test_compliance_rate_limit.py`, `test_data_transfer_search.py`, `test_data_transfer_search_route.py`). `ruff check` clean on all touched files.
- **SGI PDF fix verified against the real AcroForm templates** (not mocked): filled a real D00033 and read back `CompanyName`/`CompanyName2`/`CompanyName3`/`StreetAddress`/`SGICustomerNumber*` via `pypdf.PdfReader` — all populated correctly.
- **Real-schema verification**: confirmed `drivers.full_name` and `drivers.spinr_approved` do not exist, and `drivers.name`/`drivers.is_verified` do, via `information_schema.columns` against the real project (`soavhtdhefowwvforzwb`) before writing the fix.
- **Frontend**: `npx tsc --noEmit` clean on every touched file (pre-existing unrelated jest-config errors in `__tests__/*` untouched by this change). **`npm run build` — real production build ran to completion**, both `/dashboard/compliance` and `/dashboard/data-transfer` built successfully.

## 10. What was NOT verified

- The SGI PDF fixes were verified by reading back AcroForm field values programmatically, not by visually opening the rendered PDF in Adobe Acrobat/a browser — a byte-level field-value check is not the same as confirming visual layout/rendering in every PDF viewer.
- The new `email_to` feature was verified against a mocked `send_transactional_email` — not exercised against a real SES/Resend send in this session, so actual deliverability to a real `@spinr.ca` inbox wasn't observed.
- The live-search debounce/relevance-sort UX was verified by `tsc`/build only, not by manually typing into the running dev server and watching results reorder — no browser-driven UI test exists for this component.
- Knight Archer report's exact column set/format was not confirmed against a real Knight Archer intake spec (no such document was provided) — built from the plain-language description given ("driver onboarded, driver license, with all status").
