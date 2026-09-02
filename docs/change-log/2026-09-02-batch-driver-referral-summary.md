# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-02 |
| Author | Claude (root-cause N+1 architecture sweep, subtask 3/3) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/n1-query-batching` |
| Related issue or gap ID | Follow-up to the Weekly Payouts latency root-cause audit — third instance of the same anti-pattern |

## 1. Issue / gap identified

`_driver_referral_summary()` — backing `GET /admin/drivers/{driver_id}/referrals`
— loops over up to 200 referred users and, per referee, makes 2 sequential
Supabase calls: a `drivers` lookup by `user_id` and a `count_documents` on
`rides` for that referee's completed-ride count. Up to 400 sequential round
trips to render one admin modal.

## 2. Root cause

Same N+1 shape as subtasks 1 and 2 (`CLAUDE.md`'s Performance SLA
anti-patterns list). Synchronous admin request path (not a background
loop), so a referrer with many referees directly stalls the admin driver
detail modal.

## 3. Fix / remediation

- Batch-resolve referee driver rows in one call:
  `get_rows_batched_in("drivers", "user_id", referred_user_ids, columns="id,user_id")`.
- Batch-fetch completed-ride rows for all resolved referee driver_ids in one
  call: `get_rows_batched_in("rides", "driver_id", ref_driver_ids, {"status": "completed"}, columns="driver_id")`,
  then group-count in memory (`Counter`-style dict) instead of one
  `count_documents` per referee. `columns="driver_id"` keeps each row
  minimal since only the count is needed, not full ride data.
- Both batched calls guard the empty-input case explicitly (no referees →
  skip the call entirely, matching `_compute_payable_balances_batch`'s same
  guard from subtask 1) — proven by a new test asserting
  `get_rows_batched_in` is never called when there are no referees.
- No GROUP BY / count-by-key RPC exists in this codebase for a true
  single-round-trip aggregate count; pulling the raw completed-ride rows
  and counting client-side was judged an acceptable trade for an
  admin-only, bounded (≤200 referees) view rather than building new
  aggregate-RPC infrastructure for one endpoint.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped for all callers of
  `_driver_referral_summary` — only `admin_get_driver_referrals` calls it.
  No other function reads the old per-referee `get_rows("drivers", ...)` /
  `count_documents("rides", ...)` pattern for this purpose.
- The qualification formula (`completed >= rides_required`) and every field
  in the response shape are unchanged — only how `completed` and `ref_drv`
  are looked up changed, from N per-referee round trips to 2 batched ones.
- `referred_by` (inbound referral chain) and the referral-code/earnings
  logic above/below this loop are untouched.

## 5. User-experience effect

Admin-facing only. The driver detail "Referrals" modal now loads in a
small, fixed number of round trips instead of one scaling with the
referrer's referee count. No rider/driver-facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/drivers.py` | `_driver_referral_summary`: batch-resolve referee driver rows and completed-ride counts via `get_rows_batched_in` instead of a per-referee loop of `get_rows`/`count_documents` | Eliminate the N+1 pattern |
| `backend/tests/test_admin_drivers_coverage.py` | Updated `TestDriverReferrals`'s two existing tests to mock `get_rows_batched_in`; added `test_no_referees_short_circuits_without_batched_calls` | Regression coverage for batching correctness and the empty-input guard |

## 7. Before / after

```python
# Before: 2 round trips per referee, up to 200 times
for u in referred_users:
    ref_drv = (await db_supabase.get_rows("drivers", {"user_id": u["id"]}, limit=1))[0] if ... else None
    completed = await db_supabase.count_documents("rides", {"driver_id": ref_drv["id"], "status": "completed"}) if ref_drv else 0
```

```python
# After: 2 batched calls total for ALL referees
ref_drivers = await db_supabase.get_rows_batched_in("drivers", "user_id", referred_user_ids, columns="id,user_id")
completed_rides = await db_supabase.get_rows_batched_in("rides", "driver_id", ref_driver_ids, {"status": "completed"}, columns="driver_id")
# grouped/counted in memory, then looked up per referee via dict.get
```

## 8. Rollback plan

`git-revert-safe`. No migration, no schema change. Reverting restores the
per-referee sequential lookups exactly; response shape is unchanged either
way so no client-side coordination is needed.

## 9. Verification performed

- [x] Automated tests: `TestDriverReferrals` (5 tests: 2 updated + 1 new) —
      all pass.
- [x] Broader sweep: `test_admin_drivers_coverage.py` + `test_referral_terms.py`
      + `test_referrals_coverage.py` — 201 passed, 10 failed. The 10
      failures (`TestLiveStatsLicenseMask`, `TestDriverStats`,
      `TestReferralLeaderboard`, `TestRiderReferralLeaderboard`,
      `TestReferralAnalytics`, `TestPayoutsSummary` ×5) are unrelated to
      `_driver_referral_summary`/`TestDriverReferrals` and were
      **explicitly re-confirmed** pre-existing by `git stash`-ing this
      change out and re-running the same file: identical 10/10 failures,
      same error signatures, on the prior commit.
- [x] `ruff check` clean (4 pre-existing, unrelated B904 findings in a
      different function — `admin_stripe_refresh` — confirmed pre-existing
      the same way, via stash-and-rerun). `ruff format --check` clean after
      reformatting the one line ruff flagged in my own diff.
- [ ] Manual repro / staging check — not performed, no staging environment
      available in this session.
- [x] Blast-radius grep: confirmed `_driver_referral_summary` has exactly
      one caller.
- [x] Reviewed against CLAUDE.md conventions: reused `get_rows_batched_in`;
      no silent error handling changes (batched-call failures propagate the
      same as the original per-referee calls did — no new try/except added
      that would swallow an error differently than before).
- [ ] Feature-flagged — not flagged. Justification: same class of pure
      performance fix as subtasks 1 and 2, unchanged output for an
      admin-only diagnostic endpoint.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change — response shape and qualification math are
      byte-identical, only the network access pattern changed
