# Change Impact & Risk Log — C50 review follow-up (metric accuracy, RLS CI, pool gauge, flag contract)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-02 |
| Author | Codex-style review follow-up on PR #4873 (C50 Phase 0/1) |
| Surface(s) | backend, CI |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/pr-4873-codex-review-r0ag6j` (commits `59ba299`, `1b67917`, `4586307`, `c29488f`, + this entry), on top of PR #4873 head `f4df2af` |
| Related issue or gap ID | ACTION_ITEMS.md C50 (T3, T9, T10, T11); review findings 1–5 |

## 1. Issue / gap identified

Five defects found reviewing PR #4873. All are silent — each ships green and misleads rather than failing:

1. **`spinr_dispatch_attempt_db_calls` under-counts every dispatch attempt.** The per-attempt DB-call counter missed every `run_sync` call issued from an `asyncio.gather()` child.
2. **`--ignore=tests/rls` removed the repo's only DB-role-level RLS coverage from CI**, justified by a comment asserting a dedicated step existed for it. No such step existed in any workflow file.
3. **`spinr_db_direct_pool_in_use` reported total pool size, not connections in use** — so an idle pool at `min_size=1` permanently read "1 in use" and the gauge could never reach 0.
4. **`dispatch_direct_pool_enabled` was documented as a symmetric "no redeploy" switch**, but it is read once at startup: enabling needs a restart, and rollback works only if Phase 2 re-reads the flag per attempt — an obligation nothing recorded.
5. **The added `run_sync` instrumentation had no perf numbers**, on a path with stated P95 SLAs, while adding two global-lock operations to every DB call in the backend.

## 2. Root cause

**(1)** `_db_call_count` was a plain `ContextVar[int]`. asyncio runs each `gather()`/`create_task()` child in a **copy** of the current context, so a child's `_var.set(_var.get() + 1)` writes to that copy and is discarded when the child completes. The ContextVar gave the isolation it was chosen for, but not additivity — two different properties, and only the first was reasoned about in the original comment.

`_match_driver_to_ride_attempt` fans its enrichment reads out through `asyncio.gather()` (`matching.py:961`), so at minimum `_fetch_rider` → `get_user_by_id` and `_fetch_incentives` → `match_ride_incentives` (`incentive_service.py:276`, `await db.run_sync(...)`) were dropped from every observation.

Verified empirically before fixing — a parent issuing 1 call and gathering two children of 2 calls each observed **1 of 5**.

**(2)** The `--ignore` was added on the correct premise (the mocked-DB step's placeholder DSN is truthy, which defeats both suites' `_DSN`-truthiness self-skip) but paired with an unverified claim that `tests/rls` had its own real-Postgres step. It did not: `grep -rn "tests/rls" .github/workflows/` matched only the new `--ignore` itself. The suite had been running inside the mocked step (`pytest.ini`'s `testpaths = tests` collects it; its conftest-level `pytestmark` does not propagate to sibling modules — the same latent bug T11 documented for `tests/direct_pool`; and that step's connection string previously held real service credentials).

**(3)** `psycopg_pool.get_stats()` reports `pool_size` (every connection the pool manages, idle included) and `pool_available` (the idle subset). The gauge read `pool_size` alone, which counts idle connections as busy. The existing `acquire()` tests stubbed `get_stats()` with `pool_size` only, so they encoded the same assumption and could not have caught it.

**(4)** `init_pool()` is called only from `lifespan.init_database`, so the flag is sampled once per process; nothing re-reads it and nothing closes the pool when it flips. The "no redeploy, ≤60s" claim in `schemas.py` and the plan doc's T10 row held only for the OFF direction, and only conditionally — it depends entirely on a Phase 2 behaviour (per-attempt flag re-read) that was never written down as a requirement.

**(5)** Not a defect — an unmeasured claim. The PR template requires perf numbers when an SLA-critical path is touched and the field was blank, while `_timed_func` added two `metrics.observe()` calls (each taking `utils/metrics.py`'s single module-level `threading.Lock`) to every DB call process-wide, with a 64-thread DB executor.

## 3. Fix / remediation

**(1)** The counter now holds a one-element list instead of an int. A context copy binds the *same* list object, so a child's `[0] += 1` is visible to the parent, while `reset_db_call_count()` rebinds a fresh list so separate attempts stay isolated. The default is `None`, not a module-level `[0]` — a shared mutable default would be the single object every un-reset context sees and would accumulate across unrelated call sites forever. With `None`, increments outside a counting window are not counted, which is the correct semantics.

**(2)** Added the missing dedicated `tests/rls` CI step, mirroring the `tests/direct_pool` one and using the isolated `-c /dev/null --confcutdir=tests/rls` invocation `CLAUDE.md` documents. The `--ignore` is kept — correct on its own terms. Both misleading comments corrected, plus a note that an `--ignore` without a matching step silently drops a suite.

**(3)** Extracted `_in_use(pool)`, used by both gauge call sites, computing `pool_size - pool_available`. Falls back to `pool_size` when `pool_available` is absent (a stats-key change degrades to the previous behaviour rather than raising on the dispatch path) and clamps at 0 (the two keys are sampled under separate locks and can momentarily disagree). Test fakes now carry both keys, plus a `TestInUseGauge` class covering the idle-pool case that was the bug.

**(4)** Documented the asymmetry at all four places that assert the contract — `schemas.AppSettings`, `dispatch_pool.py`'s module docstring, `lifespan.py`'s gate, and the plan doc's T10 row — and stated the per-attempt flag re-read as a **requirement of T13**, not an implementation detail. No behaviour change: the flag is still default-off and still read only at startup.

**(5)** Measured it and recorded the numbers in `_timed_func`'s docstring, so the cost is settled rather than re-argued. No code change — the data says no mitigation is warranted (see §9).

## 4. Risk & impact on existing functionality

**Blast radius: isolated (backend observability only) + CI configuration.** No ride state, money, auth, or user-facing path is touched.

Grep performed — `reset_db_call_count|get_db_call_count|_incr_db_call_count|_db_call_count` across `backend/`:

- **Exactly one production consumer**: `routes/rides/matching.py:199` (reset) and `:1145` (observe). No other module reads the counter.
- `_incr_db_call_count()` is called from `run_sync` (`_base.py:414`), i.e. on **every DB call process-wide**. Its behavior outside a counting window changes from "increment a context-local int" to "no-op after an `is None` check" — marginally *less* work per DB call, and nothing read that value before.
- Metric consumers of `spinr_dispatch_attempt_db_calls`: only `docs/audit/2026-09-02-pgbouncer-direct-pool-migration-plan.md` names it, as a planned input. **No dashboard or alert queries it yet**, so the corrected (higher) values cannot break an existing threshold.

Ride state machine, insurance-period writes, wallet deltas, background loops: untouched. The counter is read once in a `finally:` and only feeds a histogram.

**Findings 3–5 blast radius:**

- `_in_use()` is new and called from exactly two sites, both inside `dispatch_pool.acquire()`. `acquire()` is in turn called only from `run_query()` (`dispatch_pool.py:305`), and `grep` for `run_query` outside `dispatch_pool.py` and the tests returns **nothing** — Phase 2 (T12/T13) is not built. So finding 3's fix cannot reach any live path today; it corrects a gauge before anything depends on it.
- `spinr_db_direct_pool_in_use` has no dashboard or alert consumer (same grep as above) — the corrected values cannot move an existing threshold.
- Finding 4 is comment-only across four files. The flag's value is read from `app_settings` in exactly one place, `lifespan.py:94`; `routes/admin/settings.py:496` exposes it for write, and `schemas.py:695` declares it. No behaviour changed.
- Finding 5 is a docstring. No executable change.

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
| `backend/repositories/_base.py` | `_timed_func` docstring records the measured cost of the two added `observe()` calls | Finding 5: close the blank "Perf numbers" field with data instead of a shrug |
| `backend/repositories/dispatch_pool.py` | Added `_in_use()`; both gauge sites use it. Module docstring gains the "flag is read once at startup" section | Findings 3 and 4 |
| `backend/tests/test_dispatch_pool.py` | Fakes carry `pool_available`; entry/finally gauge assertion now expects 2.0 not 3.0; added `TestInUseGauge` | Finding 3 regression coverage |
| `backend/schemas.py` | `dispatch_direct_pool_enabled` comment documents the OFF→ON / ON→OFF asymmetry and the T13 obligation | Finding 4 |
| `backend/core/lifespan.py` | Comment notes this is the sole flag read, so it is sampled once per process | Finding 4 |
| `docs/audit/2026-09-02-pgbouncer-direct-pool-migration-plan.md` | T10 row gains an "Asymmetry — binds T12/T13" bullet | Finding 4: keep the plan doc and the code agreeing |

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

```python
# Before (finding 3) — idle pool at min_size=1 reports 1 connection in use
_metric_gauge("spinr_db_direct_pool_in_use", float(_pool.get_stats().get("pool_size", 0)))
```

```python
# After — pool_size minus the idle subset, so an idle pool reads 0
_metric_gauge("spinr_db_direct_pool_in_use", _in_use(_pool))
```

## 7a. Perf numbers (finding 5 — the PR template field that was blank)

`_timed_func` adds two `metrics.observe()` calls per DB call, each taking `utils/metrics.py`'s single module-level `threading.Lock`, with a 64-thread DB executor (`DB_THREAD_POOL_SIZE` default). Measured against the real `backend/utils/metrics.py`:

| Measurement | Value |
|---|---|
| Uncontended `observe()` | ~1.5 µs/call |
| Lock ceiling, 64 threads doing nothing but `observe()` | ~69k observe/s process-wide |
| Metrics locks per `run_sync` | 6 (4 pre-existing gauges + 2 added) |
| Implied `run_sync` ceiling, before → after | ~17.1k/s → ~11.4k/s per process |
| **Realistic: 64 threads, 5 ms I/O-bound call each** | **+7.9 µs/call (+0.15%)** |

**Conclusion: no mitigation warranted.** +7.9 µs against a millisecond-scale DB call is negligible, and the ~11.4k run_sync/s ceiling is orders of magnitude above real load. The numbers are recorded in `_timed_func`'s docstring so this doesn't get re-litigated. If DB throughput ever approaches that ceiling, the fix is to shrink `metrics.observe()`'s critical section (its cumulative bucket loop runs inside the lock) — not to drop instrumentation. Deliberately **not** done here: `metrics.py` is shared by every domain, and optimising it on this PR's behalf would be scope creep against measurements that say it isn't needed.

Note the spinning-thread figure is the worst case (pure-Python loop, never releasing the GIL); real DB threads block on socket I/O between calls, which is why the realistic row is the one to plan against.

## 8. Rollback plan

`git revert` is sufficient and complete for all five fixes. None writes to any table, mutates live data, or changes an API contract. Findings 3–5 are additionally inert at runtime: `_in_use()` sits behind `acquire()`, which has no production caller until Phase 2; findings 4 and 5 are comments only.

- The counter change affects only the value passed to an in-process histogram. Reverting restores the old (under-counting) value immediately on the next deploy; no data to unwind, since `utils/metrics.py` is per-process and in-memory.
- The CI change is workflow configuration only. Reverting the `tests/rls` step restores the prior state (that suite not running) with no artifact to clean up.

No feature flag is warranted: neither change is user-visible, and neither can be observed mid-session by a rider or driver. `DISPATCH_POOL_DSN` / `dispatch_direct_pool_enabled` are untouched by this work.

## 9. Verification performed

- [x] **Counter semantics verified against the shipped file text**, not a paraphrase: the `_db_call_count` assignment and all three functions were AST-extracted from `backend/repositories/_base.py` and executed. Results — gather children counted (5/5), two concurrent attempts isolated (5 and 5), no-window increments not counted (0), no cross-context leak between two fresh `contextvars.Context()` runs (0, 0).
- [x] `python -m py_compile` on both modified Python files.
- [x] **Blast-radius grep** — `reset_db_call_count|get_db_call_count|_incr_db_call_count|_db_call_count` and `spinr_dispatch_attempt_db_calls` across `backend/`, `docs/`, `.github/`; plus `dispatch_direct_pool_enabled`, `acquire()`, `_in_use(`, and `run_query` for findings 3–4. Consumers enumerated in §4.
- [x] **`_in_use()` verified against the shipped file**, same AST-extract-and-execute method: 8/3 → 5; idle pool at min_size=1 → 0 (the bug, which the old code reported as 1); saturated 8/0 → 8; `pool_available` absent → falls back to 4; keys disagreeing (2/5) → clamped to 0; `None` pool → 0; empty stats → 0; `None`-valued keys → 0.
- [x] **Finding 5 measured**, not asserted — see §7a. Benchmarked the real `backend/utils/metrics.py` uncontended, at the 64-thread lock ceiling, and in an I/O-bound simulation.
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
- **`psycopg_pool` was never imported** (no PyPI access from this environment — `pip download psycopg-pool` fails with "from versions: none"). Finding 3's `pool_size` / `pool_available` semantics come from the library's documented `get_stats()` keys, not from a local run against the installed package. This is why `_in_use()` falls back to `pool_size` when `pool_available` is absent: if the key names ever differ from the documentation, the gauge degrades to the previous behaviour instead of raising on the dispatch path. **Worth a 30-second confirmation** by anyone with the package installed.
- **Finding 5's benchmark is a simulation, not production.** It runs `metrics.observe()` against `time.sleep()` stand-ins for DB I/O on this container's CPU, not against real Supabase on Fly. The relative conclusion (~0.15% of a 5 ms call) is robust to that; the absolute ceiling figures (~69k observe/s, ~11.4k run_sync/s) are machine-specific and should be treated as an order of magnitude, not a spec.
- **Findings 3 and 4 are pre-Phase-2 corrections.** Neither is exercised by a running code path today (`run_query`/`acquire` have no production callers; the flag stays default-off), so "verified" here means verified in isolation, not observed in a live dispatch.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no live-data remediation needed — justified in §8)
- [x] Blast radius is stated, not assumed (§4, from an enumerated grep)
- [x] No silent behavior change to an already-shipped flow — the only behavior change is to a metric value that no dashboard or alert currently reads
