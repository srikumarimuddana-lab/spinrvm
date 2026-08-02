# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-30 |
| Author | Claude Code (agent) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | drivers / admin |
| PR / commit link | branch `claude/driver-license-work-auth-edit-n748bk` |
| Related issue or gap ID | Operator report: no way to edit a driver's licence number; four separate "Unknown" compliance rows; missing Spinr-approval date |

## 1. Issue / gap identified

Three gaps in the admin **Drivers → detail slideout → Compliance & Import Data** panel:

1. **Licence number is not editable and not visible.** The backend `PUT /api/admin/drivers/{id}` already accepted `license_number`, but the dashboard never rendered a field for it, so an operator correcting a mistyped/updated licence had no path short of a direct DB write.
2. **Work authorization renders as four disconnected "Unknown" rows.** `work_authorization_status`, `is_permanent_resident`, `is_citizen` were three independently-editable fields describing one mutually-exclusive fact, so a driver could be saved as `citizen` *and* `is_permanent_resident = true`, and an unset driver showed "Unknown" three times over.
3. **"Spinr approved" and its date were not surfaced at all.** Only *Authority Approved* (SGI/regulator) was shown, so operators could not see whether **Spinr** had approved the driver or when.

## 2. Root cause

1. Oversight when the compliance panel was built — `license_number` was added to the backend allow-list (and to `_encrypt_driver_pii`) but no corresponding input was added. It is also Vault-encrypted at rest, so the bulk drivers list only ever carried the opaque token; there was no decrypted value the panel *could* have rendered.
2. The three columns arrived together from the Saskatoon bulk import (migration `221_drivers_bulk_import_fields.sql`), where the CSV genuinely had three separate columns. The admin UI mirrored the CSV shape instead of the domain fact. The backend did attempt to keep them in sync, but with `setdefault`, so an explicit boolean in the same payload silently beat the status the operator had just chosen.
3. **There is no `drivers.spinr_approved` column** — this was verified against `information_schema.columns` in a prior change (`docs/change-log/2026-07-28-data-transfer-sgi-fixes-and-features.md`) and re-confirmed here by grepping every migration. The real columns are **`drivers.is_verified`** (bool) and **`drivers.verified_at`** (timestamptz, added by `12_driver_lifecycle_status.sql`). The bulk importer's CSV column `spinr_approved` is folded into `is_verified` at import time (`driver_import_service.py`). `spinr_approved` *does* exist, but only as a field on the **external LMS payload** (`DriverTraining.lms.driver.spinr_approved`), which is a different system. So no migration was needed — the data was there, just never rendered.

## 3. Fix / remediation

- **Licence number**: added an editable *License Number* field to the compliance panel. It is **write-only** — it starts blank with a `•••• 1234` placeholder, and blank means "leave the number on file unchanged". The read-only view shows the same last-4 mask. `GET /drivers/{id}/live-stats` now decrypts the single selected driver and returns `license_number_last4` + `license_number_on_file`; the full number never leaves the backend.
- **Work authorization**: `work_authorization_status` is now the single field an operator picks (Canadian citizen / Permanent resident / Work permit — no expiry / Work permit — expires / Unknown). The `is_citizen` / `is_permanent_resident` columns are kept but are strictly **derived**: the status now *overrides* rather than `setdefault`s them, an unrecognised status is rejected with 400, and `""`/`unknown` normalizes the column to `NULL`. The panel renders the two derived flags as **"Not applicable"** when a different category was chosen, and "Unknown" only when the status genuinely is. A new backend helper `work_authorization_view()` ships the same projection as `work_authorization` on every driver list row so the shape is defined server-side, not only in the dashboard.
- **Spinr approved**: added read-only *Spinr Approved* (`is_verified`) and *Spinr Approved At* (`verified_at`) rows next to the existing *Authority Approved* pair, and relabelled the corresponding CSV export headers ("Verified" → "Spinr Approved", "Approved As Driver" → "Spinr Approved At") so the export and the panel agree.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface (backend + admin-dashboard), but confined to the drivers admin domain. No ride, dispatch, payment, wallet, or insurance-period code path is touched.**

Consumers of the changed fields, grepped and enumerated:

| Field | Every other reader/writer | Effect |
|---|---|---|
| `work_authorization_status`, `is_citizen`, `is_permanent_resident` | `services/driver_import_service.py` (bulk import writes all three from CSV, via `work_auth_status()`); `routes/admin/rides.py` `admin_export_drivers` (reads all three into the CSV); `admin-dashboard/.../drivers/page.tsx` (panel + CSV header list) | Import path **unchanged** — it writes the row directly, not through `PUT /drivers/{id}`, and it already derives the three consistently. Export **unchanged** (still raw columns), and now can no longer receive a self-contradicting row from the admin edit path. |
| `license_number` | `routes/drivers/_shared.py` `_VAULT_PII_FIELDS` (encrypt/decrypt); driver self-serve profile update; `routes/admin/sgi_forms.py`; `routes/admin/compliance.py`; `services/data_transfer/entity_export_service.py`; `admin_export_drivers` (masks to last-4); `admin_get_drivers?missing_license=true` backfill filter | Purely additive. The admin write already went through `_encrypt_driver_pii`; nothing about the encryption contract changed. The new read is a decrypt of **one** driver on slideout open. |
| `is_verified` / `verified_at` | `admin_driver_action` (approve/unban/reactivate — writes both), `admin_verify_driver` (writes `is_verified` only), `admin_override_driver_status` (writes `is_verified` only), dispatch partial index `55_drivers_dispatch_partial_index.sql`, `12_driver_lifecycle_status.sql` backfill | **Read-only surfacing — no write path added.** See "not verified" below for the known staleness this exposes. |
| `DriverLiveStats` (`/live-stats`) | Only `admin-dashboard/.../drivers/page.tsx` | Two additive response fields. |

Regression risks and how they are handled:

- **A stale `is_citizen`/`is_permanent_resident` could now be overwritten.** This is the intended fix, but it means an admin who edits *any* compliance field while the status select is populated will rewrite the two booleans from the status. That is correct by construction (the select is pre-populated from the row's own status, and from the legacy booleans when the status is empty — see `work_authorization_view`'s legacy promotion), so a driver imported with only `is_citizen = true` round-trips to `citizen` rather than being zeroed.
- **`unknown` no longer writes `false`.** The old code wrote `is_citizen = False, is_permanent_resident = False` for an unknown status — a confident "not a citizen" claim about a driver nobody had checked. It now writes `NULL`. Anything reading those booleans must already tolerate `NULL` (the columns are nullable and default `NULL` from migration 221); the export renders it as an empty cell rather than `False`.
- **Background loops**: none touched. No new loop, no change to `lifespan.py`.
- **No new PII egress.** The licence number is masked to last-4 on the way out, the same rule `admin_export_drivers` already applies; the full value is never returned by any endpoint. If Vault cannot decrypt, `_vault_decrypt` returns the raw token, and the mask is suppressed (`license_number_last4 = null`) rather than showing 4 characters of ciphertext as if they were the licence.

## 5. User-experience effect

- **Internal admin only.** Riders, drivers, and corporate admins see nothing — no driver-app or rider-app code was touched, and no notification copy changed.
- Not visible mid-session to anyone using the rider/driver apps.
- Visible to an admin with the drivers slideout already open: the Compliance & Import Data section gains *License Number*, *Spinr Approved*, *Spinr Approved At*; loses the standalone *Permanent Resident* / *Citizen* dropdowns in edit mode (they remain as read-only derived rows); and *Work Authorization Status* is relabelled *Work Authorization* with human-readable options ("Canadian citizen" rather than "citizen").
- Drivers-CSV export headers change wording for two existing columns (`is_verified`, `approved_at`). Column keys and values are unchanged — only the header text — so any downstream sheet keyed by position is unaffected; one keyed by header text would need the new label.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/drivers.py` | Added `WORK_AUTHORIZATION_CHOICES`, `normalize_work_authorization_status()`, `derived_work_authorization_flags()`, `work_authorization_view()`, `_mask_license_number()`; ship `work_authorization` on every list row; validate + force-derive in `admin_update_driver`; return `license_number_last4` / `license_number_on_file` from `/live-stats`; import `_vault_decrypt` | One canonical work-auth field defined server-side; give the panel a safe licence value to display |
| `backend/tests/test_admin_drivers_coverage.py` | 5 new `TestUpdateDriver` cases + `TestWorkAuthorizationView` + `TestLiveStatsLicenseMask` | Cover the override-not-setdefault change, the `unknown` → `NULL` change, 400 on invalid status, licence encryption on the admin path, and that the full licence / ciphertext never leaks |
| `admin-dashboard/src/lib/api/drivers.ts` | `DriverLiveStats` gains `license_number_last4`, `license_number_on_file` | Type the new response fields |
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | Added `WORK_AUTH_LABELS` / `workAuthLocal()` / `workAuth()` / `WORK_AUTH_FLAG_LABELS`; write-only License Number edit field + masked read row; single Work Authorization select; derived PR/Citizen rows; Spinr Approved + Spinr Approved At rows; `EditField` gained `placeholder`/`hint`; export header relabels; `saveEdits` handles write-only fields and re-derives after save | Deliver the three operator-facing changes |
| `docs/change-log/2026-07-30-admin-driver-license-and-work-auth.md` | This log | CLAUDE.md mandatory entry |

No migration was added — all four columns involved (`license_number`, `work_authorization_status`, `is_verified`, `verified_at`) already exist.

## 7. Before / after

**Work-authorization derivation (`backend/routes/admin/drivers.py`)**

```python
# Before — setdefault: an explicit boolean in the same payload beat the status,
# and "unknown" was written as a confident False/False.
if "work_authorization_status" in driver_updates:
    status = str(driver_updates.get("work_authorization_status") or "").strip().lower()
    if status == "citizen":
        driver_updates.setdefault("is_citizen", True)
        driver_updates.setdefault("is_permanent_resident", False)
    elif status == "permanent_resident":
        driver_updates.setdefault("is_permanent_resident", True)
        driver_updates.setdefault("is_citizen", False)
    elif status in {"expiring", "indefinite", "unknown", ""}:
        driver_updates.setdefault("is_permanent_resident", False)
        driver_updates.setdefault("is_citizen", False)
```

```python
# After — status is authoritative, unrecognised values are rejected, and
# "unknown" means NULL (unknown), not False (checked and negative).
if "work_authorization_status" in driver_updates:
    raw_status = driver_updates.get("work_authorization_status")
    status = str(raw_status or "").strip().lower()
    if status and status not in WORK_AUTHORIZATION_CHOICES:
        raise HTTPException(status_code=400, detail=...)
    driver_updates["work_authorization_status"] = None if status in ("", "unknown") else status
    driver_updates.update(derived_work_authorization_flags(status))
```

**Compliance panel read view (`admin-dashboard/.../drivers/page.tsx`)**

```tsx
// Before — three independent columns, three separate "Unknown"s.
<DetailField label="Work Authorization" value={selected.work_authorization_status ? selected.work_authorization_status.replace(/_/g, " ") : "Unknown"} />
<DetailField label="Permanent Resident" value={selected.is_permanent_resident === true ? "Yes" : selected.is_permanent_resident === false ? "No" : "Unknown"} />
<DetailField label="Citizen" value={selected.is_citizen === true ? "Yes" : selected.is_citizen === false ? "No" : "Unknown"} />
```

```tsx
// After — one status drives both derived rows; the others read "Not applicable".
<DetailField label="Work Authorization" value={workAuth(selected).expires_at
    ? `${workAuth(selected).label} ${fmtDate(workAuth(selected).expires_at!)}`
    : workAuth(selected).label} />
<DetailField label="Permanent Resident" value={WORK_AUTH_FLAG_LABELS[workAuth(selected).permanent_resident] || "Unknown"} />
<DetailField label="Citizen" value={WORK_AUTH_FLAG_LABELS[workAuth(selected).citizen] || "Unknown"} />
```

## 8. Rollback plan

No migration, no schema change, no flag, and **no write to live data that cannot be re-entered by an operator**. Rollback is a `git revert` of the commit + redeploy of backend and admin-dashboard, which is sufficient here because:

- The only new write path is the licence-number edit, which stores a value an admin typed and can re-type; it does not touch money, ride state, wallet deltas, or insurance-period rows.
- The work-auth derivation change can leave rows where `is_citizen` / `is_permanent_resident` moved from `false` → `NULL` (for a status of `unknown`). Reverting the code does not restore those, and does not need to: `NULL` is the honest value, both columns are nullable, and every reader already handles `NULL`. Data-level restore if ever required:
  ```sql
  -- Re-assert the old (less accurate) semantics for rows with no status on file.
  UPDATE public.drivers
     SET is_citizen = COALESCE(is_citizen, FALSE),
         is_permanent_resident = COALESCE(is_permanent_resident, FALSE)
   WHERE work_authorization_status IS NULL;
  ```
- Not feature-flagged: the change is admin-only, affects no rider/driver-facing surface, and per CLAUDE.md gate 3 the flag requirement targets user-visible and shared-component changes. `EditField` is the one shared component touched, and its two new props are optional with no behavior change when omitted — every existing call site is unaffected.

## 9. Verification performed

- [x] **Automated tests run** — `pytest backend/tests/test_admin_drivers_coverage.py` (unit tier, all Supabase/Vault mocked). New cases: status overrides a contradicting explicit boolean; `""`/`unknown` → `NULL` for the status and both flags; work-permit statuses → `False`/`False`; invalid status → 400; admin licence write goes through `_encrypt_driver_pii`; `/live-stats` returns last-4 only, suppresses the mask when Vault cannot decrypt, and reports `license_number_on_file = false` when absent; and the pure `work_authorization_view` projection across all five statuses plus the two legacy boolean-only shapes.
- [x] **Real production build run** — `npm run build` (`next build`) in `admin-dashboard`, **not** just `tsc --noEmit`. Note: the first run failed on a **pre-existing, unrelated** missing dependency — `motion` is declared in `admin-dashboard/package.json` but was absent from `node_modules`, breaking `src/app/dashboard/monitoring/alert-feed.tsx`. Installing the already-declared version fixed it; `package.json` and `package-lock.json` are unmodified.
- [x] **Wider backend selection run** — `pytest -m "not slow" -k "admin or driver"`: **1364 passed, 1 failed**. The single failure is `test_e2e_ride_lifecycle.py::TestRideLifecycleConcurrency::test_two_drivers_accepting_same_ride_one_wins` (`'coroutine' object has no attribute 'data'` in `routes/drivers/ride_flow.py:327`), which is **pre-existing and unrelated** — confirmed by stashing this branch's changes and reproducing the identical failure on a clean tree. Also ran `test_admin_business_logic.py`, `test_driver_import_service.py`, `test_admin_driver_photo.py`, `test_sgi_forms_route.py`, `test_entity_export_service.py`: 85 passed.
- [x] `ruff check` + `ruff format --check` clean on both changed backend files.
- [x] **Dual-import pattern honoured** — `_vault_decrypt` is imported in *both* branches of the `try/except ImportError` block. The first pass added it to the relative-import branch only, which the new `/live-stats` tests caught immediately (`AttributeError: module 'routes.admin.drivers' does not have the attribute '_vault_decrypt'`) — under `python -m backend.server` that would have been a `NameError` at request time, not import time.
- [x] `tsc --noEmit` shows zero errors in either changed frontend file (the repo has pre-existing errors in unrelated test files and `alert-feed.tsx`).
- [x] **Blast-radius grep performed** — searched for `work_authorization`, `work_authorization_status`, `is_permanent_resident`, `is_citizen`, `decals_sent`, `license_number`, `spinr_approved`, `is_verified`, `verified_at`, `approved_at` across `backend/`, `admin-dashboard/`, `docs/`, and `backend/migrations/`. Every consumer is enumerated in §4.
- [x] **Reviewed against CLAUDE.md conventions** — PIPEDA (licence number masked to last-4, never logged, never returned in full; no new PII in logs); "do not silently swallow errors" (the `/live-stats` decrypt failure suppresses the *mask*, and `_vault_decrypt` still logs at `error`); migration conventions (N/A, no migration); ride state machine / money (untouched).
- [ ] **Manual repro in staging** — not performed; see below.

## 10. What was NOT verified

- **Not exercised against a live Supabase or a real Vault.** Every test mocks `db_supabase` and `_vault_decrypt`. The decrypt-path behavior on a real `decrypt_driver_pii` RPC (including whether legacy pre-Vault rows store plaintext rather than a token — in which case `_vault_decrypt` returns the value unchanged and the panel will correctly show "On file (unreadable)" rather than the real last-4) has **not** been confirmed against production data.
- **No staging click-through.** The panel changes were verified by a passing production build and typecheck, not by opening the slideout.
- **No visual/snapshot regression tooling exists for `admin-dashboard`**, so the layout impact of the added/removed rows in the Compliance grid was reasoned about, not screenshotted. This is a standing repo-wide gap, not specific to this change.
- **Pre-existing gap found but deliberately NOT fixed here — `verified_at` can be stale.** Only `admin_driver_action` (`approve` / `unban`) stamps `verified_at`. `admin_verify_driver` (`POST /drivers/{id}/verify`) and `admin_override_driver_status` (`PUT /drivers/{id}/status`) both write `is_verified` **without** it, so a driver approved through either path will show *Spinr Approved: Yes* with *Spinr Approved At: —*. `admin_verify_driver`'s docstring further claims `drivers` has no `verified_at` column at all, which contradicts `12_driver_lifecycle_status.sql` and contradicts `admin_driver_action` writing it today — one of the two is wrong and I could not resolve it without querying the production `information_schema`. Adding the write blind risks a PGRST204 → 500 on a live driver-approval path, so per CLAUDE.md gate 9 it is **escalated, not shipped**: this change only *reads* the column. Follow-up needed to (a) confirm the column against production, (b) correct or delete the stale docstring, and (c) stamp `verified_at` on the other two approval paths.
- **`decals_sent` was intentionally left as its own field.** It appeared in the same block of "Unknown" rows, but it is an operations shipping flag with no relationship to immigration status, so folding it into the work-authorization selector would have been wrong. It remains a separate boolean + date.
