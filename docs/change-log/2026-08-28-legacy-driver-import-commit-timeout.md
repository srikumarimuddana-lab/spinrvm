# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Live Phase 1 legacy driver import commit failing in production ("Internal server error") |

## 1. Issue / gap identified

Committing the real Phase 1 legacy driver import (924 rows) through the
live admin dashboard failed with a raw "Internal server error" and no
error detail. Confirmed via a read-only production query that **zero rows
from this batch were written** — the failure happened before or during
`commit_mongo_driver_import_plan()`, not partway through.

## 2. Root cause

`commit_mongo_driver_import_plan()` issued its writes as hundreds of fully
sequential, synchronous HTTP round-trips within a single request: a
per-row `.update()` call in a plain `for` loop for `users_to_update` (114
rows) and `drivers_to_enrich` (~215 rows) — 329 sequential calls — plus up
to ~120 sequential `encrypt_driver_pii` RPC calls (one per driver with a
license number) while building the `drivers_to_insert` payload. At this
real-world scale, the combined wall-clock time was long enough to trip an
upstream proxy/gateway timeout before the route's own `try/except Exception`
(which returns a clean 502 with a useful message) ever got a chance to run —
the client saw a raw, un-JSON'd 500 instead. The code's own pre-existing
comment already flagged the shape of this risk ("at real scale this plan's
`users_to_update`/`drivers_to_enrich` loops are hundreds of sequential
UPDATE calls") but the actual timeout only surfaced running the real batch,
not the mocked-Supabase test fixtures used in this route's/service's tests
(which have no real network latency to accumulate).

## 3. Fix / remediation

Replaced the three sequential per-row loops (`users_to_update`,
`drivers_to_enrich`, and the license-number encryption step for
`drivers_to_insert`) with bounded-concurrency execution via a local
`ThreadPoolExecutor(max_workers=20)`, scoped to this one commit call (not
the shared `repositories/_base.py` DB thread pool, so one big admin batch
can't starve concurrent request traffic). Each row updates/encrypts a
different id with no cross-row dependency, so this changes **only how long
the commit takes, not what gets written or in what final state** — same
per-row `.update(dict).eq("id", id).execute()` call, same fields, same
`updated_at` stamping, just issued concurrently instead of one-at-a-time.
Insert-row order for `drivers_to_insert` is explicitly preserved via
index-tracked futures (`drivers[i] = fut.result()`), not completion order.
The bulk `.insert()` calls for `users_to_insert`/`drivers_to_insert` were
already single batched calls (no change needed there — they were never the
N+1 problem).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped for every caller of
  `commit_mongo_driver_import_plan` — exactly two: the admin route
  (`routes/admin/legacy_driver_import.py`) already exercised this session,
  and the CLI script (`scripts/import_legacy_mongo_drivers.py`), which
  wraps the same function. No other code path touches it.
- **Behavior-preserving, not behavior-changing**: verified with a
  from-scratch stress test (not the existing small-fixture unit tests
  alone) at 1000 rows each for `users_to_update`, `drivers_to_enrich`, and
  `drivers_to_insert` (larger than the real 924-row Phase 1 batch) against
  a fake in-memory Supabase store: zero lost or corrupted updates, insert
  order preserved, per-row license encryption still applied correctly only
  to rows that had a plain license number. See verification section below
  for the exact script.
- Sibling Phase 2 routes (`legacy_sin_dob_backfill.py`,
  `legacy_vehicle_history_backfill.py`) use their own separate commit
  functions in different service modules — **not touched by this fix** and
  may have the identical sequential-loop risk at their own real-world
  scale; flagged as a follow-up, not fixed here to keep this change
  minimal and scoped to the failure actually hit today.
- Concurrency is bounded (20 workers) well under Supabase/PostgREST
  connection limits for a single admin-triggered burst; does not touch the
  shared `_DB_EXECUTOR` pool other concurrent requests rely on.

## 5. User-experience effect

- **Internal admin only.** No rider/driver-facing change — this only
  changes how a legacy-migration admin tool performs its already-existing
  write, not any rider/driver-visible behavior. The practical effect for
  the admin operator: the Commit button on
  `/dashboard/drivers/legacy-import` should now complete within the
  request's timeout window instead of failing with a generic error after
  doing nothing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/driver_import_service.py` | `commit_mongo_driver_import_plan()`'s three sequential per-row loops replaced with bounded-concurrency `ThreadPoolExecutor` execution; new small helpers `_run_concurrently`, `_update_user_row`, `_update_driver_row` | Fix real production commit timeout on the Phase 1 legacy driver import |

## 7. Before / after

```python
# Before
for upd in plan.users_to_update:
    upd = dict(upd)
    user_id = upd.pop("id")
    if not upd:
        continue
    upd["updated_at"] = datetime.now(timezone.utc).isoformat()
    supabase.table("users").update(upd).eq("id", user_id).execute()
```

```python
# After
def _update_user_row(upd: dict) -> None:
    upd = dict(upd)
    user_id = upd.pop("id")
    if not upd:
        return
    upd["updated_at"] = datetime.now(timezone.utc).isoformat()
    supabase.table("users").update(upd).eq("id", user_id).execute()

# ...
_run_concurrently(plan.users_to_update, _update_user_row)
```

(`_run_concurrently` submits every row to a bounded `ThreadPoolExecutor`
and re-raises the first exception hit, preserving the original
fail-the-whole-commit contract.)

## 8. Rollback plan

`git-revert-safe` for the code itself. If a future commit run somehow
still fails partway (e.g. a genuine data error on one row after this fix),
the existing per-row idempotent-by-phone/email matching in
`build_mongo_driver_import_plan` means a re-validate + re-commit naturally
re-resolves against current DB state rather than risking duplicates — same
safety property the sequential version always had, unaffected by this
change. No feature flag: this is an internal-admin-tool reliability fix,
not a user-visible behavior change requiring dark-ship rollout.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_legacy_mongo_driver_import_service.py tests/test_admin_legacy_driver_import.py` — 35 passed, 1 skipped (pre-existing/unrelated).
- [x] `ruff check` / `ruff format --check` on the changed file — clean.
- [x] **Stress test at realistic scale** (1000 rows each, larger than the real 924-row batch) against a from-scratch fake Supabase store, verifying zero lost/corrupted writes and preserved insert order — see script embedded in this session's history; not committed as a permanent test (ad hoc verification), though the existing unit tests do cover correctness at small scale.
- [x] Blast-radius grep performed: exactly 2 real callers of `commit_mongo_driver_import_plan`, both already accounted for.
- [x] Reviewed against CLAUDE.md conventions: additive/behavior-preserving (release gate #2), isolated blast radius stated (gate #1), bounded concurrency (not unbounded fan-out).

## What was NOT verified

- **Not re-run against the real live Supabase production commit yet** —
  this fix has not itself been exercised against production; the next step
  is the operator re-attempting the Phase 1 commit through the admin
  dashboard once this deploys, to confirm the timeout is actually resolved
  end-to-end (not just proven correct against a fake store).
- The sibling Phase 2 SIN/DOB and vehicle-history backfill commit paths
  were not audited or fixed for the same risk in this change — flagged
  above as a known follow-up, not yet confirmed to have (or not have) the
  same problem at their own real-world row counts.
- No live production timing measurement (before/after P95) was taken —
  the original failure was a hard timeout/crash, not a graceful slow
  response, so there was no "before" latency number to compare against;
  success here is judged by "does the commit complete at all," verified
  once the operator retries in production.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`; unaffected by the existing idempotent-matching safety property)
- [x] Blast radius is stated, not assumed (isolated to 2 known callers of one function)
- [x] No silent behavior change to an already-shipped flow — the *only* prior successful outcome of this code path was "fails" at this scale, so there is no working behavior being altered, only made to actually complete
