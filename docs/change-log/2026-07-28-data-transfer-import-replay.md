# Change Impact & Risk Log — Data Transfer module: document + insurance-period replay (Phase 2.2)

## Issue/gap identified
Phase 2.1's `commit_plan` created only user + driver profile rows; the ZIP
bundle's documents and `driver_insurance_periods` (the 7-year regulatory
audit trail per CLAUDE.md's Insurance periods rules) were parsed into memory
but never written to the target environment. Without this, an "import" would
silently drop a driver's document history and the SGI-relevant insurance
audit trail — exactly the data this module exists to carry over.

## Root cause
Deliberate phasing (CLAUDE.md's ≤3-file subtask rule) — profile creation
first, replay of dependent child records (which need the freshly-created
`driver_id`) second.

## Fix/remediation
- New `backend/services/data_transfer/bundle_document_uploader.py`:
  - `replay_documents(new_driver_id, documents, document_files)` — re-uploads
    each document's raw bytes to *this* environment's `driver-documents`
    bucket under a fresh storage key (the source environment's key is
    meaningless here), validates extension against the existing
    `ALLOWED_EXTENSIONS` allowlist and `_validate_file_type` magic-byte check
    (both imported from `documents.py`, not reimplemented), inserts a fresh
    `driver_documents` row. A document whose bytes weren't captured during
    export (see `entity_export_service`'s `_content=None` fallback) is
    skipped rather than creating a metadata-only row pointing at nothing.
  - `replay_insurance_periods(new_driver_id, periods)` — append-only insert
    of each period under the new `driver_id`, `ride_id` dropped (the source
    ride doesn't exist in the target environment) since `period`/`started_at`/
    `ended_at` are the fields that matter for the regulatory audit trail.
    Never updates or deletes an existing row, per CLAUDE.md's "Never delete
    or mutate period rows — append only" rule.
- Modified `backend/services/data_transfer/entity_import_service.py`:
  `commit_plan` now calls both replay functions per created driver and
  returns their counts (`documents_replayed`, `insurance_periods_replayed`)
  alongside `created_users`/`created_drivers`.

## Risk & impact on existing functionality
Blast radius: `bundle_document_uploader.py` is a new file with one caller
(`entity_import_service.commit_plan`, itself only reachable via the not-yet
UI-wired `/data-transfer/import/commit` route). It writes to the SAME
`driver-documents` Storage bucket and `driver_documents` table that
`documents.py`'s admin/driver upload paths use — grepped for every other
writer of `driver_documents`: `backend/routes/admin/documents.py`'s
`admin_document_upload` (admin-on-behalf-of-driver upload) and the driver's
own upload endpoint in `documents.py`. This module inserts new rows with
fresh UUIDs and a fresh `driver_id`; it never updates or reads an existing
document row from another driver, so it cannot collide with or overwrite
anything either of those paths wrote. Same story for
`driver_insurance_periods`: the only other writer is
`routes/admin/drivers.py:2681` and the ride-state-transition inserts in
`routes/drivers/ride_flow.py`/`ride_complete.py` — all inserts, matching this
module's append-only insert, so there's no update-path collision possible.
A partial failure (one document fails to upload) is caught and logged per-item
inside `replay_documents`/`replay_insurance_periods` — it does not abort the
rest of the entity's replay or roll back the already-created driver profile;
this is a deliberate choice (documented in `commit_plan`'s docstring) since a
driver record missing one document is recoverable via manual re-upload,
while surprise-deleting a profile the operator is relying on is worse.

## User experience effect
None yet — not reachable from any UI.

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/services/data_transfer/bundle_document_uploader.py` | New: document + insurance-period replay | Complete the import's data fidelity |
| `backend/services/data_transfer/entity_import_service.py` | `commit_plan` now calls both replay functions and returns their counts | Wire replay into the commit path |

## Before/after snippet
```python
# before
await db_supabase.insert_one("drivers", driver_record)
created_drivers += 1
# (documents and insurance periods never written)

# after
await db_supabase.insert_one("drivers", driver_record)
created_drivers += 1
documents_replayed += await bundle_document_uploader.replay_documents(
    new_driver_id, entity.documents, entity.document_files
)
insurance_periods_replayed += await bundle_document_uploader.replay_insurance_periods(
    new_driver_id, entity.driver_insurance_periods
)
```

## Rollback plan
Revert `entity_import_service.py`'s `commit_plan` to the profile-only version
(git revert is safe — no other code depends on the new return-dict keys yet)
and delete `bundle_document_uploader.py`. Any `driver_documents`/
`driver_insurance_periods` rows already replayed during testing are ordinary
rows scoped to the test-imported `driver_id`s — cleanup is the same as
deleting any other test-imported driver.

## Verification performed
- `python3 -m py_compile` on both files — passes.
- Confirmed `ALLOWED_EXTENSIONS`/`_validate_file_type` are real, importable
  module-level symbols in `documents.py` (not accidentally private in a way
  that would break the import).
- Grepped every other writer of `driver_documents` and
  `driver_insurance_periods` to confirm no update-path collision is possible
  (this module only ever inserts, under a fresh driver_id it just created).

## What was NOT verified
- No unit test yet for either file — `test_entity_import_service.py` is
  still planned as a single test covering the full commit path now that it's
  complete end-to-end (profile + documents + insurance periods).
- Not exercised against a real Storage bucket or a real multi-document ZIP —
  the upload/signed-URL calls are reasoned through against the existing
  `documents.py` bulk-upload code shape, not executed.
- The `_content=None` skip-and-continue behavior (for documents whose bytes
  didn't survive export) has not been tested against an actual partial-export
  bundle.
