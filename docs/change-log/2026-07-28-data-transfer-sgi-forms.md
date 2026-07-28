# Change Impact & Risk Log — Data Transfer module: SGI PDF form-fill service + route (Phase 5.1)

## Issue/gap identified
Drivers must be reported to SGI (Saskatchewan Government Insurance) via two
real government fillable PDFs — "Passenger for Hire – Driver Details"
(D00032) and "Transportation Network Company – Vehicle Details" (D00033) —
currently filled by hand.

## Root cause
No existing tooling generates these; they're a manual per-driver/vehicle
process today.

## Fix/remediation
- Copied the two real fillable PDFs the user provided (already carrying
  Spinr's own pre-filled company-section fields — company name, SGI
  customer number, address, primary contact) into
  `backend/static/sgi_forms/D00032_driver_details_template.pdf` and
  `D00033_vehicle_details_template.pdf` as fill templates, rather than
  recreating the forms from scratch (which `fpdf2`, already a dependency,
  cannot do — it only generates new PDFs, not fills existing ones).
- New `backend/services/data_transfer/sgi_form_filler.py`: uses `pypdf`'s
  `PdfWriter.update_page_form_field_values` against the real AcroForm field
  names — inspected via `PdfReader.get_fields()` against both actual
  templates (86 fields / 128 fields, matching the numbers established
  earlier in this conversation), not assumed. The row-slot naming in the
  real forms is NOT uniform: row 1 of some field types uses a bare name,
  others an explicit "1" suffix, and one field in the vehicle form has an
  actual typo in the government PDF itself (`YeaMakeModel5` instead of
  `YearMakeModel5`) — all encoded exactly as observed, not "corrected,"
  since the filled PDF must match the real form's field names to submit to
  SGI. Button/checkbox export states were verified via `get_fields()`'s
  `/_States_` (not assumed): `AddOrRemove` uses numeric indices (`/0`/`/1`/
  `/2`) while `Vehicle<N>` uses literal labels (`/Add`/`/Remove`/`/Change`)
  — two different encodings for what looks like the same UI concept, caught
  by actually inspecting the PDF rather than guessing.
- New `backend/services/data_transfer/sgi_field_maps.py`: maps a `drivers`
  table row to each form's row-value dict. Column names cross-checked
  against `driver_import_service.py` (the authoritative source for what
  actually lands in `drivers`) rather than assumed.
- New `backend/routes/admin/sgi_forms.py`: `POST /admin/data-transfer/sgi-forms/generate`
  (`form_type`, `driver_ids`, `action`) → filled PDF response, capped at
  each form's real row count (10 drivers / 16 vehicles), audit-logged.
- Modified `backend/routes/admin/__init__.py`: registers the router, gated
  by the same `bulk_operations` module as the rest of this admin module.

**Two real bugs caught and fixed before commit, both during end-to-end
verification against the actual template PDFs (not just `py_compile`)**:
1. `fill_vehicle_details_form` built a `vehicle_action` field-name slot but
   never actually populated it in `field_values` — the Add/Remove/Change
   checkbox row would have silently stayed blank on every generated form.
2. `sgi_field_maps.py`'s first draft referenced three fabricated column
   names (`license_verified`, `criminal_record_check_status`,
   `vehicle_inspection_status`) that don't exist on the `drivers` table —
   `.get()` would have silently defaulted them to falsy forever, so every
   generated form would have shown "No" for verified-history/
   criminal-record-check/valid-inspection regardless of the driver's actual
   status. Fixed to use the real columns (`spinr_approved`,
   `background_check_expiry_date`, `vehicle_inspection_expiry_date`) with an
   explicit unexpired-date check.

**Known gap, documented rather than silently worked around**: `vehicle_vin`
may be stored encrypted at rest (per `driver_import_service.py`'s
`vin_plain`/"(re)encrypt at commit" handling) — `sgi_field_maps.py` passes
the column value through as-is. If it's ciphertext, the generated form shows
the encrypted string instead of a real VIN. Decryption needs the same crypto
helper the import path uses, not wired into this subtask.

## Risk & impact on existing functionality
Blast radius: all four new files have zero existing callers (grep-confirmed)
— `sgi_forms.py` is a brand-new route, `sgi_form_filler.py`/
`sgi_field_maps.py` are only imported by it. `routes/admin/__init__.py`
again gets only additive lines. `db_supabase.get_rows("drivers", {"id":
{"$in": ...}})` is a read-only call using the pre-existing `$in` operator —
no write path exists on this route at all. The two template PDFs are new
static assets with no code dependency beyond `sgi_form_filler.py`'s own
`TEMPLATE_DIR` path resolution.

**Flag for the user, not a code risk but a business-data one**: the two
copied template PDFs already contain Spinr's real SGI customer number
(88816996) and a real employee name/signature in the pre-filled
company-section fields, now committed as binary blobs in this git
repository. That's presumably intentional (they're the company's own
working templates), but worth a conscious "yes, that's fine to have in the
repo" rather than an assumption on my part — flagging explicitly.

## User experience effect
None yet — no frontend calls this route (Phase 5.3).

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/static/sgi_forms/D00032_driver_details_template.pdf` | New: real SGI form as fill template | Source template for driver-details PDF generation |
| `backend/static/sgi_forms/D00033_vehicle_details_template.pdf` | New: real SGI form as fill template | Source template for vehicle-details PDF generation |
| `backend/services/data_transfer/sgi_form_filler.py` | New: AcroForm fill mechanics | Row-slot naming + pypdf fill logic |
| `backend/services/data_transfer/sgi_field_maps.py` | New: drivers-row → form-row mapping | Business-data mapping, kept separate from fill mechanics |
| `backend/routes/admin/sgi_forms.py` | New: generate route | HTTP surface |
| `backend/routes/admin/__init__.py` | +2 lines: import + `include_router` | Wire in, module-gated |

## Before/after snippet
```python
# bug 1, before (vehicle_action never populated):
field_values[slots["licence_plate_number"]] = ...
# ... (no line for vehicle_action)

# after:
field_values[slots["vehicle_action"]] = _VEHICLE_ACTION_STATE.get(vehicle.get("action", "add"), "/Add")
```
```python
# bug 2, before (fabricated columns, always False):
"criminal_record_check_attached": bool(driver.get("criminal_record_check_status") == "approved"),

# after (real column, unexpired-date check):
"criminal_record_check_attached": _has_unexpired_date(driver.get("background_check_expiry_date")),
```

## Rollback plan
Remove the two added lines in `routes/admin/__init__.py`, delete
`sgi_forms.py`, `sgi_form_filler.py`, `sgi_field_maps.py`, and the two
template PDFs. No other code depends on any of these yet (grep-confirmed).

## Verification performed
- `python3 -m py_compile` on all Python files — passes.
- **Actually exercised the filler end-to-end against the real template
  PDFs** (not just syntax-checked): filled both forms with test data,
  re-read the output with `PdfReader.get_fields()`, and confirmed the
  written values match exactly — including the row-5 `YeaMakeModel5` typo
  field and both button-state encodings (`AddOrRemove=/0`/`/1`,
  `Vehicle1=/Add`).
- Exercised `sgi_field_maps.py` feeding directly into `sgi_form_filler.py`
  with a realistic driver row, including an explicit expired-inspection-date
  case to confirm `_has_unexpired_date` returns `False` correctly.
- This live exercising is what caught both bugs above — a static-only review
  would have missed both, since neither raises an exception; they just
  silently produce a form with blank/wrong values.

## What was NOT verified
- Not exercised via the actual HTTP route (`sgi_forms.py`) — only the
  underlying service functions were run directly. `db_supabase.get_rows`
  with the `$in` filter wasn't exercised against a real or mocked Supabase
  client in this subtask.
- The `vehicle_vin` encryption gap (documented above) — whether the column
  is actually encrypted at rest, and if so what the real generated PDF would
  show, was not confirmed against a live database.
- The SGI-specific "criminal record check dated within 90 days of
  submission" requirement is NOT independently verified by this code — it
  reflects "an unexpired background check is on file" as a proxy, which the
  admin generating the form must still sanity-check before submitting to
  SGI. This is stated in the code comment, not silently assumed to be
  equivalent.
- No unit test yet (`test_sgi_form_filler.py` is Phase 5.2, next subtask).
