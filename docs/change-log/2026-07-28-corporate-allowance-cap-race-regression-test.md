# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (P0 audit follow-up) |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | (this branch) |
| Related issue or gap ID | P0 finding from the 2026-07-28 structured SDLC audit of the Corporate billing module — "no locking/race regression test exists for the allowance ceiling fix (migration 258)" |

## 1. Issue / gap identified

Migration 258 fixed a real double-spend race in `corporate_allowance_apply_delta` (the per-member allowance ceiling wasn't enforced inside the row-locked section). The fix shipped with tests (`test_allowance_cap_fallback.py`) that verify `settle_corporate`'s Python-side catch/fallback *given that the RPC already raised* `allowance_cap_exceeded` — but nothing tested whether the locked-check algorithm the RPC implements actually enforces the invariant it claims to under concurrent execution. The exact bug class that shipped once had no regression coverage.

## 2. Root cause

Not applicable in the usual sense — this entry documents adding test coverage, not fixing a bug. The underlying reason the gap existed: the test suite mocks Supabase (per `CLAUDE.md`'s `mock_supabase_client` convention) rather than running against real Postgres, so a true concurrent-load integration test against a live `FOR UPDATE` lock isn't available in this repo's current test tiers.

## 3. Fix / remediation

Added `backend/tests/test_corporate_allowance_cap_race.py`: an in-memory, line-for-line port of the locked section of `corporate_allowance_apply_delta` (migration 258, SQL lines 70-106), serialized under `asyncio.Lock` as a stand-in for the row lock, exercised via `asyncio.gather` for genuine concurrent scheduling. Four tests: (1) two concurrent debits that jointly exceed the cap — exactly one must succeed, one must raise; (2) two concurrent debits that jointly stay under cap — both must succeed (not over-conservative); (3) an unlimited allowance (cap=None) never rejects; (4) a control test reproducing the verified migration-248 shape (locked read, no cap check) to prove the race is real absent the fix — this is the same algorithm class, contrasted directly against the fixed version.

Also closed a related coverage gap in the same file's test suite (`backend/tests/services/test_corporate_allowance_service.py`): added tests for the "RPC returned no row" defensive `RuntimeError`, and the non-positive-amount guards on `apply_ride_debit_reversal`/`apply_rollback` (previously only `apply_grant`/`apply_ride_debit`'s guards were tested). Added a `# pragma: no cover` on the dual-import `except ImportError` line in `corporate_allowance_service.py`, matching the existing convention in `dispatch_service.py`/`fare_service.py`/others, since that branch is only reachable under one of the two import modes per test run.

## 4. Risk & impact on existing functionality

- **Risk of this change itself:** none — this is test-only plus one comment addition (`# pragma: no cover`). No production code path changed.
- **What else reads/writes the same code?** `services/corporate_allowance_service.py` is called only by `services/payment_service.py::settle_corporate` and admin-facing allowance grant/reset/rollback routes (not modified here).
- **Blast radius:** isolated — new test file plus additive test cases in one existing test file, plus one comment in the service module.

## 5. User-experience effect

None — no user-facing or admin-facing behavior changed. Nobody sees a difference.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_corporate_allowance_cap_race.py` (new) | 4 tests modeling the locked ceiling-check algorithm under real `asyncio` concurrency, plus a control test reproducing the pre-fix shape | Close the P0 gap: the exact bug class from migration 258 had no regression test |
| `backend/tests/services/test_corporate_allowance_service.py` | Added 3 tests: no-row RuntimeError, `apply_ride_debit_reversal`/`apply_rollback` non-positive guards | Raise `corporate_allowance_service.py` from 89%→97%, above the 90% money-path floor |
| `backend/services/corporate_allowance_service.py` | Added `# pragma: no cover` to the `except ImportError:` line | Match the established dual-import coverage-exclusion convention used elsewhere in `services/` |

## 7. Before / after

Not applicable — no behavior-changing diff (test-only + a coverage-tooling comment).

## 8. Rollback plan

`git revert` is fully sufficient — no data, schema, or runtime behavior is touched by this change.

## 9. Verification performed

- [x] Automated tests run: unit — `tests/test_corporate_allowance_cap_race.py` (4/4), `tests/services/test_corporate_allowance_service.py` (10/10), full `pytest -k corporate` (348 passed, 3 skipped, 0 failed).
- [x] `services/corporate_allowance_service.py` coverage confirmed at 97% via the same `-k corporate` full-module run CI uses.
- [x] `ruff check` clean on all three modified/new files.
- [ ] Manual repro steps in staging — not applicable (test-only change).

## 10. What was NOT verified

- This remains a **model** of the SQL locking algorithm, not a live-Postgres integration test — it proves the algorithm migration 258 encodes is correct in isolation, not that the deployed RPC (with PostgREST/network layers, connection pooling, and real transaction isolation semantics) behaves identically under real production concurrency. A genuine integration test against a throwaway Supabase/Postgres test schema remains an open item — this repo's test tiers per `CLAUDE.md` include an "integration" tier in principle, but it isn't wired up for this function.
- Did not re-verify whether other RPC call sites (`corporate_wallet_service.py`'s `_apply`/`apply_topup`/`apply_adjustment`/`apply_refund`) have an equivalent non-locking-read-then-RPC-call pattern that could exhibit an analogous race for the master wallet floor rather than the allowance ceiling — flagged as a separate open item in the 2026-07-28 audit, not addressed here.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data dependency).
- [x] Blast radius is stated, not assumed (§4).
- [x] No silent behavior change — none occurred (§5).
