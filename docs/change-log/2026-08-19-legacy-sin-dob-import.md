# Change Impact & Risk Log — Legacy driver SIN/DOB backfill from banks.csv

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Session user (business-owner decision), implemented by Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (this branch — `claude/spinr-mongodb-migration-u9y6iz`) |
| Related issue or gap ID | `ACTION_ITEMS.md` A34; `docs/audit/2026-08-19-full-mongodb-export-collection-inventory.md` (banks.csv finding) |

## 1. Issue / gap identified

The raw MongoDB export's `banks.csv` (157 rows) carries every legacy driver's SIN and date of
birth. 187/211 already-imported Spinr drivers have neither field on file, meaning they can't
receive a payout (`_require_sin_for_payout`) or complete Stripe Connect onboarding cleanly
(`_require_sin_for_onboarding`) without re-entering data the old app already collected from them.

## 2. Root cause

The 2026-07-29 production driver import (`legacy_saskatoon_driver_import`, numeric-ID CSV) never
carried SIN, and only conditionally carried DOB. `banks.csv` is a *different* export collection
(Mongo-ObjectId-keyed, from the same `Mongo.zip`) that does carry both — but nothing in this repo
had a crosswalk between the two ID namespaces until this session's audit verified one (phone
match, 100%/99% joinable on the ride-side; verified separately here for the `drivers.csv`↔
`banks.csv` pair specifically).

## 3. Fix / remediation

Added `plan_legacy_sin_dob_import()`/`apply_legacy_sin_dob_import()` to
`backend/services/driver_import_service.py` (the module that already owns the driver-import
plan/commit pattern) plus a CLI wrapper. Given `banks.csv`'s `driver_id` and `drivers.csv`'s `_id`
join 1:1 within the export, and `bookings.driver_id`/`bookings.customer_id` were independently
verified to join at 96/96 and 172/173 against the same drivers/customers collections (see the
2026-08-19 audit doc), the crosswalk resolves phone → an already-imported Spinr driver.

**Deliberately excludes** `account_number`/`transit_number`/`institute_number`: nothing in the
live payout path reads raw banking numbers today — Stripe Connect collects them directly from the
driver, the local `bank_accounts` table already discards the full account number down to last4 on
write, and its only payout consumer (`request_payout`'s standard-cashout branch) is hardcoded
`_STANDARD_CASHOUT_DISABLED = True`. This was surfaced to the user mid-session as a scope-narrowing
decision (see conversation) before any code was written — building storage for data nothing reads
would be new attack surface with no consumer, not what "encrypted at rest" was meant to authorize.

Two more safety properties enforced in code, not just documented:
- **Legacy-driver-only gate**: only writes to a driver whose `legacy_import_metadata->>'source'`
  already equals `legacy_saskatoon_driver_import`. A phone-number coincidence can never reach an
  organic (non-legacy) driver's SIN/DOB.
- **Never-clobber**: if `sin` or `date_of_birth` is already set — self-entered by the driver, or
  from an earlier run of this same script — that value wins. The two fields are evaluated and
  staged independently, so a driver missing only one of the two gets exactly that one backfilled.

This subtask **builds and unit-tests the importer only**. It has not been run against production —
running it needs its own explicit go-ahead (see §9).

## 4. Risk & impact on existing functionality

Blast radius grepped for every reader/writer of `drivers.sin` and `drivers.date_of_birth`:

- **`drivers.sin` writers/readers**: `routes/drivers/profile.py` (driver self-entry — validates,
  encrypts, writes; blocks re-entry once set — "cannot be changed from the app"),
  `routes/drivers/_shared.py` (`_encrypt_driver_pii`/`_decrypt_driver_pii` helpers),
  `routes/drivers/payouts.py` (`_require_sin_for_onboarding`, `_require_sin_for_payout`,
  `prefill_sin_to_stripe` — the one documented live decrypt path), `routes/admin/drivers.py`,
  `routes/admin/compliance.py`, `utils/sin.py` (validation, reused as-is), `utils/t4a_pdf.py`
  (year-end filing), `routes/drivers/tax_exports.py`, `routes/admin/tax_id_import.py`.
- **`drivers.date_of_birth` writers/readers**: `routes/admin/drivers.py` (admin view/edit),
  `services/driver_import_service.py` itself (the existing Saskatoon-CSV import path — unaffected,
  this backfill only ever targets a row *after* that import already ran and left the column NULL).
- **Not touched by this change**: `bank_accounts` table, `routes/drivers/payouts.py`'s
  `save_bank_account`/manual-cashout path (already dead — `_STANDARD_CASHOUT_DISABLED = True`),
  any Stripe API call (this writes to Spinr's own DB only; `prefill_sin_to_stripe` is a *separate*,
  already-existing code path that will pick up a newly-backfilled SIN the next time that driver
  goes through Connect onboarding — no new call site was added here).
- **Regression risk**: none of the listed consumers branches on "was this SIN/DOB self-entered vs.
  backfilled" — they all just check truthiness/decrypt. A backfilled value is indistinguishable
  from a self-entered one to every existing reader, by design (never-clobber means self-entry
  always wins if it happens first either way).
- **Blast radius: isolated.** No background loop, ride state machine, or money/wallet code path is
  touched. `legacy_import_metadata` gains one new namespaced key
  (`legacy_mongo_banks_sin_dob_import`) merged alongside whatever keys a driver's row already had —
  verified not to clobber the existing `source`/`old_driver_id` keys (test:
  `test_apply_encrypts_sin_and_merges_metadata_without_clobbering`).

## 5. User-experience effect

- **Driver-facing, mid-session-relevant**: a legacy driver who previously couldn't request a payout
  or finish Stripe onboarding (missing SIN) will, after this runs, find that gate already satisfied
  — a positive, unblocking change, not a behavior regression. No new UI, no copy change.
- Nothing rider/corporate/internal-admin-facing changes.
- Not yet live — this PR only ships the importer; no driver sees any effect until someone runs it
  with `--apply` against production, which is a separate, explicit step (§9).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/driver_import_service.py` | Added `LEGACY_BANK_SIN_DOB_SOURCE`, `SinDobImportPlan`, `join_legacy_bank_sin_dob`, `plan_legacy_sin_dob_import`, `apply_legacy_sin_dob_import`, `print_sin_dob_report`; added `sin_last4`/`validate_sin` to the module's dual-import block | New backfill capability, reusing the module's existing plan/commit/`encrypt_pii` conventions |
| `backend/scripts/backfill_legacy_driver_sin_dob.py` | New CLI wrapper (dry-run default, `--apply` to write) | Operator entry point, mirrors `backfill_stripe_customer_emails.py`'s house style |
| `backend/tests/test_legacy_sin_dob_import_service.py` | New unit tests (crosswalk, legacy-only gate, never-clobber ×2, invalid SIN, unparseable DOB, duplicate-phone, apply/encrypt/metadata-merge, refuse-on-errors) | Coverage for a PII-adjacent write path |

## 7. Before / after

Purely additive — no existing function's behavior changed. The one non-additive edit was fixing
`date_of_birth_raw` parsing to strip the `T00:00:00.000` time component `banks.csv` actually uses
(the shared `DATE_FORMATS` list only matches plain dates):

```python
# Before (would silently fail to parse every banks.csv DOB — "%Y-%m-%d" doesn't match
# an ISO-datetime string, and this bug was caught by this PR's own test before it shipped)
"date_of_birth_raw": row.get("date_of_birth") or "",

# After
"date_of_birth_raw": (row.get("date_of_birth") or "").split("T", 1)[0],
```

## 8. Rollback plan

No code path in this change is destructive or hard to revert:
- **Code**: `git revert` — the new functions have no other caller yet (the CLI script is the only
  entry point), so reverting removes the capability cleanly.
- **Data, if `--apply` has already been run**: for each driver id the CLI printed, null
  `sin`/`sin_last4`/`sin_collected_at`/`date_of_birth` and remove the
  `legacy_mongo_banks_sin_dob_import` key from `legacy_import_metadata` (leaving its other keys
  intact). No cascading state — no Stripe call, no payout, no WS event is triggered by this write —
  so there is nothing else to unwind. Every touched driver id is printed by the CLI at apply time,
  so the affected set is recoverable without a DB scan.
- This is stated as data-level remediation, not just `git revert`, per CLAUDE.md's rollback rule —
  applicable here specifically *if and when* someone runs `--apply`, which has not happened yet.

## 9. Verification performed

- [x] Automated tests: unit (`backend/tests/test_legacy_sin_dob_import_service.py`, 15 cases) —
  **15/15 pass**. Regression check on the module's existing coverage
  (`test_driver_import_service_coverage.py` + `test_driver_import_service.py` +
  `test_import_saskatoon_drivers.py` + `test_admin_driver_import.py`, 108 cases) — **108/108 still
  pass**, no existing behavior broken by the added import block or the `date_of_birth_raw` fix.
  Run via a fresh venv (`backend/requirements.txt`) since none was present in this session; not a
  production build (`pytest`, not `npm run build` — n/a, this is backend-only). `ruff check` clean
  on all three new/modified files.
- [ ] Manual repro / staging run — **not performed**. No live Supabase access this session; the
  crosswalk join rates (96/96, 172/173) were verified against the raw CSV export directly, not
  against live production data.
- [x] Blast-radius grep performed — see §4 for the full file list (`grep` for `"sin"`/`'sin'`/
  `.sin\b`/`sin_last4`/`sin_collected_at` and `date_of_birth` across `backend/**/*.py`).
- [x] Reviewed against CLAUDE.md conventions: PIPEDA (SIN/DOB never logged — report items carry
  only `old_driver_id`/field-name/generic message, verified by
  `test_plan_skips_unmatched_phone`'s explicit "never leaks the phone value" assertion), money
  arithmetic (n/a, no Decimal involved), state machine (n/a), dual-import pattern (followed).
- [ ] Feature flag — not applicable; this is an offline CLI operator tool, never an in-request code
  path, so there's no live traffic to flag-gate.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (see §8) — not yet exercised, since nothing has been
  applied to production.
- [x] Blast radius is stated, not assumed (§4).
- [x] No silent behavior change to an already-shipped flow — the UX field (§5) states plainly this
  is unblocking-only and not yet live.

**Not yet done, and deliberately out of scope for this change**: running
`backend/scripts/backfill_legacy_driver_sin_dob.py --apply` against production. That needs the
actual `banks.csv`/`drivers.csv` files staged somewhere this backend can reach, live Supabase
write access, and a separate explicit go-ahead — this PR ships the reviewed, tested capability
only.
