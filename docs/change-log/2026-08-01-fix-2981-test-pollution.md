# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code (session) |
| Surface(s) | backend (test infrastructure only — `core/lifespan.py` change is test-env-gated) |
| Domain (Sentry tag) | n/a (test-infra fix; no production code path changes) |
| PR / commit link | see PR (closes #2981) |
| Related issue or gap ID | #2981 |

## 1. Issue / gap identified

`backend-test` on `main` intermittently fails 10 specific tests (out of ~6200) only
when the FULL suite runs in CI's exact collection order — never individually or in
small groups. Failure signatures: unrelated mocks (`insert_one`, `get_rows`) showing
extra or missing calls, sometimes against a table (`driver_statements`, `drivers`)
the failing test never touches itself.

## 2. Root cause

Two independent causes were bundled together in the original 10-test list:

**(a) Real background loops firing during unrelated HTTP-level tests (the primary,
multi-test cause).** `core/lifespan.py`'s FastAPI `lifespan()` unconditionally
starts all 16 production background loops (`referral_payout_loop`,
`driver_statement_job`, etc.) via `asyncio.create_task` on *every* app startup —
including every test that instantiates `TestClient(app)` (used by most HTTP-level
route tests). Each loop's body runs once immediately on start (loop-then-sleep, not
sleep-then-loop), so real production logic executes concurrently with whatever
`db_supabase.*` function that specific test had `patch()`ed for its own narrow
purpose, landing extra/foreign calls on that test's mock.

Worse, `core/lifespan.py`'s shutdown does `task.cancel()` +
`await asyncio.gather(*background_tasks, return_exceptions=True)`, but a task
that's mid-`await` inside `repositories/_base.py`'s `run_sync()` — i.e. awaiting a
`loop.run_in_executor()` future wrapping a synchronous Supabase call already
running on a worker thread of the shared, module-level `_DB_EXECUTOR`
`ThreadPoolExecutor` — does **not** actually stop when the outer task is
cancelled: `concurrent.futures.Future.cancel()` is a no-op once a thread has
started running, so the thread keeps executing to completion regardless, calling
into whatever the `supabase` module attribute happens to be bound to *at that
moment*. Since `mock_supabase_client` is rebound to a fresh mock every test, a
slow-to-finish thread from test N's TestClient can complete during test N+2's
active `patch()` window and register its own call there — explaining why the
symptom appeared several tests removed from any test that plausibly caused it
(exactly what `test_data_transfer_search_route.py`'s pre-existing `_entity_call()`
comment about "the Meta conversions sender" already described for one narrow
case).

This affected: `test_admin_document_upload.py`,
`test_compliance_reports_http.py` (both tests), `test_data_transfer_search_route.py`
(all three), and `test_admin_analytics_coverage.py`'s 503 (a `_DB_EXECUTOR` real
network attempt against `test.supabase.co` exhausting/contending the shared
executor/circuit-breaker for an unrelated request).

**(b) An unrelated, pre-existing time-bomb in `test_referral_payout_batching.py`
(a second, independent cause that happened to also be in the reported 10).**
`utils/referral_payout.py`'s expiry check uses real
`datetime.now(timezone.utc)` against a referral's deadline
(`referral_applied_at` + 30-day global window). Three of that file's tests
hardcoded `referral_applied_at="2026-07-01T00:00:00+00:00"`. Once real wall-clock
time passed 2026-07-31 (30 days later), every referral in those fixtures crossed
from "still pending" to "expired," which triggers an extra `_record_expiry()` →
`insert_one` call the tests' `assert_not_awaited()`/`assert_awaited_once()` don't
expect. This reproduces in complete isolation (confirmed via `git stash` against
unmodified `main`) — it has nothing to do with (a) or with full-suite ordering;
it was a real-time coincidence that it started failing around the same CI window
as (a).

## 3. Fix / remediation

- **`backend/core/lifespan.py`**: skip spawning all 16 background loops when
  `settings.ENV.lower() == "test"` (the value `tests/conftest.py` sets before any
  backend import). Every loop already has its own direct-call unit test (e.g.
  `test_referral_payout_batching.py` calls `referral_payout._tick()` directly), so
  no test coverage is lost — only the incidental, nondeterministic re-execution of
  loop bodies via `TestClient(app)` lifespan is removed.
- **`backend/tests/test_referral_payout_batching.py`**: replaced hardcoded
  absolute-date fixtures (`"2026-07-01T00:00:00+00:00"`) with dates computed
  relative to `datetime.now(timezone.utc)` (`_iso(delta_days)`), preserving each
  test's intent (ride before/after the referral window) independent of the date
  the suite happens to run on.

## 4. Risk & impact on existing functionality

- **Blast radius of the `core/lifespan.py` change: isolated to `ENV=="test"`.**
  Grepped every reader of `app.state.background_tasks` and the `background_tasks`
  list in `core/lifespan.py` (`_spawn`, shutdown's `task.cancel()` +
  `asyncio.gather`, and the startup log line) — none behave differently when the
  list is simply empty; shutdown already guards with
  `if background_tasks: ...`. No other module imports or asserts on the *count* of
  spawned tasks (grepped `tests/*.py` for `lifespan`/`background_tasks` — the only
  hits are unrelated `BackgroundTasks` FastAPI-param usages in individual route
  handlers, and doc-comment references in `test_replay_safety_payment_loops.py`).
  Production and staging are unaffected — `ENV` there is never `"test"`.
- **Referral test fixture change**: touches only `test_referral_payout_batching.py`
  test data, not `utils/referral_payout.py` production logic. No other test file
  imports these fixtures.
- Neither change touches ride state, money/wallet deltas, dispatch, or auth.

## 5. User-experience effect

None. Test-infrastructure-only change; no user-facing surface is touched, and
`core/lifespan.py`'s behavior in `development`/`staging`/`production` is
byte-for-byte unchanged (the new branch only triggers when `ENV=="test"`).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/lifespan.py` | `_spawn()` now no-ops when `settings.ENV.lower() == "test"` | Stop the 16 real production background loops from firing during `TestClient`-based tests and polluting unrelated tests' mocks |
| `backend/tests/test_referral_payout_batching.py` | Fixture dates (`referral_applied_at`, ride `created_at`) computed relative to `datetime.now(timezone.utc)` instead of hardcoded `"2026-07-01"` | Fix an unrelated time-bomb: the hardcoded date + 30-day window had crossed into "expired" by the time this session ran |
| `docs/change-log/2026-08-01-fix-2981-test-pollution.md` | New Change Impact Log | Required by `CLAUDE.md` for any behavior-changing commit |

## 7. Before / after

```python
# Before (backend/core/lifespan.py)
def _spawn(name: str, coro_factory):
    try:
        task = asyncio.create_task(_restartable(name, coro_factory), name=name)
        background_tasks.append(task)
        logger.info(f"Started background task: {name}")
    except Exception as e:
        logger.error(f"Failed to start background task {name}: {e}", exc_info=True)
```

```python
# After
def _spawn(name: str, coro_factory):
    if _skip_background_loops:
        logger.info(f"Skipped background task in ENV=test: {name}")
        return
    try:
        task = asyncio.create_task(_restartable(name, coro_factory), name=name)
        background_tasks.append(task)
        logger.info(f"Started background task: {name}")
    except Exception as e:
        logger.error(f"Failed to start background task {name}: {e}", exc_info=True)
```

```python
# Before (backend/tests/test_referral_payout_batching.py)
def _rider(uid, referred_by="referrer_1", applied_at="2026-07-01T00:00:00+00:00"):
    ...
```

```python
# After
def _rider(uid, referred_by="referrer_1", applied_at=None):
    if applied_at is None:
        applied_at = _iso(-10)  # 10 days ago, deadline still in the future
    ...
```

## 8. Rollback plan

Pure `git revert` is sufficient and safe here — this is test-infrastructure code,
not a live-tested surface; nothing here writes to Stripe, wallets, or ride state.
No feature flag or migration needed. Reverting restores the exact pre-existing
(flaky) test behavior with no data-level cleanup required.

## 9. Verification performed

- [x] Automated tests run: full suite (`python -m pytest tests/ -q -p no:randomly --no-cov`),
      three consecutive full runs from repo root:
      - Baseline (before fix, this session): `10 failed, 6538 passed, 8 skipped, 1 xfailed`
      - Run 1 (after `core/lifespan.py` fix only): `4 failed, 6544 passed, 8 skipped, 1 xfailed`
        (all 4 remaining were the 3 referral time-bomb tests + 1 unrelated
        pre-existing flaky test, confirmed via `git stash` to fail identically on
        unmodified `main`)
      - Run 2 (after both fixes): `1 failed, 6547 passed, 8 skipped, 1 xfailed` — the
        1 remaining (`test_spinr_pass_subscription.py::TestExpiryWarning3Day::
        test_3d_warning_skipped_when_flag_already_set`) is a pre-existing,
        unrelated day-boundary time bomb, confirmed via `git stash` to fail
        identically on unmodified `main`, **not** one of the 10 tests in #2981,
        and out of scope for this fix (left untouched per instructions not to
        touch unrelated surfaces)
      - Targeted re-run of all originally-10 failing tests individually and
        together: all pass, deterministically, across repeated runs
- [x] `python3 -m pytest` used per this repo's convention (run from `backend/`,
      not `pytest` bare) — confirmed via `pytest.ini`'s `testpaths = tests`
- [ ] Manual repro in staging — not applicable; this is a local/CI test-runner
      fix with no staging-deployable surface
- [x] Blast-radius grep performed (see §4) — every reader of
      `background_tasks`/`app.state.background_tasks` in `core/lifespan.py`
      checked; every `tests/*.py` reference to `lifespan`/`background_tasks`
      checked
- [x] Reviewed against `CLAUDE.md`'s "Background task safety" convention — the
      16 loops themselves are unchanged and still replay-safe; this only skips
      *starting* them under `ENV=="test"`
- [ ] Feature-flagged — not applicable (test-only, `ENV`-gated, not
      user-visible)
- [ ] `npm run build` — not applicable, no frontend surface touched

## What was NOT verified

- Did not verify behavior under CI's actual GitHub Actions runner (only this
  sandbox environment) — collection order, thread scheduling, and timing could
  differ, though `-p no:randomly` was used to keep ordering deterministic and
  match what the issue described as the reproducing condition.
- Did not run with `pytest-xdist` / parallel workers — the issue's CI run and
  this investigation both assumed serial collection order.
- The pre-existing, unrelated `test_spinr_pass_subscription.py` day-boundary
  flake was identified and left unfixed (out of scope for #2981); it should be
  filed as its own follow-up rather than silently left for a future engineer to
  rediscover.
- Coverage (`--cov`) was disabled (`--no-cov`) for these verification runs to
  keep full-suite iteration time under ~7 minutes instead of the ~10+ minutes
  it originally timed out at with coverage instrumentation on; did not
  separately re-verify coverage numbers/`--cov-fail-under=60` are unaffected,
  though nothing in this change touches statement coverage in a way that would
  plausibly move that number (test-only + one `if` branch gated on `ENV`).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data cleanup)
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow — production/staging
      `core/lifespan.py` behavior is unchanged; only `ENV=="test"` is affected
