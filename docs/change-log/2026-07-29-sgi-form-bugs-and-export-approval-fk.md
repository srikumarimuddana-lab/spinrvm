# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin, drivers |
| PR / commit link | (this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B10; live-user bug report on SGI D00032/D00033 forms, Export Approvals, Compliance email |

## 1. Issue / gap identified

Four separate real bugs, reported by a live admin user testing the SGI compliance-form and export-approval features shipped this session:

1. Generated SGI D00032 forms showed the literal text **"None"** in the Licence number/Licence class fields for drivers with a NULL `license_number`/`license_class`, instead of a blank field.
2. Generated D00032/D00033 forms showed **stale leftover data on unused rows** (e.g. a "Feb-20-2026" date, a pre-selected "Add" radio) that had nothing to do with the actual submission.
3. Requesting more drivers than a form's row limit (10 for D00032, 16 for D00033) just refused outright instead of splitting into multiple documents.
4. Creating an export-approval request failed with **"could not upload the queue — DB operations failed."**
5. Emailing a Compliance report failed with a generic **"Could not generate report / Internal Server Error"** toast, and the toast title was misleading (it said "generate" for a send failure).
6. Rows on generated forms came out in arbitrary DB order, not a predictable order.

## 2. Root cause

1. `sgi_field_maps.py` used `driver.get("license_number", "")` — `.get(key, default)`'s default only applies when the *key is missing*. A NULL DB column still returns a dict with the key present and value `None`, so `None` flowed straight through into the PDF field, and pypdf rendered it as the literal string `"None"`.
2. The real SGI template PDFs (`D00032_driver_details_template.pdf`, `D00033_vehicle_details_template.pdf`) ship with **stale baked-in `/V` values on every row slot** — confirmed via `PdfReader.get_fields()` (e.g. `AddOrRemove_2`/`_3`/`_4` all default to "Add" selected; every `VechicleDateN_af_date` defaults to `"Feb-20-2026"`), almost certainly a leftover from whatever real filled-out form was used to build the template. `fill_driver_details_form`/`fill_vehicle_details_form` only wrote field values for rows actually supplied — rows beyond that were never touched, so they silently kept showing the template's stale data.
3. No chunking logic existed anywhere in the SGI-forms flow — `SgiFormsTab.tsx` client-side blocked with an error message the moment the selection exceeded a form's row limit.
4. Migration 268 declared `admin_export_approval_requests.requested_by TEXT NOT NULL REFERENCES users(id)` (and the same FK on `decided_by`). Per CLAUDE.md's documented JWT trust model, **platform-admin identity is not a `users` row** — `admin["id"]` from `get_admin_user`/`require_super_admin` is either `admin_staff.id` or the `"admin-001"`/`"break-glass"` env-var-creds sentinels, none of which reliably exist in `users`. Every real admin's `create_request` call hit Postgres 23503 (FK violation), surfacing as the generic DB-operations-failed error. This is the exact same bug class migrations 213 and 214 already fixed for `kyb_reviewed_by` and `corporate_wallet_transactions.actor_user_id` — migration 268 reintroduced it.
5. `compliance/page.tsx` funneled both download and email failures through the same `onError` handler titled "Could not generate report," so a send-step failure read as a generation failure. Separately, `_deliver_report` in `compliance.py` had no `try/except` around `send_transactional_email` — an unhandled exception there (its docstring promises it never raises, but nothing enforced that) fell through to FastAPI's default unhandled-500 response, which the frontend rendered as "Internal Server Error."
6. `sgi_forms.py`'s route resolved drivers via `db_supabase.get_rows("drivers", {"user_id": {"$in": ids}})`, which does not preserve `driver_ids`' input order (itself just click order in Search & Select) — DB return order is arbitrary from the caller's perspective.

## 3. Fix / remediation

1. `sgi_field_maps.py`: changed every `.get(key, "")` on a nullable driver/vehicle column to `.get(key) or ""`, which also blanks an explicit `None`.
2. `sgi_form_filler.py`: both fill functions now explicitly write blank values (`""` for text/date fields, `"/Off"` for radio/checkbox fields — verified `/Off` is accepted by pypdf even though it's not enumerated in the templates' own `/_States_` list) for every row from `len(items)+1` through `MAX_DRIVER_ROWS`/`MAX_VEHICLE_ROWS`.
3. `SgiFormsTab.tsx`: a selection larger than a form's row limit is now split into consecutive chunks (`chunk()` helper) and generates one PDF download per chunk (`_1.pdf`, `_2.pdf`, …) instead of refusing.
4. New migration `270_export_approvals_admin_id_no_fk.sql` drops both FK constraints (no column type change needed — both were already `TEXT`). The self-approval `CHECK` constraint is untouched.
5. `compliance.py`: wrapped the `send_transactional_email` call in `_deliver_report` in `try/except`, logging the real exception with `exc_info=True` and surfacing the existing clean 502 instead of an unhandled 500. `compliance/page.tsx`: added a distinct `onEmailError` handler titled "Could not email report," wired to all three email-flow catch blocks.
6. `sgi_forms.py`: sorts `driver_rows` by `name` (case-insensitive) right after PII decryption, before mapping to form rows.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the SGI-forms/export-approvals/compliance-email code paths.** Grepped for other callers:
  - `sgi_field_maps.driver_to_driver_details_row`/`driver_to_vehicle_details_row`: only called from `routes/admin/sgi_forms.py` — no other consumer.
  - `sgi_form_filler.fill_driver_details_form`/`fill_vehicle_details_form`: only called from the same route (via `FORM_FILLERS`).
  - `admin_export_approval_requests` table: only written/read by `services/admin_export_approvals.py`, which is only called from `routes/admin/export_approvals.py`, `routes/admin/compliance.py`, and `routes/admin/data_transfer_export.py` (all part of the same B10 feature, still dark-launched behind `dual_approval_exports_enabled = false`).
  - `_deliver_report`/`onError` in compliance.py/page.tsx: used by all three Compliance report types (GST/PST, insurance-period audit, Knight Archer onboarding) — the email-error-title fix applies uniformly, no behavior change to the download path.
- No ride, dispatch, payment, or corporate-billing code touched.
- The migration is a `DROP CONSTRAINT IF EXISTS` — safe to run against production traffic; does not lock or rewrite the table, and the `IF EXISTS` guard makes it a no-op if constraints were already absent.
- Sorting `driver_rows` by name is a pure ordering change with no behavioral side effect on the filled values themselves.

## 5. User-experience effect

- **Internal admin only** — no rider/driver/corporate-admin-facing surface touched.
- Admin generating SGI forms: license number/class now render blank instead of "None" for the 22 backfill-pending drivers (ACTION_ITEMS.md B14); unused rows render fully blank instead of showing stale leftover dates/selections; a >10/>16-driver selection now produces multiple numbered PDF downloads instead of an error.
- Admin using the Export Approvals queue: creating a request that previously always failed with a DB error now succeeds (once migration 270 is applied).
- Admin emailing a Compliance report: a genuine send failure now shows "Could not email report" (with the real underlying reason) instead of the misleading "Could not generate report."
- None of this is visible mid-session to a rider or driver.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/data_transfer/sgi_field_maps.py` | `.get(key, "")` → `.get(key) or ""` for all nullable driver/vehicle fields | Fix #1 |
| `backend/services/data_transfer/sgi_form_filler.py` | Explicitly blank every unused row (text/date → `""`, radio/checkbox → `"/Off"`) | Fix #2 |
| `backend/routes/admin/sgi_forms.py` | Sort `driver_rows` by name after decryption | Fix #6 |
| `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx` | Chunk oversized selections into multiple PDF downloads instead of refusing | Fix #3 |
| `backend/migrations/270_export_approvals_admin_id_no_fk.sql` (new) | Drop `requested_by`/`decided_by` FKs to `users(id)` | Fix #4 |
| `backend/routes/admin/compliance.py` | `try/except` around `send_transactional_email` in `_deliver_report`, logging the real exception | Fix #5 |
| `admin-dashboard/src/app/dashboard/compliance/page.tsx` | New `onEmailError` handler (distinct title), wired to all 3 email flows | Fix #5 |
| `backend/tests/test_sgi_form_filler.py` | 4 new regression tests (None-value blanking, unused-row blanking ×2) | Test coverage for fixes #1/#2 |

## 7. Before / after

```python
# Before (sgi_field_maps.py)
"licence_number": driver.get("license_number", ""),

# After
"licence_number": driver.get("license_number") or "",
```

```sql
-- Before (migration 268)
requested_by TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

-- After (migration 270, additive)
ALTER TABLE admin_export_approval_requests
    DROP CONSTRAINT IF EXISTS admin_export_approval_requests_requested_by_fkey;
```

## 8. Rollback plan

- SGI form-filler/field-map fixes and the sort-by-name change: plain `git revert` — pure code, no data touched, no flag involved.
- `SgiFormsTab.tsx` chunking: plain `git revert` — frontend-only, no API contract change (each chunk is a normal existing single-form request).
- Migration 270: **not** a plain `git revert` once applied — re-adding the FK requires `ALTER TABLE ... ADD CONSTRAINT ... REFERENCES users(id)`, which would immediately fail again for the same reason unless every existing `requested_by`/`decided_by` value happens to match a `users.id` (unlikely, since the whole point is they don't). If migration 270 itself needs to be undone, the correct rollback is to re-run migration 268's original constraint DDL as a new migration — never re-apply 270 in reverse by hand.
- Compliance email fixes: plain `git revert` — the `try/except` only changes what the admin sees on an already-failing send, and the toast title change is display-only.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_sgi_form_filler.py` (13 passed, 4 new), `pytest backend/tests/test_sgi_forms_route.py` (11 passed, unaffected by the sort change), `pytest backend/tests/test_admin_export_approvals.py backend/tests/test_export_approvals_routes_http.py` (20 passed — these mock `db_supabase`, so they could not have caught the real FK violation; the migration is the actual remediation for bug #4, not a unit test).
- [x] Real production build (`npm run build`) run for `admin-dashboard` — succeeded, all pages including `/dashboard/compliance` and `/dashboard/data-transfer` compiled.
- [x] `ruff check` on all touched backend files — clean.
- [x] Manually generated both D00032 and D00033 PDFs via the real templates (not mocked) with a partial row count and confirmed via `PdfReader` that unused rows and NULL license fields render blank as expected.
- [ ] Not run against the real Supabase instance — migration 270 has not been applied to staging/production in this session; the FK-violation diagnosis is based on reading the schema (migration 268) against the documented admin-identity model (CLAUDE.md JWT trust model, migrations 213/214's identical prior fix), not a live repro.

## 10. What was NOT verified / deferred

- **Data Transfer export button "won't activate"**: investigated `ExportTab.tsx`'s disabled logic (`!hasSelection || !reasonValid || loading`) and the shared `useEntitySelection` state (correctly lifted to the parent page, shared across tabs) — found no code bug. The button requires a Reason field of 10–200 characters, which is documented in the UI hint text but easy to miss. No fix applied; flagging as a possible UX-discoverability issue rather than a confirmed bug, since I could not reproduce a case where a valid selection + valid reason still left the button disabled.
- **Compliance-forms header alignment / logo size**: not addressed — pure CSS/layout polish, out of scope for this pass given the number of functional bugs already in this batch; no visual regression tooling exists in this repo (see `docs/change-log/2026-07-28-*` and CR #2829) so any fix here would be reasoned about, not screenshotted, and I did not want to ship an unverified visual change alongside functional fixes.
- The exact previous root cause of the compliance-email "Internal Server Error" (which specific line inside `send_transactional_email`/SES/Resend threw) was not confirmed via live logs — the fix wraps the call defensively and logs loudly so the *next* occurrence is diagnosable, but the original stack trace was not available in this session.
