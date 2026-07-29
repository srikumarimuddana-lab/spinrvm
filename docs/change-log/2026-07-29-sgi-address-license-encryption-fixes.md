# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (session 01EdoQM4tiLZ6g27DbcKKMMZ) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin, drivers |
| PR / commit link | `89c84ff`, `b350311`, `e7dd207`, `210699e` on `claude/reporting-module-kickoff-tx25am` |
| Related issue or gap ID | ACTION_ITEMS.md B14 |

## 1. Issue / gap identified

User-reported: the SGI D00032/D00033 compliance PDFs showed a jumbled company address, and populated a driver's name but not their licence number/class.

## 2. Root cause

Three independent issues, confirmed against real data/templates before writing any fix:

1. **Address:** both real SGI templates ship dedicated street/city/province/postal AcroForm fields, but `sgi_form_filler.py` only set the street-address field, to one combined `"STREET, CITY, PROVINCE, COUNTRY, POSTAL"` string — leaving the template's own city/province/postal fields at stale placeholder values. Every generated form showed two disagreeing addresses.
2. **Licence number/class blank:** confirmed via direct query against the real `drivers` table that 22 of 209 drivers (some already `is_verified: true`) have `NULL` `license_number`/`license_class`. Not a code bug — the field-mapping, PDF-slot naming, and Vault-decryption code all trace correctly. These are optional self-serve profile fields, never required at signup or document approval, and the uploaded licence photo is never transcribed into them.
3. **Found while fixing #2:** the admin-dashboard's `PUT /admin/drivers/{id}` route — the exact path needed to backfill #2 — wrote `license_number` as **plaintext**, never calling the `_encrypt_driver_pii()` step that the self-serve profile-update and bulk-import paths both use. A PIPEDA-violating gap independent of the other two, caught before it could be compounded by the backfill itself.

## 3. Fix / remediation

- Split the SGI company address into street/city/province/postal constants, each mapped to its correct dedicated field on both templates; country dropped (neither template has a field for it).
- Added `_encrypt_driver_pii()` call before the `drivers` table write in `admin_update_driver`.
- Added a `missing_license` filter to `GET /admin/drivers` (additive, default `false`) and a new admin page (`/dashboard/driver-license-backfill`) that lists the gap drivers, lets an admin view each one's already-uploaded licence document via the existing `DocumentReviewer`, and save via the now-fixed encrypting path.
- Wrote up a full root-cause + reasoned OCR/automated-onboarding proposal (`docs/proposals/2026-07-29-driver-document-ocr-onboarding-automation.md`) — **not implemented**, pending a scope/vendor decision.

Deliberately did **not** attempt to transcribe the 22 drivers' actual licence numbers/classes myself — the proposal doc explicitly reasons that an unverified read (OCR or otherwise) should never land on a regulator-facing form untrusted; a human must do that step via the new queue.

## 4. Risk & impact on existing functionality

- **`sgi_form_filler.py`**: only consumer is `routes/admin/sgi_forms.py`'s `generate_sgi_form` — grepped, no other caller. Isolated.
- **`admin_update_driver` (`PUT /admin/drivers/{id}`)**: this is the general-purpose admin driver-edit endpoint, used by the main Drivers page for *every* editable field, not just licence data. Grepped for all callers: only `admin-dashboard/src/app/dashboard/drivers/page.tsx`'s `updateDriver()` and the new backfill page use it. The change only affects the `license_number` key specifically — every other field in `driver_updates` passes through `_encrypt_driver_pii()` unchanged (it only touches keys in `_VAULT_PII_FIELDS = {"license_number"}`), so no other field's write behavior changes. Blast radius: isolated to `license_number` writes through this one route.
- **`GET /admin/drivers`**: `missing_license` defaults to `false` — every existing caller (main Drivers page, monitoring panel, exports) is unaffected. New param only activates on the new backfill page.
- No ride state machine, money/wallet, or background-loop interaction anywhere in this batch.

## 5. User-experience effect

- **Internal admin-facing only.** No rider/driver/corporate-admin visible change.
- Any admin who previously edited a driver's licence number via the dashboard was unknowingly writing plaintext PII — this is silently corrected going forward (existing plaintext values already in the DB are **not** migrated by this change; see Rollback/gap note below).
- New nav item ("Licence Backfill" under Drivers) is visible to any admin with the `drivers` module — additive, no existing screen changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/data_transfer/sgi_form_filler.py` | Address split into street/city/province/postal, mapped to correct dedicated fields | Fix disagreeing-address bug |
| `backend/tests/test_sgi_form_filler.py` | 2 new regression tests | Assert no address-component leaks into street field |
| `backend/routes/admin/drivers.py` | `_encrypt_driver_pii()` before drivers-table write; new `missing_license` filter param | Fix plaintext PII write; power backfill queue |
| `backend/tests/test_admin_business_logic.py` | New encryption regression test | Assert plaintext never reaches `update_one` |
| `backend/tests/test_admin_extended.py` | 2 new filter tests | Assert `missing_license` filter shape + 400 on combo with `search` |
| `admin-dashboard/src/app/dashboard/driver-license-backfill/page.tsx` | New page | Backfill queue UI |
| `admin-dashboard/src/lib/api.ts` | `getDrivers` opts gained `missing_license` | Wire new filter |
| `admin-dashboard/src/components/sidebar.tsx` | New nav child under Drivers | Discoverability |
| `docs/proposals/2026-07-29-driver-document-ocr-onboarding-automation.md` | New | Root-cause writeup + automation proposal |
| `ACTION_ITEMS.md` | B14 entry added/updated | Tracking |

## 7. Before / after

```python
# Before (admin_update_driver)
if driver_updates:
    await db_supabase.update_one("drivers", {"id": driver_id}, driver_updates)
```
```python
# After
if driver_updates:
    await db_supabase.update_one("drivers", {"id": driver_id}, await _encrypt_driver_pii(driver_updates))
```

```python
# Before (sgi_form_filler.py company fields)
_COMPANY_ADDRESS = "#200, 1956 BROAD STREET, REGINA, SASKATCHEWAN, CANADA, S4P 1Y1"
_DRIVER_COMPANY_FIELDS = {"Company name": _COMPANY_NAME, "Street address": _COMPANY_ADDRESS, ...}
```
```python
# After
_COMPANY_STREET = "#200, 1956 Broad Street"
_COMPANY_CITY = "Regina"
_COMPANY_PROVINCE = "SK"
_COMPANY_POSTAL = "S4P 1Y1"
_DRIVER_COMPANY_FIELDS = {
    "Company name": _COMPANY_NAME, "Street address": _COMPANY_STREET,
    "City/town": _COMPANY_CITY, "Provincestate": _COMPANY_PROVINCE,
    "Postalzip code": _COMPANY_POSTAL, ...
}
```

## 8. Rollback plan

- All four commits are plain `git revert`-safe — no migration, no destructive data change. Reverting restores the prior (buggy) behavior exactly.
- **Known gap, not covered by this change:** any `license_number` already written as plaintext via the admin route *before* this fix remains plaintext in the DB (this fix only stops new plaintext writes, it does not remediate existing rows). Not addressed here — flagging explicitly so it isn't assumed fixed. A follow-up should audit `drivers.license_number` values for ones that fail `_vault_decrypt` (i.e., look like a raw licence number rather than a vault UUID) and re-encrypt them.

## 9. Verification performed

- [x] Automated tests run: `test_sgi_form_filler.py` (10 pass), `test_admin_business_logic.py` (68 pass), `test_admin_extended.py` (57 pass) — all via `pytest --no-cov` against real (non-mocked-away) PDF templates and mocked Supabase.
- [x] Manual repro: generated real D00032/D00033 PDFs post-fix and read every field back via `pypdf.PdfReader` — confirmed each address component lands in its correct field.
- [x] Real production build: `npm run build` completed successfully; `/dashboard/driver-license-backfill` compiled and routed.
- [x] `ruff check` clean on all touched backend files; `eslint` clean on all touched frontend files (2 pre-existing warnings elsewhere, unrelated).
- [x] Blast-radius grep performed: all callers of `sgi_form_filler`, `admin_update_driver`, and `GET /admin/drivers` enumerated (see §4).
- [x] Reviewed against CLAUDE.md conventions: PIPEDA (encryption fix, address data-minimization already covered by an earlier PR), no auth/RLS/state-machine/money involvement.
- [ ] Feature-flagged: **not flagged**. Justification: the address fix and encryption fix are both bug fixes restoring intended behavior (no new user-visible surface to gate); the backfill page is a net-new internal admin tool with no existing behavior to protect, gated by the existing `drivers` module permission like every other admin screen — not a candidate for `app_settings` flagging per CLAUDE.md's own guidance (flags are for user-visible/behavior-changing rollouts, not internal tooling additions).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert; existing-plaintext gap explicitly called out as NOT covered)
- [x] Blast radius is stated, not assumed (§4)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5) — the encryption fix is the one behavior change to an existing flow (`admin_update_driver`), and it's a bug fix restoring intended behavior, not a new one
