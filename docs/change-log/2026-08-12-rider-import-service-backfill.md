# Change Impact & Risk Log — Rider bulk-import service (retroactive backfill)

> **This is a documentation backfill, not a new change.** `rider_import_service.py`
> and its admin entry point have been live for some time. ACTION_ITEMS.md
> **A28 (P2-B)** flagged that — unlike `booking_import_service.py` and
> `stripe_mapping_import_service.py`, which each have a runbook/change-log —
> this importer, which writes directly to `auth`/`users`, had none. This
> document describes the **existing, already-shipped** behavior as of
> 2026-08-12; it does not itself change any code. (Two narrower fix-specific
> entries already exist for this module —
> `docs/change-log/2026-08-11-rider-import-pii-protection.md` (P0-C) and
> `docs/change-log/2026-08-11-rider-import-provenance.md` (P0-A) — this
> document is the holistic overview those two assumed as context but never
> stated on their own.)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 (backfill; original code predates this date) |
| Author | Claude Code (agent-assisted) |
| Surface(s) | backend (+ read-only effect on admin-dashboard) |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/a28-p2b-import-service-docs` |
| Related issue or gap ID | ACTION_ITEMS.md A28, sub-finding P2-B |

## 1. Issue / gap identified

`rider_import_service.py` is a shared core (`build_plan` / `commit_plan`) that writes/updates live `users` rows, and it had no Change Impact & Risk Log entry describing its overall behavior — only two narrow entries exist for specific fixes made to it (P0-A provenance stamping, P0-C PII-protection guard), each of which assumes the reader already understands the base importer. For a path that creates rider accounts and can update PII on existing ones, that's a real documentation gap on a live-tested surface.

## 2. Root cause

Not a defect — a documentation gap, same class as the driver-import backfill (see the sibling entry, `2026-08-12-driver-import-service-backfill.md`). This importer predates the Change Impact Log convention becoming mandatory, and was never backfilled with a holistic entry — only its two most recent point-fixes were.

## 3. Fix / remediation

This document. No code changed.

### What the service does

`backend/services/rider_import_service.py` holds the pure parsing/validation (`build_plan`) and write (`commit_plan`) core for one entry point:

- **Admin dashboard** — `POST /api/admin/riders/import/{validate,commit}` (`routes/admin/rider_import.py`). CSV columns: `customer_id` (Stripe `cus_…`), `email`, `gender`, `phone`, `ratings`, `temp_email`, `timeZone` (plus name variants), with `phone` the only required column. Requires `get_admin_user` (admin auth). Caps: 1 MB CSV, 500 rows per import. There is no separate CLI script for this importer — the admin HTTP flow is the only entry point.

Same two-phase contract as `driver_import_service.py`: `build_plan` never writes; `commit_plan` refuses outright if `plan.errors` is non-empty. Unlike the driver importer, the admin rider-import endpoints do **not** use a validate-then-signed-token-bound-commit pattern — `/commit` independently re-parses and re-validates the same uploaded CSV rather than requiring proof a prior `/validate` call happened. This is a real difference from the driver-import path worth noting explicitly (not a defect being reported here, just documenting the asymmetry so it isn't rediscovered as a surprise later).

**Validation performed** (all in `build_plan`, before any write):
- `phone` required; normalized to E.164 and format-validated (`^\+1\d{10}$`); duplicate phone within the same CSV is rejected.
- `customer_id`, if present, is soft-checked to look like a Stripe id (`cus_…` prefix) — a mismatch is a **warning**, not a rejection (the CSV may contain non-Stripe legacy ids that still need importing).
- No format validation on `email`/`gender`/`ratings`/`timezone` beyond trimming — these are optional, lower-stakes fields compared to `phone`.

**Matching / duplicate handling:** an existing user is matched by phone first, then by email. Three distinct outcomes:
1. **No match** → new user row created (`role="rider"`, `is_rider=True`, `is_driver=False`), tagged with `legacy_import_metadata.rider_csv_import` (batch, source, timestamp).
2. **Match found, on a normal account** → `plan.users_to_update` gets a partial update: `stripe_customer_id` only if it differs, `email` only if the existing user has none (never overwrites an existing email), `gender` only if it's one of the accepted values, and `is_rider` is set true if not already. **A field is never blanked** — every write is additive/fill-in-if-missing, never a destructive overwrite of already-set data.
3. **Match found, but the account's `status` is `pending_deletion` or `deleted`** (`_PII_PROTECTED_STATUSES`) → the row is **skipped entirely, no fields written**, flagged as `protected_skip` for manual review. This is the P0-C guard (see the dedicated `2026-08-11-rider-import-pii-protection.md` entry): a `pending_deletion` account has had `email`/`first_name`/`last_name` NULLed by the 30-day PIPEDA scrub (migration 296), and without this guard the importer's ordinary "repopulate if falsy" update logic would silently re-populate that PII — functionally equivalent to reactivating a deleted account, but with none of the auditing/OTP-gating the real `POST /auth/reactivate` reactivation flow has.

A match against an existing **driver** account (phone reused) is separately flagged (`match_type: "driver"`) — the row still proceeds as an update (setting `is_rider=True` alongside the existing driver flags, i.e. the account becomes both a rider and a driver), not rejected, since a person legitimately holding both roles under one phone number is a supported state elsewhere in the system.

**Provenance:** every row this importer actually creates or modifies is stamped with `legacy_import_metadata.rider_csv_import` — merged onto whatever metadata already exists on that row (not overwritten wholesale), specifically because `users.legacy_import_metadata` is shared with `stripe_mapping_import_service.py`, which writes its own `stripe_migration` sub-key onto the same column; a rider touched by both importers must retain both provenance records (this is the P0-A fix, see the dedicated `2026-08-11-rider-import-provenance.md` entry).

**What gets written** (`commit_plan`): new users are batch-inserted (200 rows/batch); updates are applied one row at a time via `.update(...).eq("id", ...)`, each stamped with `updated_at`.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface** (writes/updates the core `users` table, read throughout the backend and admin-dashboard).

| Consumer | Effect of an imported/updated rider row | Handling |
|---|---|---|
| Signup / login (`routes/auth.py`) | An imported rider can log in via the same phone-based OTP flow as any account | `phone` is format-validated at import to match the live OTP flow's expected shape |
| `stripe_mapping_import_service.py` | Shares `users.legacy_import_metadata` column | Merge-not-overwrite handled explicitly (see P0-A) |
| Account-deletion / PIPEDA scrub (migration 296) | Nulls PII on `pending_deletion`/`deleted` accounts | This importer never repopulates those fields on a protected-status match (P0-C) |
| Admin rider list/detail | Reads `legacy_import_metadata` where present | Not modified by this backfill |
| Corporate `join-domain` (`routes/corporate_rider.py`) | Gates on `email_verified`, unrelated to this importer | Not written or affected by this importer — importer sets `email` but never `email_verified` |
| Driver-role accounts sharing a phone | `existing_driver` lookup exists specifically to detect and label this case | Handled additively (§3) rather than rejected |

No money, wallet, ride, or corporate-billing table is touched by this importer — scoped to identity data only.

## 5. User-experience effect

- **Riders**: an imported/updated rider experiences no visible difference — same login flow. A rider whose account already existed and gets a field filled in (e.g. `stripe_customer_id`) sees no UI change; the update is backend-only bookkeeping.
- **Internal admin**: sees imported/updated riders in the normal user list/detail views, with the dry-run report additionally surfacing duplicate/protected-skip counts before commit.
- **Drivers / corporate admin**: no effect, except in the specific phone-reuse-with-existing-driver case (§3), where the driver's own experience is unaffected — only their account gains `is_rider=True` internally.
- No copy or notification changes — this document adds no new behavior.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/change-log/2026-08-12-rider-import-service-backfill.md` | New (this file) | Backfills the missing holistic runbook per ACTION_ITEMS.md A28 P2-B |

No source files were modified as part of this entry.

## 7. Before / after

Not applicable — documentation-only, no behavior-changing diff.

## 8. Rollback plan

Not applicable to this document itself (pure docs, `git-revert-safe`, trivially). For the importer's own operational rollback, every row this service creates or modifies is tagged (new rows fully, updated rows via the merged `legacy_import_metadata.rider_csv_import` sub-key) with `batch`, so a bad batch's created users can be identified and removed by that batch ID. **Updated (not created) rows are not cleanly revertible by this metadata alone** — the importer's update logic is fill-in-if-missing, so the previous field values are not captured anywhere before being overwritten; a real rollback of a bad update batch would need a pre-commit snapshot taken before running it, which the importer does not currently produce. This asymmetry (created rows are cleanly revertible by batch tag; updated rows are not) was not previously written down; capturing it here is part of closing this documentation gap, and is worth flagging to whoever runs a future rider-import batch as an operational caveat rather than something this document itself needs to fix.

## 9. Verification performed

- [x] Read the full current source of `rider_import_service.py`, `routes/admin/rider_import.py`, and the existing test files (`test_admin_rider_import.py`) plus the two narrower prior change-log entries for this module, to confirm this document matches shipped, tested behavior — not aspirational or planned behavior.
- [ ] No code was changed, so no test suite was (re-)run as part of this entry — existing coverage for this module already exists and is unaffected.
- [x] Blast-radius grep performed for `legacy_import_metadata` consumers (§4).

## 10. What was NOT verified

- **Not exercised against a real Supabase instance** — this is a documentation backfill of existing code, not a new test pass. The behavior described is read directly from the current source, not re-verified end-to-end in this session.
- **The "updated rows are not cleanly revertible" caveat in §8 is a reading of the code, not something tested by attempting a real rollback.** If this ever needs to be exercised for real, treat it as something to verify against the live schema/data first, not as a proven procedure.
- **No visual/UI verification** — this document covers backend behavior only; the admin dashboard's Bulk Rider Import section UI itself was not reviewed here.
