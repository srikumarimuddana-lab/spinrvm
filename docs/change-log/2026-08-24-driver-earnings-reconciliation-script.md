# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code session (see PR for session link) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (this branch) |
| Related issue or gap ID | Found during this session's Mongo-import audit (`docs/audit/2026-08-19-full-mongodb-export-collection-inventory.md`'s `driverearnings` finding: "this collection... has never been cross-checked against the imported payouts rows") |

## 1. Issue / gap identified

`booking_import_service.py`'s `driver_total` for each imported completed
legacy ride is derived from `driverearnings.csv` (summed per booking), with a
documented fallback to the booking's own `you_earn` field for the ~4 rows
that have no earnings row at all. Nobody has ever verified that what's
actually stored in production (`rides.driver_earnings`) agrees with what
`driverearnings.csv` says it should be — the audit flagged this as worth
doing before treating the original import as fully trustworthy.

## 2. Root cause

Not a bug — a missing verification step. The import logic itself is
correct by construction (it derives `driver_earnings` directly from the
ledger), but nothing has ever re-read the ledger independently afterward to
confirm production actually matches what was computed at import time (e.g.
from an operator error re-running an older CSV snapshot, or from a
since-corrected `driverearnings.csv` row).

## 3. Fix / remediation

Built `backend/scripts/reconcile_legacy_driver_earnings.py` — a **read-only**
diagnostic CLI script (never writes anything), following the same pattern as
the existing `backend/scripts/backfill_imported_ride_snapshots.py` /
`backfill_imported_ride_routes.py` one-time scripts. For every already-
imported completed legacy ride, it buckets the comparison into:

- `match` — `driverearnings.csv` rows for that booking sum to (within a
  configurable cent tolerance) `rides.driver_earnings`.
- `fallback` — no ledger rows exist for that booking at all (the known,
  expected `you_earn`-fallback case) — reported separately, never treated as
  a bug.
- `mismatch` — ledger rows exist but disagree with what's stored, beyond
  tolerance. This is the actual finding the script exists to surface; exits
  non-zero if any are found.

The CSV grouping (`_load_earnings_by_booking`) deliberately mirrors
`booking_import_service._earnings_by_booking`'s exact rule (blank
`booking_id` rows — referral bonuses — are excluded) so the script compares
against precisely what the importer itself would compute, not a
re-derivation that could silently drift from the real logic. An unparseable
`amount` field is flagged explicitly (never silently treated as a real $0).

12 new unit tests covering CSV grouping, all three buckets, the
different-import-source and cancelled-ride skip conditions, and both CLI
exit codes.

## 4. Risk & impact on existing functionality

- **What else reads/writes the same data:** nothing — this script only
  reads `rides` (via `db_supabase.get_rows`) and a local CSV file; it never
  calls `insert`/`update`/any RPC. Zero write path exists in this script at
  all.
- **Could this regress a flow that currently works?** No — it is not called
  from any route, background loop, or other script. It has no callers.
- **Blast radius:** isolated — one new script file, one new test file.
- **Interaction with background loops / ride state machine / money:** none.
  Read-only against `rides.driver_earnings`; never mutates it or anything
  else.

## 5. User-experience effect

Nobody — this is an operator-run CLI diagnostic, not reachable from any app
surface.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/scripts/reconcile_legacy_driver_earnings.py` | New file — read-only reconciliation CLI | Close the "never cross-checked" gap flagged in the 2026-08-19 audit |
| `backend/tests/test_reconcile_legacy_driver_earnings.py` | New file — 12 unit tests | Regression coverage |
| `docs/change-log/2026-08-24-driver-earnings-reconciliation-script.md` | This file | Change Impact Log, mandatory for a payments-adjacent change per CLAUDE.md |

## 7. Before / after

Not applicable — new code, no prior behavior (this check never existed).

## 8. Rollback plan

`git revert` is complete and sufficient — the script has no callers and
performs no writes, so there is no data-level effect to roll back regardless
of whether it's ever run.

## 9. Verification performed

- [x] Automated tests run — unit only: `pytest backend/tests/test_reconcile_legacy_driver_earnings.py
  backend/tests/test_booking_import_service.py -q --no-cov` → 58 passed, 0 failed.
- [x] Blast-radius grep performed — confirmed no other file imports or calls
  this script; it has zero callers by design.
- [x] Reviewed against relevant `CLAUDE.md` conventions — Decimal-only money
  arithmetic throughout (never a float comparison for the match/mismatch
  determination); "never silently swallow an error" (an unparseable ledger
  amount is logged and reported, not silently zeroed without a trace).
- [ ] Manual repro steps followed in staging — **not done**; no live Supabase
  access and no real `driverearnings.csv` file available in this session to
  actually run the script against. It has only been exercised via mocked
  `db_supabase.get_rows` and a synthetic CSV in tests.
- [ ] Feature-flagged — not applicable, CLI-only, no route.

## What was NOT verified

- **Never run against the real `driverearnings.csv` or a real database.**
  All coverage is unit-level with synthetic data. The actual reconciliation
  result for production's 186 (or however many now) already-imported legacy
  rides is unknown until someone runs this script for real.
- The script assumes `rides.driver_earnings` is a plain numeric column
  Postgres/PostgREST returns as something `Decimal(str(...))` can parse
  directly (matches every other money field read elsewhere in this
  codebase) — not independently re-verified against the live schema.

## 10. Sign-off

- [x] Rollback plan is concrete — plain `git revert`, no data impact either way.
- [x] Blast radius is stated, not assumed — zero callers, read-only.
- [x] No silent behavior change to an already-shipped flow — new, unreachable, read-only code.
