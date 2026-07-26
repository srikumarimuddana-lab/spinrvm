# Stripe driver import — non-blocking existing mappings + per-driver update

**Date:** 2026-07-26
**Status:** Approved (design)
**Scope:** Legacy Stripe *mapping* import, `kind=drivers` only. Riders unchanged.

## Problem

The legacy Stripe mapping importer (`backend/services/stripe_mapping_import_service.py`,
`backend/routes/admin/stripe_import.py`) treats a driver who already carries a
**different** `stripe_account_id` than the CSV row as a hard error
(`conflict_existing`). Because the commit gate is `can_commit = len(plan.errors) == 0`,
a **single** already-mapped driver blocks the **entire** batch — none of the
NULL-ID drivers get imported.

Operators want:
1. Bulk import to write only drivers with **no** Stripe account, and treat
   already-mapped drivers as non-blocking (skip them from the auto-insert).
2. A deliberate, per-driver path to **update** an already-mapped driver's Stripe
   account when they genuinely want to redirect it — behind a confirm step,
   because writing `drivers.stripe_account_id` redirects that driver's payouts.

Also observed: a warning `account type is <custom>, not express`
(`stripe_mapping_import_service.py:434`). **Decision: keep importing Custom
accounts with the warning** (payouts still work; only the in-app Stripe
management AccountLink is Express-only). No change to this rule.

## Behavior matrix (driver rows)

| Driver's current `stripe_account_id` | Before | After |
|---|---|---|
| NULL / empty | imported by commit | imported by commit (unchanged) |
| equals CSV account | warning `already_mapped`, skipped | unchanged |
| **differs from CSV account** | **error `conflict_existing` → blocks whole batch** | **`needs_update` item, non-blocking → rest of batch still commits** |

## Design

### 1. Service — `stripe_mapping_import_service.py`
- Add a `needs_update: list[dict]` field to `StripeMappingPlan`.
- In `_build_local_driver_plan`, the "different existing id" branch appends to
  `plan.needs_update` instead of `plan.errors`. Each item:
  `{ row_ref, driver_id, current_stripe_account_id, new_stripe_account_id }`.
  These rows are **not** added to `driver_updates`, so the commit never touches
  them (the NULL-only commit guard already enforces this too — belt and braces).
- Add `update_driver_stripe_account(driver_id, new_acct, expected_current_acct, batch) -> dict`:
  1. Live-validate `new_acct` via `_account_findings` + `_retrieve_stripe`
     (Custom → warning, non-CA / missing transfers → hard error, refuse on error).
  2. **Optimistic concurrency:** update filters on both `id = driver_id` **and**
     `stripe_account_id = expected_current_acct`. Zero rows → 409 (driver moved
     since the review screen loaded).
  3. Reject if `new_acct` is already held by another driver (`id_taken`).
  4. Write `stripe_account_id = new_acct`; append prior id to
     `legacy_import_metadata` provenance (`stripe_migration.previous_account_id`).

### 2. Route — `routes/admin/stripe_import.py`
- `_report(...)` gains a `needs_update` array (serialized `{row_ref, driver_id,
  current_stripe_account_id, new_stripe_account_id}`) and a
  `counts.needs_update` tally. Report stays **PII-free** — no driver name.
  Appears in both `validate` and `commit` responses.
- New endpoint `POST /api/admin/stripe/import/update-driver` (super_admin only,
  same gate as the rest of the router). Body:
  `{ driver_id, new_stripe_account_id, current_stripe_account_id, batch }`.
  Calls the service update fn; on the optimistic-concurrency miss returns 409;
  audits `stripe_account_update` (driver_id + old/new account + batch, no PII);
  kicks the same `sync_kyc_after_commit([driver_id], batch)` background task so
  the driver's KYC state converges.

### 3. Admin UI — `bulk-operations/page.tsx` + `lib/api.ts`
- Report type gains `needs_update` + `counts.needs_update`.
- `lib/api.ts`: `updateDriverStripeAccount({driver_id, new_stripe_account_id,
  current_stripe_account_id, batch})`.
- New results section "**Already mapped — review to update (N)**". Each row:
  driver **name** (resolved by `driver_id` via the existing drivers admin API —
  keeps the report PII-free) + `current → new` account + an **Update** button.
- Update button → confirm dialog naming the payout redirect
  ("Redirect <driver>'s payouts from acct_… to acct_…?") → calls the endpoint →
  on success removes the row; on 409 shows "driver changed, re-validate".

### PII / security notes
- Import report never carries name/phone/email — only `row_ref`, `driver_id`,
  and `acct_…` ids (ids are non-PII, already logged elsewhere).
- Update is super_admin-only and audited, matching the existing
  payout-destination-write threat model in the route docstring.

## Out of scope
- Rider (`kind=riders`) mapping behavior — unchanged.
- Bulk "update all" — explicitly declined; per-driver confirm only.
- Any DB migration — only existing columns are written.

## Testing
- `test_stripe_mapping_import_service.py`: different-existing → `needs_update`,
  not `errors`; `can_commit` stays true with NULL rows still importing;
  `update_driver_stripe_account` overwrites + records provenance; concurrency
  guard returns no rows when `expected_current_acct` is stale; `id_taken` guard.
- `test_admin_stripe_import.py`: report contains `needs_update`; update endpoint
  happy path, 409 on stale current, 403 for non-super_admin, audit written.
