# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code session (see PR for session link) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (this branch) |
| Related issue or gap ID | Found during this session's Mongo-import audit (`docs/audit/2026-08-19-full-mongodb-export-collection-inventory.md`'s `wallets` finding, ~$900 rider + $60 driver prepaid balance, previously "no import path exists") |

## 1. Issue / gap identified

The legacy `wallets.csv` collection (13 rows in the reference export) carries real,
owed prepaid money — ~$900 of rider wallet credit and ~$60 of driver
referral-wallet credit — with **no importer built** to bring it into Spinr's
`wallets`/`wallet_transactions` tables. The 2026-08-19 audit explicitly flagged
this needs the row-locked `wallet_apply_delta`-style RPC pattern, not a plain
insert, and the RPC itself (migration 249) already exists — nothing consumed it
for this data.

## 2. Root cause

No prior session built this importer; the RPC infrastructure (`wallet_apply_delta`,
migration 249) was built independently for four *live* call sites
(admin credit/debit, cancellation fee, no-show fee) and never wired up to a
legacy-import path.

## 3. Fix / remediation

Built `backend/services/wallet_import_service.py` — a new service module (this
is genuinely new, not an update to an existing importer, unlike the
insurance-period/airport-fee fixes from the same audit) mirroring the
validate-then-commit contract every other legacy importer in this repo
(`booking_import_service.py`, `driver_import_service.py`,
`rider_import_service.py`) already uses:

- `build_plan()` matches each legacy wallet row to an existing Spinr rider or
  driver account by phone (never fabricates an owner — an unmatched row is
  skipped and reported, re-runnable once the account exists). Driver-owned
  rows resolve to `drivers.user_id` (the `users.id` `wallets.user_id`
  actually references), not `drivers.id` — verified against `routes/wallet.py`'s
  own "riders and drivers each have a single wallet" design.
- Legacy `type` (`from_bank` → `top_up`, `from_driver_refer` /
  `for_owner_refer` → `referral_reward`) and `status` (`add`/`deduct` → sign)
  are mapped through an explicit allowlist — an unrecognized value is a hard
  error, never guessed at or defaulted.
- `commit_plan()` applies every planned delta through the existing, already-
  reviewed `wallet_apply_delta` RPC (row-locked, floor-enforced, no plain
  balance UPDATE), get-or-creating the target wallet row first. Each row is
  independent — a single row's RPC failure (e.g. a deduct exceeding the
  wallet's floor) is logged loudly and recorded in the per-row result, but
  does not abort the rest of the batch.
- No separate "already imported" tracking needed: `reference_id` is
  deterministic (`legacy-wallet-<old_id>`), and `wallet_apply_delta`'s own
  `(wallet_id, reference_id, type)` dedup (inside its row lock) makes
  re-running `commit_plan()` against the same plan — or after a partial
  failure — safe by construction.
- 20 new unit tests covering column validation, rider/driver matching, every
  row-level validation branch, metadata/provenance, and `commit_plan`'s
  applied/deduped/failed result handling.

**Explicitly NOT done in this change:** no admin HTTP route was built to
invoke this importer (unlike `routes/admin/booking_import.py` etc.). See
"What was NOT verified" below for why.

## 4. Risk & impact on existing functionality

- **What else reads/writes the same table/RPC:** `wallet_apply_delta` is
  already called by `routes/admin/wallet.py` (admin credit/debit),
  `routes/drivers/ride_cancel.py` (no-show fee), and `routes/rides/cancellation.py`
  (cancellation fee) — this importer is a fifth caller, using the exact same
  function with no modification to it. `wallets`/`wallet_transactions` are
  also read by `routes/wallet.py` (balance/history endpoints) and
  `utils/referral_payout.py` (existing live referral payouts) — this importer
  writes rows in the same shape (`type` values drawn from the existing
  `wallet_transactions_type_check` allowlist, no new type added, no schema
  change), so nothing downstream needs to change to read them correctly.
- **Could this regress a flow that currently works?** No live code path calls
  this new module — it has zero blast radius on any currently-working flow
  until something invokes `build_plan()`/`commit_plan()`, which nothing does
  yet (no admin route, no CLI entrypoint wired up).
- **Blast radius:** isolated — one new file, plus its own test file. No
  existing file was modified.
- **Interaction with background loops / ride state machine / money:** none
  directly (no background loop reads this module); money-wise, every delta
  goes through the same atomic, floor-enforced RPC every other wallet
  mutation in this codebase uses — no new money-mutation code path invented.

## 5. User-experience effect

Nobody, currently — this module isn't reachable from any route or scheduled
job. If/when it's wired to an admin route in a follow-up, the only visible
effect would be to an internal admin (running the import) and, once
committed, to the specific riders/drivers whose wallet balance changes (a
real balance increase they're actually owed, per the audit).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/wallet_import_service.py` | New file — `build_plan`/`commit_plan`/`print_report` for legacy wallet-balance import | Close the `wallets.csv` import gap identified in the 2026-08-19 Mongo-export audit |
| `backend/tests/test_wallet_import_service.py` | New file — 20 unit tests | Regression coverage for the new module |
| `docs/change-log/2026-08-24-wallet-import-service-built.md` | This file | Change Impact Log, mandatory for a payments-touching change per CLAUDE.md |

## 7. Before / after

Not applicable in the usual sense — this is new code with no prior behavior to
diff against. The relevant "before" is: no code path existed to import
`wallets.csv` at all.

## 8. Rollback plan

`git revert` is complete and sufficient. The module is not called from
anywhere, so a revert removes it with zero live-data impact. If this module
is later wired to an admin route and actually run against production, the
rollback for *that* data would follow the same pattern `wallet_apply_delta`
already supports for any wallet mutation: each written `wallet_transactions`
row carries `metadata.source = "legacy_mongo_wallet_import"` and
`metadata.old_wallet_entry_id`, so an incorrect batch could be identified and
reversed with an equal-and-opposite `wallet_apply_delta` call per row (never
a raw balance UPDATE) — not built here since no commit against a real
database has happened yet.

## 9. Verification performed

- [x] Automated tests run — unit only: `pytest backend/tests/test_wallet_import_service.py
  backend/tests/test_wallet_repo.py backend/tests/test_wallet_apply_delta_contract.py
  backend/tests/test_wallet.py -q --no-cov` → 111 passed, 0 failed.
- [x] Blast-radius grep performed — every existing caller of `wallet_apply_delta`
  enumerated (4 live call sites, all unmodified); confirmed no new
  `wallet_transactions.type` value was introduced (reused `top_up` /
  `referral_reward`, both already in the migration-199 allowlist).
- [x] Reviewed against relevant `CLAUDE.md` conventions — Decimal-only money
  math (`to_decimal`/`Decimal` throughout, `str()` at the RPC-call boundary
  only), money-mutating functions called only from the backend
  (`wallet_apply_delta` is `SECURITY DEFINER`, `EXECUTE` already locked to
  `service_role` — unchanged), "never silently swallow a money-path error"
  (every RPC failure is `logger.error(..., exc_info=True)` and recorded in
  the return value, never dropped).
- [ ] Manual repro steps followed in staging — **not done**, no live
  Supabase access, no admin route exists yet to trigger a real run.
- [ ] Feature-flagged — not applicable yet (no route exists to gate).

## What was NOT verified

- **Column names are inferred, not confirmed.** No prior audit session
  captured the real `wallets.csv` header row — the columns this module
  expects (`_id`, `customer_id`, `driver_id`, `amount`, `type`, `status`)
  are inferred from this same export's sibling collections' consistent
  naming convention, documented explicitly at the top of the module. The
  module fails loudly (as a plan error, refusing commit) if any expected
  column is missing, so a real run's first dry-run will immediately surface
  a mismatch rather than silently misimport — but this has not been tested
  against the real file.
- **No admin route was built.** Deliberately out of scope for this pass:
  building an HTTP surface (file upload, auth, request/response schema)
  around a data-shape that hasn't been validated against the real CSV felt
  like compounding one unverified assumption with another. The next step,
  once the real `wallets.csv` is available, is to validate the column
  mapping against it (or fix it if wrong) before wiring an admin route —
  same shape as `routes/admin/booking_import.py`.
- **No real Supabase run.** `wallet_apply_delta`'s actual behavior against a
  live database (RLS, the `period_3_requires_ride`-style constraints that
  don't apply here, real floor/lock behavior under this importer's specific
  call pattern) is only exercised by this session's unit-test mocks — the
  RPC itself has separate real-database-adjacent test coverage
  (`test_wallet_apply_delta_contract.py`), but this importer's specific
  usage of it has not been run against a real database.

## 10. Sign-off

- [x] Rollback plan is concrete — `git revert`, zero live-data impact (module
  unreachable from any route).
- [x] Blast radius is stated, not assumed — isolated, new file, zero callers.
- [x] No silent behavior change to an already-shipped flow — this is
  entirely new, unreachable code; nothing currently working is touched.
