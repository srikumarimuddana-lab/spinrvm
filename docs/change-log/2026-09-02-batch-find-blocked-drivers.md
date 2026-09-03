# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-02 |
| Author | Claude (root-cause architecture follow-up to the Weekly Payouts spinner fix) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/n1-query-batching` |
| Related issue or gap ID | Follow-up to PR #4874 — the capped fix removed the hang but left ~20s latency |

## 1. Issue / gap identified

PR #4874 fixed the "Weekly Payouts" admin tab hanging forever by capping
`find_blocked_drivers`'s per-driver balance-check loop at 300 drivers and
parallelizing each driver's 4 sub-queries. That bounded the worst case, but
did not fix the underlying shape: the endpoint still issued **one
round-trip-set per candidate driver**, sequentially, up to 300 times —
measured at ~20 seconds at realistic hosted-PostgREST latency (~65-70ms ×
300).

## 2. Root cause

Classic N+1 query pattern — explicitly named in `CLAUDE.md`'s Performance
SLA anti-patterns list ("N+1 Supabase reads in a loop (batch via `.in_()`
instead)"). `find_blocked_drivers` looped over gate-failing candidate
drivers and called `_compute_payable_balance(driver_id)` once per driver,
each call issuing 4 independent Supabase queries. Capping the loop bounded
the *worst case*; it did not change the *shape* — latency still scaled
linearly with the candidate count.

## 3. Fix / remediation

Replaced the per-driver loop with the batch-loading pattern already
established and used correctly elsewhere in this codebase (see
`routes/admin/rides.py::admin_export_drivers` for the template this follows,
and `repositories/_base.py::get_rows_batched_in` for the helper itself):

1. **Phase 1** (unchanged): page the `drivers` table, apply the cheap
   in-memory `_eligibility_skip_reason` gate check, collect up to
   `_MAX_BALANCE_CHECKS` (300) candidate driver_ids.
2. **Phase 2** (new): compute ALL candidates' balances in one batched call —
   `_compute_payable_balances_batch(driver_ids)` — which issues ~4-5 total
   `get_rows_batched_in` calls (one per source table, `.in_()`-filtered
   across every candidate driver_id at once, auto-chunked at 150 ids/request
   by the existing helper) instead of one round-trip-set per driver.

The money formula itself moved into a new pure function, `_balance_from_rows`,
called by BOTH the single-driver `_compute_payable_balance` (used by the
weekly batch's per-skip notification path, unaffected by this change) and
the new batched function — guaranteeing the two paths can never compute a
different number for the same driver, since there is only one formula.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `find_blocked_drivers`.** `_compute_payable_balance`
  (single-driver) keeps its exact prior behavior and callers
  (`_handle_skipped_driver`, `run_weekly_auto_payout`'s main loop) are
  untouched — this PR does not change the weekly batch's own execution path,
  only the admin preflight endpoint's.
- The money math itself did not change — `_balance_from_rows` is a
  line-for-line extraction of the existing formula, verified by the existing
  `test_parity_with_balance_endpoint` test continuing to pass unchanged, plus
  new tests proving the batched path produces identical results to the
  single-driver path for multiple drivers at once.
- `get_rows_batched_in` is an established, already-tested helper (used in
  `routes/admin/drivers.py`, `services/dispatch_candidates.py`,
  `utils/stale_intent_reconciler.py`) — not new infrastructure.

## 5. User-experience effect

Admin-facing only. The "Weekly Payouts" tab now loads in a small, fixed
number of round trips regardless of fleet size, instead of latency that
scales linearly with the number of gate-failing drivers. No rider/driver
change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/auto_payout.py` | Extracted `_balance_from_rows` (pure formula); added `_group_by` and `_compute_payable_balances_batch`; rewrote `find_blocked_drivers` to collect candidates first, then batch-compute balances once | Eliminate the N+1 pattern at its root instead of just bounding it |
| `backend/tests/test_auto_payout.py` | Added `_mock_batched` fixture helper; new `TestComputePayableBalancesBatch` (4 tests: parity, zero-activity driver, empty-input short-circuit, incentive-claim attribution); updated existing `find_blocked_drivers` tests to also mock `get_rows_batched_in`; rewrote the balance-check-cap test to assert on batched call count instead of per-driver call count | Regression coverage for the batching correctness and for the call-count bound this fix provides |

## 7. Before / after

```python
# Before: one round-trip-set per candidate, up to 300 times
for c in candidates:
    balance = await _compute_payable_balance(c["driver"]["id"])
    ...
```

```python
# After: one batched call for ALL candidates at once
balances = await _compute_payable_balances_batch([c["driver"]["id"] for c in candidates])
for c in candidates:
    balance = balances.get(c["driver"]["id"], Decimal("0"))
    ...
```

Measured (real asyncio execution, 1000-driver fleet, 30ms simulated
per-query latency, same methodology as PR #4874's verification):
**9.34s → 0.09s**, batched call count constant at 4 regardless of fleet size
(was up to 300 individual round-trip-sets).

## 8. Rollback plan

`git-revert-safe`. No migration, no schema change. `_compute_payable_balance`
(the function other code paths depend on) is untouched; only
`find_blocked_drivers`'s internals changed. Reverting restores the
capped-but-sequential PR #4874 behavior exactly.

## 9. Verification performed

- [x] Automated tests: `test_auto_payout.py` (69 tests, including 4 new
      batch-correctness tests), plus `test_payout_toctou.py`,
      `test_drivers_extended.py`, `test_payouts_coverage.py`,
      `test_earnings_coverage.py`, `test_admin_drivers_coverage.py` (436
      total) — all pass except the same 10 pre-existing, environment-specific
      failures in `test_admin_drivers_coverage.py` already confirmed
      identical on baseline `main` earlier this session (unrelated to this
      change).
- [x] Real-execution timing verification (asyncio, simulated realistic
      latency) — 9.34s → 0.09s, call count 300→4, reported above.
- [ ] Manual repro / staging check — not performed, no staging environment
      available in this session.
- [x] Blast-radius grep: confirmed `_compute_payable_balance`'s other two
      callers are untouched.
- [x] Reviewed against CLAUDE.md conventions: reused the established
      `get_rows_batched_in` pattern rather than inventing a new one;
      Decimal-only math preserved (formula extracted verbatim); no silent
      error swallowing (batch failure still logs and falls through to
      `balance = 0` per driver, same fail-safe direction as before).
- [ ] Feature-flagged — not flagged. Justification: same as PR #4874 — a
      pure performance fix to a read-only admin diagnostic, same results for
      any request that previously completed successfully.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change — money math is byte-identical, only the
      network access pattern changed
