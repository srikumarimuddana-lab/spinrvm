# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (session_01Sspqro7zzjKdTbUh6D61wQ) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments, dispatch, rides |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | ACTION_ITEMS.md C69 |

## 1. Issue / gap identified

6 loguru-backed log call sites across 4 backend modules pass `extra={...}`, a
stdlib-`logging` kwarg that loguru does not support. loguru silently hands the
dict to `str.format(**kwargs)`; since none of the affected messages contain a
matching `{...}` field, the dict is dropped with no error, no warning, and no
test failure. The log line renders with the message text only — the
structured context never reaches the log or the loguru→Sentry bridge.

## 2. Root cause

Same defect family as C60 (`exc_info=` on loguru) and C65 (the scanner's
selector missing re-exported loguru loggers): CLAUDE.md's Observability
section told authors to "use structured context via `extra={...}`", which is
correct for the ~252 stdlib `logging` modules and silently wrong for the ~50
loguru modules — the doc never distinguished them, so a stdlib-habituated
author reasonably reached for `extra=` on a loguru logger too. loguru's own
API offers no `extra=` at all; the equivalent is `logger.bind(**kwargs)`,
which returns a new logger pre-bound with that context.

## 3. Fix / remediation

Mechanical, behavior-preserving replacement at all 6 call sites:
`logger.<level>(msg, extra={...})` → `logger.bind(**{...}).<level>(msg)`. Same
message text, same key/value data, same log level — only how the context is
handed to loguru changed, from a silently-dropped kwarg to loguru's real
context-binding API. Also extended `backend/tests/test_loguru_call_conventions.py`
with a third static detector (`test_no_extra_kwarg_in_loguru_calls`, mirroring
the existing `exc_info=` detector from C60/C65) so a future `extra=` on a
loguru logger fails CI instead of shipping silently, and added a short
loguru/stdlib split note to CLAUDE.md's Observability Conventions section so
the next author isn't misled the same way.

**PII note:** `backend/repositories/_base.py` is a hot path with a dedicated
PII-logging regression suite (`backend/tests/test_base_pii_logging.py`). That
file was read in full (not just grepped) before touching `_base.py`. The 3
`extra=` call sites fixed there carry only `reason`, `stage`, `retry_policy`,
`attempt`, and `overdue_seconds`/`waited_seconds` (deadline-budget bookkeeping,
all non-PII per CLAUDE.md's never-log list) — no lat/lng, name, phone, email,
or other PII-relevant value was moved into any `.bind()` call. The dedicated
PII tests continue to pass unchanged (they exercise a different code path in
the same file — the generic Postgres-error catch-all — not the 3 lines
touched here).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these exact 6 call sites**, in 4 files:
  `backend/repositories/_base.py` (3 sites — deadline-rejection logging in
  `run_sync`), `backend/repositories/dispatch_pool.py` (1 site — deadline
  rejection in `acquire()`), `backend/repositories/wallet_repo.py` (1 site —
  the stuck-Stripe-event error in `mark_stripe_event_processed`),
  `backend/routes/rides/estimates.py` (1 site — the haversine-undercharge
  ERROR log). No other reader/writer of these code paths was touched; the
  change is confined to the argument-passing style of an already-executing
  log statement, not the surrounding control flow, DB call, or business logic.
- **Nothing downstream reads the dropped `extra` payload today** (that was the
  bug — it was reaching nobody), so restoring it via `.bind()` can only add
  visibility, not remove any behavior a caller currently depends on.
- One test regression was found and fixed as part of this change:
  `tests/test_wallet_repo.py::test_mark_stripe_event_processed_swallows_db_error_but_logs_loudly`
  patched `wallet_repo.logger` directly and asserted
  `mock_logger.error.assert_called_once()` with `kwargs["extra"][...]`. Since
  the call now goes through `logger.bind(...).error(...)`, the `.error()` call
  lands on the child mock `mock_logger.bind.return_value`, not on
  `mock_logger.error` directly — the old assertion would pass-vacuously
  (mock auto-creates `.error` as a callable, so it wouldn't raise on the
  attribute, it would just never be called, which the test correctly caught
  as a failure). Updated to assert
  `mock_logger.bind.assert_called_once_with(domain="payments", event_id="evt_1")`
  and `mock_logger.bind.return_value.error.assert_called_once()`. Grepped the
  rest of the suite for the same `extra=`+mock-logger pattern
  (`grep -rln "extra=" tests/ | xargs grep -l "mock_logger\|patch.*logger"`) —
  the only other hit (`tests/test_rides_payments_coverage.py`) is an unrelated
  `PaymentResult(extra=...)` dataclass field, not a logger call, and needed no
  change.
- No interaction with the ride state machine, wallet/money deltas, or any of
  the 40 background loops — these are observability-only log statements; none
  of them write to a table, mutate ride/driver state, or move money.

## 5. User-experience effect

None. No rider, driver, corporate-admin, or internal-admin facing behavior
changes. This is a backend-only observability fix: an operator looking at
these specific ERROR/WARNING log lines (or their Sentry events, once the
loguru→Sentry bridge forwards `record["extra"]`) will now see the structured
fields (e.g. `haversine_km`, `fare_mode`, `event_id`, `retry_policy`) that
were previously silently dropped. Nothing user-visible changes mid-session or
otherwise.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/repositories/_base.py` | 3 `logger.warning/error(msg, extra={...})` → `logger.bind(**{...}).warning/error(msg)` | Restore dropped deadline-rejection context (`reason`, `stage`, `retry_policy`, `attempt`, `overdue_seconds`/`waited_seconds`) |
| `backend/repositories/dispatch_pool.py` | 1 `logger.warning(msg, extra={...})` → `logger.bind(...).warning(msg)` | Restore dropped `overdue_seconds` on the pre-acquire deadline rejection |
| `backend/repositories/wallet_repo.py` | 1 `logger.error(msg, extra={...})` → `logger.bind(...).error(msg)` | Restore dropped `domain`/`event_id` on the stuck-Stripe-event ERROR |
| `backend/routes/rides/estimates.py` | 1 `logger.error(msg, extra={...})` → `logger.bind(...).error(msg)` | Restore dropped `haversine_km`/`fare_mode` on the undercharge-alert ERROR — the field the whole alert exists to surface |
| `backend/tests/test_loguru_call_conventions.py` | Added `test_no_extra_kwarg_in_loguru_calls` detector; extended the docstring and `test_detectors_catch_the_original_defects` to cover the third defect class | Prevent recurrence — CI now fails on any future `extra=` on a loguru logger |
| `backend/tests/test_wallet_repo.py` | Updated `test_mark_stripe_event_processed_swallows_db_error_but_logs_loudly` to assert on `.bind(...).error(...)` instead of `kwargs["extra"]` on `.error(...)` | The old assertion shape no longer matches the fixed call; test would otherwise fail on the correct fix |
| `CLAUDE.md` | Added a short loguru-vs-stdlib `extra=`/`exc_info=` clarifying note under Observability Conventions | The prior single-sentence "use `extra={...}`" line was silently wrong for ~50 loguru modules and is what produced this bug class in the first place |

## 7. Before / after

```python
# Before (backend/routes/rides/estimates.py) — extra= silently dropped by loguru
logger.error(
    "[estimate] road distance unavailable — billing haversine (undercharge)",
    extra={"haversine_km": round(float(haversine_km), 3), "fare_mode": _fare_mode},
)
```

```python
# After — context reaches the log record via loguru's real API
logger.bind(haversine_km=round(float(haversine_km), 3), fare_mode=_fare_mode).error(
    "[estimate] road distance unavailable — billing haversine (undercharge)"
)
```

## 8. Rollback plan

Pure code change, no data migration, no feature flag, no config. Revert the
commit(s) via `git revert` — safe here (unlike a ride-state/money change)
because nothing downstream currently reads or depends on the previously-lost
`extra` context; a revert only returns to the prior (also broken, but no
worse) state where those fields are silently dropped again. No live data
(Stripe charges, wallet deltas, ride state) is touched by this change, so no
data-level remediation is needed either direction.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_loguru_call_conventions.py
  tests/test_base_pii_logging.py tests/test_dispatch_pool.py
  tests/test_wallet_repo.py tests/test_ride_estimate_branches.py
  tests/test_fare_road_basis_default.py --no-cov -q` — 158 passed, 1
  pre-existing failure (`test_no_percent_style_placeholders_in_loguru_calls`
  on `routes/rides/matching.py:397`, a `%s`/`%d` defect unrelated to this
  change and pre-existing on `main` before this branch — confirmed via
  `git stash` + re-run against the unmodified tree).
- [x] New detector proven non-vacuous per the task's explicit requirement:
  temporarily reverted the `wallet_repo.py` fix back to `extra=`, re-ran
  `test_no_extra_kwarg_in_loguru_calls` — it failed and named the exact
  line (`repositories/wallet_repo.py:428`) — then restored the fix and
  re-ran to confirm it passes again.
- [x] `ruff check` run on every file this change touched (backend + tests) —
  all pass. `ruff check .` run repo-wide for context — 40 pre-existing
  findings, none introduced by this diff, none in the touched files.
- [x] Blast-radius grep performed: `grep -n "extra=" backend/repositories/_base.py
  backend/repositories/dispatch_pool.py backend/repositories/wallet_repo.py
  backend/routes/rides/estimates.py` before and after (6 → 0 hits), and a
  second grep across `tests/` for any other mock-logger assertion on
  `extra=` that this change could silently break (found and fixed one, in
  `test_wallet_repo.py`; the one other hit was unrelated).
- [x] `backend/tests/test_base_pii_logging.py` read in full (not just
  grepped) before touching `backend/repositories/_base.py`, per this task's
  explicit instruction and the file's own PII sensitivity; confirmed the 3
  call sites fixed there carry no PII-relevant values.
- [x] Reviewed against CLAUDE.md's observability convention (now updated by
  this same change to document the loguru/stdlib split going forward).
- [x] Feature-flag: not applicable — pure logging-argument-shape fix with no
  observable behavior change to any user-facing flow.

## 10. What was NOT verified

- Not verified against a real loguru→Sentry delivery in a live/staging
  environment — the fix was verified by reading loguru's actual call
  contract and by the existing mocked-logger unit tests (which cannot
  observe the loguru→Sentry bridge itself, only that `.bind()` was called
  with the right kwargs and `.<level>()` was called on the result). Whether
  `record["extra"]` those fields now populate actually surfaces in a Sentry
  event tag/context was reasoned about from `server.py`'s bridge code, not
  screenshotted or observed in a real Sentry project.
- No `admin-dashboard`/`rider-app`/`driver-app` files were touched, so the
  `npm run build` / visual-regression requirements in CLAUDE.md's pre-merge
  gates do not apply to this change.
- Did not re-run the full backend test suite (`pytest` with coverage) end to
  end — ran only the targeted files for the modules changed plus the
  loguru-convention and PII-logging suites, per the task's explicit test
  list. No reason to expect broader breakage (the change touches only
  logging-call argument shape, not control flow), but a full-suite run was
  not performed here.
- The one pre-existing, unrelated `%s`/`%d`-placeholder failure in
  `routes/rides/matching.py:397` was confirmed pre-existing (via
  `git stash`) but was not fixed — it is out of scope for C69 (a different
  defect class, `%s` not `extra=`) and is not one of the 4 files this item
  names.

## Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data
  migration or flag involved)
- [x] Blast radius is stated, not assumed (6 call sites in 4 named files,
  grepped before and after; one test dependency found and fixed)
- [x] No silent behavior change to an already-shipped flow — this restores
  log context that was already silently missing; no user-facing flow changes
