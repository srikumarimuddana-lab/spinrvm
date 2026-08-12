# Change Impact & Risk Log — Driver bulk-import service (retroactive backfill)

> **This is a documentation backfill, not a new change.** `driver_import_service.py`
> and its two entry points have been live for some time (Saskatoon launch
> onboarding). ACTION_ITEMS.md **A28 (P2-B)** flagged that — unlike
> `booking_import_service.py` and `stripe_mapping_import_service.py`, which each
> have a runbook/change-log — this importer, which writes directly to `auth`/
> `users`/`drivers`/`driver_documents`, had none. This document describes the
> **existing, already-shipped** behavior as of 2026-08-12; it does not itself
> change any code.

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 (backfill; original code predates this date) |
| Author | Claude Code (agent-assisted) |
| Surface(s) | backend (+ read-only effect on admin-dashboard, driver-app) |
| Domain (Sentry tag) | drivers, admin |
| PR / commit link | branch `claude/a28-p2b-import-service-docs` |
| Related issue or gap ID | ACTION_ITEMS.md A28, sub-finding P2-B |

## 1. Issue / gap identified

`driver_import_service.py` is a shared core (`build_plan` / `commit_plan`) used by two entry points that both write live driver/user rows, and it had no Change Impact & Risk Log entry — the only documentation for its behavior was the module's own docstring and inline comments. For a path that creates production driver accounts and gates their ability to go online, that's a real documentation gap on a live-tested surface.

## 2. Root cause

Not a defect — a documentation gap. This importer predates the Change Impact Log convention becoming mandatory for live-tested-surface changes, and unlike `booking_import_service.py` (built after the convention existed) it was never backfilled.

## 3. Fix / remediation

This document. No code changed.

### What the service does

`backend/services/driver_import_service.py` holds the pure parsing/validation (`build_plan`) and write (`commit_plan`) core, shared by two entry points:

- **CLI** — `scripts/import_saskatoon_drivers.py` (drivers CSV + a local documents folder + a documents CSV). Supports `--dry-run` and a commit mode.
- **Admin dashboard** — `POST /api/admin/drivers/import/{validate,commit}` (`routes/admin/driver_import.py`). Drivers CSV only — document *files* are uploaded per-driver afterward through the existing manual document-upload endpoint, not as part of this flow (`build_plan` is called with `files_root=None`, so any document row in this path is rejected with a validation error rather than silently ignored).

Both entry points call the same `build_plan` → (review report) → `commit_plan` two-phase flow: `build_plan` never writes; `commit_plan` refuses outright if `plan.errors` is non-empty (`raise RuntimeError("refusing to commit with validation errors")`), so a partially-valid CSV cannot partially commit.

**Admin HTTP path specifically** requires `get_admin_user` (admin auth), rate-limits the commit call (`driver_import_commit_limit`), and binds a `validation_token` (HMAC-signed, scoped to `batch + sha256(csv) + admin_id`) from the prior `/validate` call — `/commit` refuses without a token proving validate ran against this exact file content, closing a gap where commit could otherwise accept any CSV with no proof a dry run ever happened. Caps: 1 MB CSV, 500 rows per import.

**Validation performed** (all in `build_plan`, before any write):
- Required columns present; no duplicate `old_driver_id` within one CSV.
- Row is scoped to the resolved service area (rejects a row that names a different area).
- `phone` — normalized to E.164, then format-validated against the same `^\+1\d{10}$` shape `SendOTPRequest`/`VerifyOTPRequest` require at signup.
- `email` (optional) — structurally validated (`^[^\s@]+@[^\s@]+\.[^\s@]+$`) if present; deliberately permissive (one-time CLI-operator input, not a live user-facing form) rather than full RFC 5322 grammar.
- `vehicle_type` must resolve against `vehicle_types` (by name or id).
- `date_of_birth`, if present, must parse under one of the accepted formats; every date field (`DRIVER_DATE_FIELDS`) is additionally checked for day-first/month-first **ambiguity** (e.g. `03/04/25`) and surfaced as a warning rather than silently picking one interpretation, since these dates gate document expiry / `go_online`.
- `vin`, if present, is format/checksum-validated via the existing `validators.validate_vin` (17-char ISO 3779, I/O/Q excluded) — the same helper used for live vehicle registration elsewhere — and normalized to uppercase.
- A document row (CLI path only) whose `status == "approved"` **and** whose `expiry_date` has already passed is rejected outright — an operator cannot accidentally import an already-expired document as pre-approved. (A `pending` row with the same past date still imports; the real runtime gate is `go_online`'s own expiry re-check, `routes/drivers/status.py`, so this is defense-in-depth, not the sole protection.)

**Matching / re-run safety:** existing users/drivers are matched by phone (then by email for users). If a phone/email match is found but it is **not** this importer's own prior row (`legacy_import_metadata.source == IMPORT_SOURCE` + matching `old_driver_id`), the row is rejected with `"matching user or driver already exists; handle manually before import"` — the importer never silently overwrites an unrelated existing account. If it **is** this importer's own prior row (a crashed or partial earlier run), the row resumes: vehicle-field changes are diffed and queued as an update (never approval/status/expiry — a re-upload cannot silently undo a post-import admin decision), and already-imported documents are skipped by re-checking `driver_documents` for the same `(requirement_key, side)` so a re-run converges instead of duplicating.

**What gets written** (`commit_plan`): a `users` row (role=driver), a `drivers` row (VIN written as plaintext per migration 244; `license_number` is vault-encrypted via `encrypt_driver_pii` RPC before write), then any queued vehicle-field updates to already-imported drivers, then document files (CLI path) uploaded to Supabase Storage and `driver_documents` rows inserted with the resulting signed URLs. Commit order is users → drivers → files → documents; a crash between the users and drivers inserts leaves an orphaned user row that surfaces as a "matching user... already exists" error on the next attempt and needs manual cleanup (documented in the module's own docstring, not new here).

**Driver activation gating:** a driver only lands in `status="active"` / `is_verified=True` if `regulatory_authority_approved` and `spinr_approved` are both true **and** (CLI path only) an approved document row already exists for them in this batch. The admin HTTP path never sets `has_import_documents=True` (it passes no document rows), so drivers imported through the dashboard always land in `needs_review` regardless of CSV approval flags, until documents are uploaded and approved through the normal per-driver flow afterward. This is intentional (module docstring: "Web imports intentionally create drivers before per-driver document uploads happen").

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface** (writes to core `users`/`drivers`/`driver_documents` tables read throughout the backend and both driver-app and admin-dashboard).

| Consumer | Effect of an imported driver row | Handling |
|---|---|---|
| `go_online` eligibility check (`routes/drivers/status.py`) | Re-checks document expiry / approval state at online time regardless of import-time flags | Unaffected by this importer either way — it's the real runtime gate |
| Signup / login (`routes/auth.py`) | A driver imported by this service can log in with the same phone-based OTP flow as any other account | Phone/email are format-validated at import so they match the shape the live OTP flow expects |
| Admin driver list/detail (`routes/admin/drivers.py`) | Filters/reads `legacy_import_metadata` in places (confirmed via grep — `test_admin_drivers_coverage.py`, `test_drivers_extended.py`, `test_base_multi_operator_filters.py` cover this) | Already handles the provenance field; not modified by this backfill |
| Dispatch / driver matching | An imported driver becomes dispatch-eligible exactly like any other driver once `is_available` conditions hold | No special-casing — intentional, an imported driver is a real driver |
| `driver_insurance_periods` | Not written by this importer | Correct — a driver has no ride history to derive a period from until they actually go online/take rides in Spinr |
| Rider-facing rating/history | Not written or affected | This importer touches `drivers`/`users`/`driver_documents` only, never `rides` |
| Document-expiry background loop (`utils/document_expiry.py`) | Will pick up imported drivers' expiry dates on its normal schedule | Same as any driver; not importer-specific |

No money, wallet, ride-state, or corporate-billing table is touched by this importer — it is scoped to identity/eligibility data only.

## 5. User-experience effect

- **Drivers**: an imported driver experiences no difference from a driver who signed up normally — same login flow, same eligibility gates, same app. The only import-specific state is `legacy_import_metadata` (internal, never surfaced to the driver).
- **Internal admin**: sees imported drivers in the normal driver list/detail views, with provenance visible via `legacy_import_metadata` where the admin UI/tests already read it.
- **Riders / corporate admin**: no effect.
- No copy or notification changes — this document adds no new behavior.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/change-log/2026-08-12-driver-import-service-backfill.md` | New (this file) | Backfills the missing runbook per ACTION_ITEMS.md A28 P2-B |

No source files were modified as part of this entry.

## 7. Before / after

Not applicable — documentation-only, no behavior-changing diff.

## 8. Rollback plan

Not applicable to this document itself (pure docs, `git-revert-safe`, trivially). For the importer's own operational rollback (removing a bad batch), the existing pattern used by every other legacy importer applies: every row this service writes is tagged with `legacy_import_metadata.batch`, so a bad batch's users/drivers/documents can be identified and manually removed by that batch ID. Unlike `booking_import_service.py`'s ride import, there is no money/payout offset to unwind here — removing a bad batch is a straightforward delete scoped by `legacy_import_metadata->>'batch' = '<batch>'` across `driver_documents` (first, to satisfy any FK), then `drivers`, then `users`. This was not previously written down anywhere; capturing it here is itself part of closing this documentation gap.

## 9. Verification performed

- [x] Read the full current source of `driver_import_service.py`, `routes/admin/driver_import.py`, and the existing test files (`test_driver_import_service.py`, `test_driver_import_service_coverage.py`, `test_admin_driver_import.py`) to confirm this document matches shipped, tested behavior — not aspirational or planned behavior.
- [ ] No code was changed, so no test suite was (re-)run as part of this entry — existing coverage for this module already exists and is unaffected.
- [x] Blast-radius grep performed for `legacy_import_metadata` consumers (§4).

## 10. What was NOT verified

- **Not exercised against a real Supabase instance or the real CLI script** — this is a documentation backfill of existing code, not a new test pass. The behavior described is read directly from the current source, not re-verified end-to-end in this session.
- **The rollback procedure in §8 has not actually been run** — it is derived from reading the schema/provenance pattern, not exercised against real data. If a real bad-batch cleanup is ever needed, treat this as a starting point to verify against the live schema (in particular, confirm current FK constraints between `driver_documents` and `drivers` before running the deletes in that order).
- **No visual/UI verification** — this document covers backend behavior only; the admin dashboard's Bulk Import page UI itself was not reviewed here.
