# Change Impact & Risk Log — Data Transfer module: license_number vault-encryption fix (follow-up)

## Issue/gap identified
Two related bugs, both stemming from the same root cause:

1. **SGI D00032 form**: `sgi_field_maps.driver_to_driver_details_row` reads
   `driver.get("license_number")` directly from the raw `drivers` row and
   writes it onto the "Licence number" field. `license_number` is
   vault-encrypted at rest (a `vault.secrets` UUID, not the real value — see
   `routes/drivers/_shared.py`'s `_VAULT_PII_FIELDS`). Every generated D00032
   would show an opaque UUID instead of a real licence number.
2. **Export/import round-trip**: `entity_export_service.gather_entity_bundle`
   put the raw (still-encrypted) `drivers` row into the export bundle.
   `entity_import_service.commit_plan` wrote that same encrypted token
   verbatim into the target environment's `drivers.license_number` column.
   Since the value is a `vault.secrets` UUID **scoped to the source Supabase
   project's vault**, that UUID points at nothing in the target project — a
   silent data-loss bug on every cross-environment import of a driver's
   licence number, independent of the SGI form issue.

This was flagged as a suspected gap in the original PR's Change Impact Log
("`vehicle_vin` may be stored encrypted... decryption needs the same crypto
helper the import path uses, not wired into this subtask"). Investigating it
found the actual encrypted field is `license_number`, not `vehicle_vin` —
migration `244_vehicle_vin_plaintext_at_rest.sql` (already applied, predates
this module) reclassified VIN to plaintext-at-rest/mask-in-UI; only
`license_number` remains vault-encrypted (confirmed via
`routes/drivers/_shared.py`'s `_VAULT_PII_FIELDS = frozenset({"license_number"})`
and the migration's own comment). The original flag was directionally right
but pointed at the wrong field — worth correcting explicitly rather than
leaving the stale VIN note in place.

## Root cause
The module was built without checking the established vault-encryption
pattern already used by `routes/drivers/_shared.py`/`driver_import_service.py`
for this exact field. Both the SGI mapper and the export/import bundle
treated every `drivers` column as a plain value.

## Fix/remediation
- `backend/routes/admin/sgi_forms.py`: decrypts every fetched driver row via
  `routes/drivers/_shared.py`'s `_decrypt_driver_pii()` (the same helper
  driver-facing routes already use) before mapping to SGI form rows.
- `backend/services/data_transfer/entity_export_service.py`:
  `gather_entity_bundle` decrypts the driver row's `license_number` at
  gather time — consistent with this export already being "full-fidelity,
  unredacted, admin-to-admin" by design (per the module's own docstring);
  the exported bundle now carries the real licence number, not an opaque
  token.
- `backend/services/data_transfer/entity_import_service.py`: `commit_plan`
  re-encrypts `license_number` via `_vault_encrypt()` against the **target**
  environment's own vault immediately before insert — fail-closed by
  design (raises 503 rather than writing plaintext PII on encryption
  failure, matching `_vault_encrypt`'s existing contract).
- Same commit fixes an unrelated, pre-existing latent bug noticed while
  touching this import block: `entity_import_service.py`'s `except
  ImportError` fallback (the non-package/CLI-style import path) was missing
  `from . import bundle_document_uploader` — would only break under that
  fallback import style, never exercised by the normal
  `python -m backend.server` path, but fixed while already in the file.

## Risk & impact on existing functionality
Blast radius: `_decrypt_driver_pii`/`_vault_encrypt` are the **established**
helpers already used by every driver-facing route that reads/writes
`license_number` (`routes/drivers/profile.py`, `status.py`, `__init__.py`,
plus the admin driver routes) — this fix makes the Data Transfer module
follow the same convention, not introduce a new one. No existing caller of
`entity_export_service`/`entity_import_service`/`sgi_forms.py` is affected
beyond correcting their output — all three are still only reachable via
this module's own routes (grep-confirmed, unchanged from the original PR).
Decryption/encryption failures now surface as loud errors (503 from
`_vault_encrypt`, or the raw token returned with a logged error from
`_vault_decrypt`'s fail-open-to-unreadable-token behavior) rather than
silently exporting/importing broken data.

## User experience effect
None visible until an admin actually uses the SGI form or import features —
at which point the fix is the difference between a working feature and a
silently broken one (opaque UUID on the PDF; a driver whose licence number
is unrecoverable after cross-environment import).

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/routes/admin/sgi_forms.py` | Decrypts driver rows via `_decrypt_driver_pii` before mapping | D00032 needs the real licence number |
| `backend/services/data_transfer/entity_export_service.py` | Decrypts `license_number` at gather time | Bundle must carry the real value, not a source-vault-scoped token |
| `backend/services/data_transfer/entity_import_service.py` | Re-encrypts `license_number` against the target vault on commit; fixes a pre-existing missing import in the `except ImportError` fallback | Correct cross-environment round-trip; unrelated latent bug fix |

## Before/after snippet
```python
# entity_import_service.py commit_plan, before:
await db_supabase.insert_one("drivers", driver_record)  # license_number = source-project vault UUID, meaningless here

# after:
if driver_record.get("license_number"):
    driver_record["license_number"] = await _vault_encrypt(str(driver_record["license_number"]), "license_number")
await db_supabase.insert_one("drivers", driver_record)
```

## Rollback plan
Revert the three files to their prior committed state (`git revert` is
safe — no data migration involved, this only changes what a fresh
export/import/SGI-generate call does going forward). Any driver rows
already imported before this fix, with a stranded source-vault UUID in
`license_number`, would need a manual data-correction pass (re-fetch the
real licence number from the source environment and re-encrypt it) — flagged
as a known cleanup item if this bug shipped and was exercised before the fix
landed (it was caught before the original PR's functionality was exercised
against real data, so no known-affected rows exist yet).

## Verification performed
- `python3 -m py_compile` on all three files — passes.
- Confirmed `_VAULT_PII_FIELDS = frozenset({"license_number"})` and the
  VIN-is-now-plaintext reclassification directly from
  `routes/drivers/_shared.py` and migration `244_vehicle_vin_plaintext_at_rest.sql`
  — not assumed.
- Confirmed `_decrypt_driver_pii`/`_vault_encrypt` are the real, established
  helpers (not newly invented) by reading their definitions and existing
  callers (`routes/drivers/profile.py`, `status.py`, `__init__.py`).
- Checked for prior precedent of an admin route importing from
  `routes.drivers._shared` — none exists; this is the first, but it's an
  ordinary intra-`backend/routes` import, not a new package boundary
  (`routes/admin/drivers.py` already operates in the same domain).

## What was NOT verified
- Not exercised against a live Supabase project — `_vault_encrypt`/
  `_vault_decrypt` require a real `encrypt_driver_pii`/`decrypt_driver_pii`
  RPC and a real vault, neither available in this session's environment
  (consistent with every other verification note in this module's history).
- No unit test added for this fix specifically — the existing
  `test_sgi_form_filler.py`/`test_data_transfer_search.py` don't cover the
  route/service layer this touches; this remains a known coverage gap
  flagged in the broader module review.
