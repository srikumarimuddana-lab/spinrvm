# Change Impact & Risk Log — dispatch DB-call metric accuracy + RLS CI restoration

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-02 |
| Author | Codex-style review follow-up on PR #4873 (C50 Phase 0/1) |
| Surface(s) | backend, CI |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/pr-4873-codex-review-r0ag6j` (commits `59ba299`, `1b67917`), on top of PR #4873 head `f4df2af` |
| Related issue or gap ID | ACTION_ITEMS.md C50 (T3, T11); review findings 1 and 2 |

## 1. Issue / gap identified

Two defects introduced by PR #4873, both silent — each ships green and misleads rather than failing:

1. **`spinr_dispatch_attempt_db_calls` under-counts every dispatch attempt.** The per-attempt DB-call counter missed every `run_sync` call issued from an `asyncio.gather()` child.
2. **`--ignore=tests/rls` removed the repo's only DB-role-level RLS coverage from CI**, justified by a comment asserting a dedicated step existed for it. No such step existed in any workflow file.

## 2. Root cause

**(1)** `_db_call_count` was a plain `ContextVar[int]`. asyncio runs each `gather()`/`create_task()` child in a **copy** of the current context, so a child's `_var.set(_var.get() + 1)` writes to that copy and is discarded when the child completes. The ContextVar gave the isolation it was chosen for, but not additivity — two different properties, and only the first was reasoned about in the original comment.

`_match_driver_to_ride_attempt` fans its enrichment reads out through `asyncio.gather()` (`matching.py:961`), so at minimum `_fetch_rider` → `get_user_by_id` and `_fetch_incentives` → `match_ride_incentives` (`incentive_service.py:276`, `await db.run_sync(...)`) were dropped from every observation.

Verified empirically before fixing — a parent issuing 1 call and gathering two children of 2 calls each observed **1 of 5**.

**(2)** The `--ignore` was added on the correct premise (the mocked-DB step's placeholder DSN is truthy, which defeats both suites' `_DSN`-truthiness self-skip) but paired with an unverified claim that `tests/rls` had its own real-Postgres step. It did not: `grep -rn "tests/rls" .github/workflows/` matched only the new `--ignore` itself. The suite had been running inside the mocked step (`pytest.ini`'s `testpaths = tests` collects it; its conftest-level `pytestmark` does not propagate to sibling modules — the same latent bug T11 documented for `tests/direct_pool`; and that step's connection string previously held real service credentials).

## 3. Fix / remediation

**(1)** The counter now holds a one-element list instead of an int. A context copy binds the *same* list object, so a child's `[0] += 1` is visible to the parent, while `reset_db_call_count()` rebinds a fresh list so separate attempts stay isolated. The default is `None`, not a module-level `[0]` — a shared mutable default would be the single object every un-reset context sees and would accumulate across unrelated call sites forever. With `None`, increments outside a counting window are not counted, which is the correct semantics.

**(2)** Added the missing dedicated `tests/rls` CI step, mirroring the `tests/direct_pool` one and using the isolated `-c /dev/null --confcutdir=tests/rls` invocation `CLAUDE.md` documents. The `--ignore` is kept — correct on its own terms. Both misleading comments corrected, plus a note that an `--ignore` without a matching step silently drops a suite.

## 4. Risk & impact on existing functionality

**Blast radius: isolated (backend observability only) + CI configuration.** No ride state, money, auth, or user-facing path is touched.

Grep performed — `reset_db_call_count|get_db_call_count|_incr_db_call_count|_db_call_count` across `backend/`:

- **Exactly one production consumer**: `routes/rides/matching.py:199` (reset) and `:1145` (observe). No other module reads the counter.
- `_incr_db_call_count()` is called from `run_sync` (`_base.py:414`), i.e. on **every DB call process-wide**. Its behavior outside a counting window changes from "increment a context-local int" to "no-op after an `is None` check" — marginally *less* work per DB call, and nothing read that value before.
- Metric consumers of `spinr_dispatch_attempt_db_calls`: only `docs/audit/2026-09-02-pgbouncer-direct-pool-migration-plan.md` names it, as a planned input. **No dashboard or alert queries it yet**, so the corrected (higher) values cannot break an existing threshold.

Ride state machine, insurance-period writes, wallet deltas, background loops: untouched. The counter is read once in a `finally:` and only feeds a histogram.

**Risk that the corrected metric changes a decision:** yes, and that is the point — C50's PostgREST→direct-pool sizing reads this metric, and it was biased low against exactly the phase that motivates the migration. Any sizing analysis already run against the old numbers should be redone.

**CI risk:** the RLS suite now runs in isolation (`-c /dev/null`) rather than alongside `backend/tests/conftest.py`. That is the invocation `CLAUDE.md` prescribes and should be at least as reliable, but it is a different invocation than the one it was implicitly getting — see "not verified" below.

## 5. User-experience effect

**Nobody.** Backend observability and CI configuration only. No rider, driver, corporate-admin, or internal-admin surface changes. Nothing is visible mid-session to a rider mid-ride or a driver online. No copy or notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/repositories/_base.py` | `_db_call_count` → `ContextVar[Optional[list]]`; `reset`/`get`/`_incr` updated; comment rewritten to explain isolation vs. additivity | Make child-task DB calls reach the parent's count without losing per-attempt isolation |
| `backend/tests/test_dispatch_metrics.py` | Added `test_db_call_counter_includes_calls_made_in_gather_children` and `test_db_call_counter_isolates_concurrent_dispatch_attempts` | Regression coverage; the first fails at 1-instead-of-5 against the old implementation |
| `.github/workflows/ci.yml` | Added "Run RLS role-level tests (real Postgres)" step; corrected two comments describing `tests/rls` as already CI-wired | Restore the auth-boundary suite the `--ignore` removed from CI |

## 7. Before / after

```python
# Before — child-task increments written to a context copy and discarded
_db_call_count: _ContextVar[int] = _ContextVar("_db_call_count", default=0)

def reset_db_call_count() -> None:
    _db_call_count.set(0)

def get_db_call_count() -> int:
    return _db_call_count.get()

def _incr_db_call_count() -> None:
    _db_call_count.set(_db_call_count.get() + 1)
```

```python
# After — a context copy binds the SAME list, so a child's increment is
# visible to the parent; reset() rebinds a fresh list to keep attempts isolated
_db_call_count: _ContextVar[Optional[list]] = _ContextVar("_db_call_count", default=None)

def reset_db_call_count() -> None:
    _db_call_count.set([0])

def get_db_call_count() -> int:
    counter = _db_call_count.get()
    return counter[0] if counter is not None else 0

def _incr_db_call_count() -> None:
    counter = _db_call_count.get()
    if counter is not None:
        counter[0] += 1
```

Observed effect for a dispatch attempt issuing 1 parent call and gathering two 2-call children: **1 → 5**.

## 8. Rollback plan

`git revert` is sufficient and complete for both commits. Neither writes to any table, mutates live data, or changes an API contract:

- The counter change affects only the value passed to an in-process histogram. Reverting restores the old (under-counting) value immediately on the next deploy; no data to unwind, since `utils/metrics.py` is per-process and in-memory.
- The CI change is workflow configuration only. Reverting the `tests/rls` step restores the prior state (that suite not running) with no artifact to clean up.

No feature flag is warranted: neither change is user-visible, and neither can be observed mid-session by a rider or driver. `DISPATCH_POOL_DSN` / `dispatch_direct_pool_enabled` are untouched by this work.

## 9. Verification performed

- [x] **Counter semantics verified against the shipped file text**, not a paraphrase: the `_db_call_count` assignment and all three functions were AST-extracted from `backend/repositories/_base.py` and executed. Results — gather children counted (5/5), two concurrent attempts isolated (5 and 5), no-window increments not counted (0), no cross-context leak between two fresh `contextvars.Context()` runs (0, 0).
- [x] `python -m py_compile` on both modified Python files.
- [x] **Blast-radius grep** — `reset_db_call_count|get_db_call_count|_incr_db_call_count|_db_call_count` and `spinr_dispatch_attempt_db_calls` across `backend/`, `docs/`, `.github/`. Consumers enumerated in §4.
- [x] `ci.yml` parses as YAML; a checker script asserts every `--ignore=<dir>` in the mocked step has a matching dedicated step that runs that dir. Both pass.
- [x] `grep -rn "tests/rls" .github/workflows/` confirmed the claim being corrected — only the `--ignore` matched.
- [x] Reviewed against `CLAUDE.md` conventions: dual-import pattern preserved in `matching.py` (untouched), metric naming unchanged, no `print()`, no PII added, no error silenced.
- [x] Line length ≤ 120 (`backend/ruff.toml`) on every added line.
- [x] Pre-commit security hook passed on both commits (secrets, forbidden files, PII-in-logs, money arithmetic).
- [ ] Not feature-flagged — justified above: no user-visible surface.

## 10. What was NOT verified

State the boundary explicitly rather than letting the checklist imply full coverage:

- **`pytest` was never run.** This environment has no `pytest` installed and no network to install it, so the two new regression tests were not executed by the runner. Their *logic* was verified by executing the real counter functions extracted from the shipped file (above), but the pytest wiring itself — fixtures, `@pytest.mark.anyio` collection, the `from repositories import _base` import path under the suite's `conftest.py` — is unexercised. The tests mirror the existing `test_db_call_counter_tracks_real_run_sync_calls` in structure and import style, which reduces but does not eliminate that risk.
- **The new `tests/rls` CI step has never run.** It is modelled on the adjacent `tests/direct_pool` step and on the invocation `CLAUDE.md` documents, but whether that suite passes under `-c /dev/null --confcutdir=tests/rls` against the CI `postgres:15` service is unconfirmed from here. It previously ran under a *different* invocation (inside the mocked step, with `backend/tests/conftest.py` loaded). If it fails on first run, the failure is pre-existing suite/environment drift surfaced by this step, not caused by it — but it will need triage before merge rather than being re-ignored.
- **PR #4873 has zero check runs and conflicts with `main`** (`mergeable_state: dirty`). Nothing in this branch has been CI-validated, and the conflict is unresolved — these commits sit on top of `f4df2af` unchanged.
- **No staging or production run.** No real Supabase, no real dispatch attempt observed end to end; the corrected count of 5 is from the isolated harness, not from a live `_match_driver_to_ride_attempt`.
- **Review findings 3–5 are not addressed here** (pool `in_use` gauge reporting `pool_size`; the startup-only flag read vs. the "no redeploy" rollback claim; unmeasured added metrics-lock traffic on every `run_sync`). They remain open on PR #4873.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no live-data remediation needed — justified in §8)
- [x] Blast radius is stated, not assumed (§4, from an enumerated grep)
- [x] No silent behavior change to an already-shipped flow — the only behavior change is to a metric value that no dashboard or alert currently reads
