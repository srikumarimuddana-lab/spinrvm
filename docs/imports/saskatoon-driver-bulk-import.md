# Saskatoon Driver Bulk Import Runbook

This runbook explains how to prepare legacy driver data and document files for
` scripts/import_saskatoon_drivers.py `. The same format can be reused for other
service areas once their Service Area regulatory settings are configured in the
admin dashboard.

## 1. Configure the service area first

In Admin Dashboard → Service Areas → open the target area → General tab, set:

| Field | Saskatoon value | Future examples |
|---|---|---|
| Province | `SK` | `AB`, `ON` |
| Regulatory Authority | `SGI` | `Alberta TNC / municipal licensing`, `Toronto PTC` |
| Regulatory Region | `SK` | `AB`, `Calgary`, `Toronto` |
| Requirements URL | Official regulator/local requirements page | Official municipal/provincial page |
| Regulatory Notes | Short local rule summary | Short local rule summary |

Then confirm the Service Area → Documents tab has the document keys you will use
in `documents.csv`, for example:

```text
drivers_license
insurance
vehicle_registration
vehicle_inspection
background_check
drivers_abstract
work_authorization
```

The importer rejects document rows whose `requirement_key` does not exist on the
selected service area's `required_documents` list.

## 2. Folder layout

Create one import folder per batch. Do not mix service areas in one batch.

```text
bulk-import-saskatoon/
  drivers.csv
  documents.csv
  files/
    OLD_DRIVER_001/
      drivers_license_front.jpg
      drivers_license_back.jpg
      insurance.pdf
      vehicle_registration.jpg
      vehicle_inspection.jpg
      background_check.pdf
      drivers_abstract.pdf
      work_authorization.pdf
    OLD_DRIVER_002/
      drivers_license_front.jpg
      drivers_license_back.jpg
      insurance.pdf
      vehicle_registration.jpg
      vehicle_inspection.jpg
      background_check.pdf
      drivers_abstract.pdf
      work_authorization.pdf
```

Rules:

- `old_driver_id` is the stable join key between `drivers.csv`, `documents.csv`,
  and each driver's file folder.
- Use old-app IDs if available. If not, create stable IDs like `OLD_DRIVER_001`.
- Do not use driver names as folder names.
- Keep source CSVs and files in a secure location because they contain PII.
- Do not commit real import CSVs or document files to git.

## 3. Required `drivers.csv` format

Minimum required columns:

```csv
old_driver_id,full_name,phone,email,vehicle_plate,vehicle_type,vehicle_year,vehicle_make,vehicle_model
```

Recommended full format for your legacy sheet:

```csv
old_driver_id,full_name,date_of_birth,phone,email,license_number,license_class,address,regulatory_authority,regulatory_region,regulatory_authority_approved,spinr_approved,vehicle_plate,vin,vehicle_type,vehicle_year,vehicle_make,vehicle_model,criminal_record_check_expiry,insurance_expiry,vehicle_inspection_expiry,drivers_abstract_status,work_authorization_expiry,permanent_resident,citizen,decals_sent,service_area
```

Example with fake data:

```csv
old_driver_id,full_name,date_of_birth,phone,email,license_number,license_class,address,regulatory_authority,regulatory_region,regulatory_authority_approved,spinr_approved,vehicle_plate,vin,vehicle_type,vehicle_year,vehicle_make,vehicle_model,criminal_record_check_expiry,insurance_expiry,vehicle_inspection_expiry,drivers_abstract_status,work_authorization_expiry,permanent_resident,citizen,decals_sent,service_area
OLD_DRIVER_001,Test Driver One,1988-05-26,+13065550111,test1@example.com,LIC12345,5,"123 Test St, Saskatoon, SK",SGI,SK,Y,Y,ABC123,1HGCM82633A004352,SUV,2021,Toyota,RAV4,2026-10-23,2026-10-17,2026-10-31,Valid,2027-12-05,Yes,No,Y,saskatoon
OLD_DRIVER_002,Test Driver Two,1979-07-15,+13065550222,test2@example.com,LIC67890,4,"456 Test Ave, Saskatoon, SK",SGI,SK,Y,N,XYZ789,2HGFB2F59DH000000,XL,2017,Dodge,Caravan,2026-10-13,2026-06-18,2026-10-31,Valid,Indefinite,No,Yes,Y,saskatoon
```

### Driver CSV notes

- `phone`: prefer E.164 format like `+13065550111`; 10-digit numbers are
  normalized to `+1...`.
- `vehicle_type`: must match an existing `vehicle_types` name/display name/id,
  for example `SUV`, `XL`, or `Sedan`.
- `regulatory_authority` and `regulatory_region`: optional. If blank, the
  importer uses the selected Service Area defaults.
- `approved_from_sgi` is still accepted as a legacy alias for
  `regulatory_authority_approved`, but new CSVs should use the generalized name.
- `spinr_approved` controls whether the imported driver can be marked active
  together with regulatory approval. If either regulatory approval or Spinr
  approval is not true, the driver imports as `needs_review`.
- `address` is treated as source metadata only and is not inserted into a
  first-class plain-text driver column.

## 4. Required `documents.csv` format

Required columns:

```csv
old_driver_id,requirement_key,side,file_path,expiry_date,status,document_type
```

Example:

```csv
old_driver_id,requirement_key,side,file_path,expiry_date,status,document_type
OLD_DRIVER_001,drivers_license,front,files/OLD_DRIVER_001/drivers_license_front.jpg,2028-05-26,pending,Driver's License
OLD_DRIVER_001,drivers_license,back,files/OLD_DRIVER_001/drivers_license_back.jpg,2028-05-26,pending,Driver's License
OLD_DRIVER_001,insurance,,files/OLD_DRIVER_001/insurance.pdf,2026-10-17,pending,Vehicle Insurance
OLD_DRIVER_001,vehicle_registration,,files/OLD_DRIVER_001/vehicle_registration.jpg,2026-10-17,pending,Vehicle Registration
OLD_DRIVER_001,vehicle_inspection,,files/OLD_DRIVER_001/vehicle_inspection.jpg,2026-10-31,pending,Vehicle Inspection
OLD_DRIVER_001,background_check,,files/OLD_DRIVER_001/background_check.pdf,2026-10-23,pending,Background Check
OLD_DRIVER_001,drivers_abstract,,files/OLD_DRIVER_001/drivers_abstract.pdf,,pending,Driver Abstract
OLD_DRIVER_001,work_authorization,,files/OLD_DRIVER_001/work_authorization.pdf,2027-12-05,pending,Work Authorization
```

### Document CSV notes

- `file_path` is relative to `--files-root`.
- `requirement_key` must exist in the target service area's required documents.
- `side` is usually blank, `front`, or `back`.
- Use `status=pending` for normal imports so admins can review documents.
- Use `status=approved` only if the old-app verification is trusted and audit-ready.
- Supported date inputs include ISO dates like `2026-10-23` and legacy dates like
  `23-Oct-26`. Use `Indefinite` only for work authorization style fields.

## 5. Dry run first

From the repo root:

```bash
python3 scripts/import_saskatoon_drivers.py \
  --drivers-csv bulk-import-saskatoon/drivers.csv \
  --documents-csv bulk-import-saskatoon/documents.csv \
  --files-root bulk-import-saskatoon \
  --service-area-name Saskatoon \
  --dry-run
```

If more than one service area matches, pass the exact ID:

```bash
python3 scripts/import_saskatoon_drivers.py \
  --drivers-csv bulk-import-saskatoon/drivers.csv \
  --documents-csv bulk-import-saskatoon/documents.csv \
  --files-root bulk-import-saskatoon \
  --service-area-id <saskatoon-service-area-id> \
  --dry-run
```

Expected clean report shape:

```text
DRY RUN report
  users planned: 200
  drivers planned: 200
  documents planned: 1200
  files planned: 1200
  warnings: 0
  errors: 0
```

Do not run `--commit` until `errors: 0`.

## 6. Commit import

After a clean dry run:

```bash
python3 scripts/import_saskatoon_drivers.py \
  --drivers-csv bulk-import-saskatoon/drivers.csv \
  --documents-csv bulk-import-saskatoon/documents.csv \
  --files-root bulk-import-saskatoon \
  --service-area-id <saskatoon-service-area-id> \
  --batch saskatoon-legacy-2026-07 \
  --commit
```

The importer will:

1. Insert `users` rows.
2. Insert `drivers` rows scoped to the target service area.
3. Encrypt license number and VIN through the backend PII encryption RPC before
   inserting those driver fields.
4. Upload document files to Supabase Storage under:

```text
saskatoon-import/<batch>/<old_driver_id>/<requirement_key>/<side-or-main>-<document_id>.<ext>
```

5. Insert `driver_documents` rows pointing at the uploaded storage objects.

## 7. Post-import admin review

After import:

1. Go to Admin Dashboard → Drivers.
2. Filter to the Saskatoon service area.
3. Open each imported driver.
4. Review **Compliance & Import Data**.
5. Review the **Documents** tab and approve/reject uploaded documents.
6. Use the **Actions** tab to approve/activate drivers only after compliance
   review is complete.

## 8. Common validation errors

| Error | Meaning | Fix |
|---|---|---|
| `drivers CSV is missing required column` | Header is missing | Add the required column exactly or use a supported alias |
| `row is not scoped to Saskatoon` | `service_area`/`service_area_id` does not match target | Fix the row or import in the correct batch |
| `matching user or driver already exists` | Duplicate phone/email/driver found | Resolve manually before import |
| `no vehicle_types row matched` | `vehicle_type` does not match catalogue | Update vehicle type label or create the vehicle type |
| `document file not found` | `file_path` is wrong or file missing | Fix folder/file path |
| `requirement_key is not configured` | Service Area Documents tab lacks that key | Add the document key in Service Areas first |
| `could not parse date` | Date format unsupported | Convert to `YYYY-MM-DD` |

## 9. Re-running after a partial failure

The commit phase runs in order: users → drivers → file uploads → documents.
If a run dies partway (network blip, storage error), simply re-run the same
command:

- Drivers already created by a previous run (matched on
  `legacy_import_metadata.old_driver_id` + `source`) are **skipped with a
  warning**, not treated as conflicts.
- Documents already inserted for those drivers (matched on
  `requirement_key` + `side`) are also skipped, so the re-run only fills in
  what is missing.
- **Caveat**: a failure between the `users` and `drivers` inserts leaves user
  rows without driver rows. Those still surface as
  `matching user or driver already exists` errors — delete the orphaned
  `users` rows (they have `role = 'driver'` and no matching `drivers` row)
  before re-running.

## 10. Ambiguous dates

Dates like `03/04/25` parse differently day-first vs month-first. The importer
warns (`date parses differently day-first vs month-first`) on any such value —
including document expiry dates, which gate `go_online`. Confirm the source
sheet's format and convert to `YYYY-MM-DD` before `--commit`.

Document `status` values are validated against the review flow's set
(`pending`, `approved`, `rejected`); anything else is a hard error so imported
documents can't land in states the admin UI never shows.
