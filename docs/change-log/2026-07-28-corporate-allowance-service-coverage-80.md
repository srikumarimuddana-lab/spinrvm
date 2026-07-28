# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude Code (session continuation of PR #2729's coverage pass) |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | ACTION_ITEMS.md A1b — corporate billing coverage track |

## 1. Issue / gap identified

`ACTION_ITEMS.md` A1b listed `backend/services/corporate_allowance_service.py` at ~39% test
coverage, below the CLAUDE.md target of ≥80% for `services/corporate_*.py` (money-moving,
same tier as rides/dispatch).

## 2. Root cause

The 39% figure was stale/measured differently than a targeted run. Re-measuring today with
`pytest tests/ -k allowance --cov=services.corporate_allowance_service` (and, isolated,
`pytest tests/services/test_corporate_allowance_service.py`) shows **97% coverage** — the
existing `tests/services/test_corporate_allowance_service.py` (added in PR #2686, before the
39% figure was recorded in PR #2699/#2713) already exercises every public function
(`apply_grant`, `apply_reset`, `apply_ride_debit`, `apply_ride_debit_reversal`,
`apply_rollback`) on both the success path (correct RPC type/amount/floor serialization) and
the failure paths (non-positive-amount `ValueError`s, empty-RPC-response `RuntimeError`).
The only uncovered line (line 16) is the primary branch of the intentional dual-import
try/except (`from ..db_supabase import run_sync` / `from ..supabase_client import supabase`)
— per CLAUDE.md this pattern is not to be simplified away, and it is structurally untestable
in this test harness because tests always exercise the bare-import (`except ImportError`)
branch, never the relative-import (`try`) branch. The sibling `except` line already carries
`# pragma: no cover` for the same reason; line 16 is the mirror image of that same gap, not
a real hole.

Whatever produced the original 39% number was not reproducible against the current test
suite and current file — no regression, no dropped test file, nothing to fix.

## 3. Fix / remediation

No test or application code was written or changed. This is a measurement correction:
- Re-ran coverage in isolation and via `-k allowance` (which pulls in every allowance-adjacent
  test file: `test_corporate_allowance_service.py`, `test_corporate_allowance_requests.py`,
  `test_allowance_rpc_sign_contract.py`, `test_allowance_cap_fallback.py`,
  `test_corporate_allowance_reset.py`, `test_corporate_allowance_cap_race.py`,
  `test_c_allowance_reset_atomic.py`, plus incidental hits) — 74 passed, 1 skipped.
- Confirmed the module's own dedicated test file alone reaches 97% (33 statements, 1 missed).
- Updated `ACTION_ITEMS.md`'s A1b entry to reflect the real, current number instead of the
  stale 39%.
- Added this change-log entry per CLAUDE.md's mandatory Change Impact & Risk Log rule (the
  file lives on a live-tested money surface, so a documentation-only correction still gets
  logged rather than silently editing the backlog doc).

**Bug found but explicitly NOT fixed per task scope (test-only change):** none in this file.
No float arithmetic exists in `corporate_allowance_service.py` — all amounts are passed to
the `corporate_allowance_apply_delta` RPC via `str(amount)` (the same convention documented
in-file as matching `wallet_increment_balance`/`wallet_pay_for_ride` in `db_supabase.py`),
which is the correct Decimal-safe pattern. Nothing to report to the user here.

## 4. Risk & impact on existing functionality

- **Blast radius: none.** No source file was modified — only `ACTION_ITEMS.md` (a backlog
  doc) and this new change-log file were added/edited. `backend/services/corporate_allowance_service.py`
  itself is untouched.
- Grepped every consumer of `corporate_allowance_service` for completeness (none touched):
  - `backend/utils/allowance_reset.py` — background loop calling `apply_reset` (see
    `test_c_allowance_reset_atomic.py` for its CAS/replay-safety contract)
  - `backend/services/payment_service.py` — `settle_corporate`, calls `apply_ride_debit` /
    `apply_ride_debit_reversal` at fare settlement
  - `backend/routes/corporate_company.py`, `backend/routes/corporate_rider.py` — admin/rider
    allowance grant and rollback endpoints
  - Test files that already cover call sites: `test_coverage_rides.py`,
    `test_corporate_ride_payment.py`, `test_corporate_settle_suspended_audit_flag.py`,
    `test_allowance_cap_fallback.py`, `test_allowance_rpc_sign_contract.py`
  - Migrations `29_corporate_allowance_rpc.sql`, `203_corporate_rpc_security_definer.sql`,
    `205_wallet_rpc_execute_lockdown.sql` define the RPC this service wraps — not touched.
- No interaction with any of the 16 background loops beyond the pre-existing, already-tested
  `allowance_reset_loop` call path (unchanged).

## 5. User-experience effect

None. No rider/driver/corporate-admin/internal-admin facing behavior changed — this is a
documentation/backlog correction only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `ACTION_ITEMS.md` | Updated A1b's `corporate_allowance_service.py` coverage figure from 39% to 97% (measured) with a short note | Correct a stale backlog number now that real coverage was re-measured |
| `docs/change-log/2026-07-28-corporate-allowance-service-coverage-80.md` | New file (this log) | Mandatory per CLAUDE.md for anything touching a live-tested surface (corporate/money), even a doc-only correction |

## 7. Before / after

Not applicable — no behavior-changing code diff. `ACTION_ITEMS.md` text change:

```
# Before
- `services/corporate_wallet_service.py` — 41%, `services/corporate_allowance_service.py` — 39% (money math)

# After
- `services/corporate_wallet_service.py` — 41%, `services/corporate_allowance_service.py` — **97%** (money math;
  closed 2026-07-28 — the existing `tests/services/test_corporate_allowance_service.py`
  already covered every branch once measured in isolation (`pytest tests/ -k allowance`);
  the 39% figure above was stale/measured differently, no new tests were needed)
```

## 8. Rollback plan

`git revert` is sufficient and complete here — this changes only backlog documentation, no
live data, no code path, no schema, no wallet delta. There is nothing at runtime to roll
back.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/services/test_corporate_allowance_service.py -q`
  (uses `pytest.ini`'s default `--cov=.` addopts) → `10 passed`, module coverage
  `services/corporate_allowance_service.py  33  1  97%  16`.
- [x] Automated tests run (broader): `pytest tests/ -k allowance -q` → `74 passed, 1 skipped`,
  same `97%` figure for the module, confirming the number holds across the full allowance
  test surface, not just the dedicated file.
- [ ] Manual repro steps in staging — not applicable, no runtime behavior changed.
- [x] Blast-radius grep performed: `grep -rl corporate_allowance_service backend/` (see §4
  for the full list of hits and why none needed changes).
- [x] Reviewed against CLAUDE.md money-arithmetic convention — confirmed the file already
  uses the `str(amount)`-to-RPC pattern correctly; no float arithmetic found.
- [ ] Feature-flagged — not applicable, no behavior change.

**Real production build:** not applicable — no `admin-dashboard`/`rider-app`/`driver-app`
files touched.

## 10. What was NOT verified

- Did **not** run the full backend test suite end-to-end in this pass (only the allowance
  subset via `-k allowance`, plus the single dedicated test file) — full-suite run was
  observed to intermittently fail at collection time in this environment with an unrelated
  `KeyError: 'pydantic.root_model'` import race inside `pyiceberg`/`storage3` (triggered via
  `supabase`'s import chain), reproducing non-deterministically across otherwise-identical
  invocations of the same command. This is a pre-existing environment flake unrelated to
  `corporate_allowance_service.py` or this change — not something this test-only PR
  introduced or is scoped to fix — but it means the exact aggregate `services/corporate_*.py`
  percentage cited elsewhere in `ACTION_ITEMS.md` (currently "~52% aggregate") was not
  re-verified in this pass, only this one file's number.
- Did not verify against a real/live Supabase instance — coverage is against
  `mock_supabase_client`/`unittest.mock.patch`-based unit tests only, per this repo's testing
  conventions (no integration-tier test exists for this module).
- No visual/snapshot regression tooling applies here (backend-only, doc-only change) — not a
  gap worth flagging since no UI surface is touched.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no live-data implications)
- [x] Blast radius is stated, not assumed (isolated — no source file changed; consumer grep
      listed explicitly)
- [x] No silent behavior change to an already-shipped flow — there is no behavior change at
      all, stated explicitly above
