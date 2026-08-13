# Change Impact & Risk Log — capacity saturation watchdog

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-07 |
| Author | Claude Code (session: postgres-scaling-supabase) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin (observability; no user-facing domain logic) |
| PR / commit link | branch `claude/postgres-scaling-supabase-ypnwiy` |
| Related issue or gap ID | `docs/LAUNCH_GATE_IMPLEMENTATION_PLAN.md` T10 ("DB pool saturation alerting — mechanism is built; observability isn't") |

## 1. Issue / gap identified

Supabase compute does not autoscale. The tier a burst arrives on is the tier it
gets handled with, and upgrading is a ~2-minute dashboard action — but only if
someone knows to do it **before** users feel it.

Nothing watched for that. `spinr_db_thread_pool_queue_depth`,
`spinr_db_calls_rejected_total`, and `spinr_db_circuit_state` were all emitted
and alerted **nowhere**. ADR-010's Grafana pipeline, which would have scraped
them, is still `Status: Proposed` and unimplemented. So the DB could saturate,
start rejecting calls, and open its circuit breaker with no signal to anyone.

Secondary defect found while building this: the thread-pool gauges are written
only from inside `run_sync`, and `spinr_db_thread_pool_threads` /
`_max_workers` only on the **success** path. During an outage — exactly when
someone reads them — they hold whatever the last successful call left behind.
If traffic stops entirely, the queue-depth gauge freezes too, making a
saturated-then-idle pool indistinguishable from a healthy one.

## 2. Root cause

The metrics were added when the circuit breaker and deadline machinery were
built, but the alerting half was deferred to ADR-010's Grafana pipeline, which
never shipped. T10 records this exactly: *"Mechanism is built; observability
isn't."*

The stale-gauge issue is a consequence of instrumenting inside `run_sync` only:
there was no way to sample the pool without a query having just run.

## 3. Fix / remediation

A new `capacity_watchdog` loop (60 s tick) that samples the signals in-process
and posts to `settings.ALERT_WEBHOOK_URL` — deliberately reusing **the one
alerting path that works in production today** (the same webhook `loop_watchdog`
uses) rather than waiting on ADR-010.

| Signal | Threshold | Why that shape |
|---|---|---|
| DB pool saturation | `queue_depth > 50` for 3 consecutive ticks | 50 is `loadtest/README.md`'s recorded breaking point. Sustained, because a brief queue during a burst is the pool working as designed |
| DB calls rejected | any increase; `reason=circuit_open` fires immediately | The breaker only opens after real failures — there is nothing to wait and see about |
| Rate-limit pressure | > 120 violations/min for 3 ticks | Separates a genuine burst from a limit set too low |

All thresholds are env-overridable (`CAPACITY_*`) so tuning does not need a code
change.

Plus `get_db_pool_stats()` in `repositories/_base.py`, which samples the
executor directly and refreshes the queue-depth gauge as a side effect — so a
`/metrics` scrape between two queries reports a current value.

### A real bug the tests caught

The first implementation keyed the cooldown off a `0.0` default:

```python
if now - _last_alerted.get(signal, 0.0) < COOLDOWN_SECONDS:
    return
```

`time.monotonic()` counts from an arbitrary origin near zero early in a
process's life, so this is true for the first `COOLDOWN_SECONDS` (30 min) of
uptime — **suppressing every alert right after a deploy or a scale-up, the
window where a burst is most likely and this watchdog is most needed.** Fixed
with an explicit `None` sentinel; two tests failed on it before the fix.

> **Note for follow-up (not fixed here):** `backend/utils/loop_alert.py:55-57`
> has the identical pattern with `COOLDOWN_SECONDS = 3600`, which means the
> existing loop-staleness alerting — the only alerting path currently live in
> production — is likely suppressed for the **first hour** after every deploy.
> That is a pre-existing defect in a file outside this commit's scope, so it is
> flagged rather than silently bundled. It should be fixed.

## 4. Risk & impact on existing functionality

**Blast radius: additive and isolated.** A new loop, a new read-only accessor,
and two registry entries. No existing loop, route, or query is modified.

- **Replay safety (CLAUDE.md background-loop contract):** the loop performs
  **no writes at all** — no DB row, no Redis key, no user-visible notification.
  The contract's claim-flag / idempotency-key / leader-lock options exist to
  prevent duplicate *side effects*; there are none here to duplicate. This is
  documented in the module docstring so a future reader does not "fix" it by
  adding a leader lock.
- **Per-replica alerting is intentional.** Pool saturation is a per-process
  condition and `utils/metrics.py` is explicitly per-process ("Each backend
  replica keeps its own counters"). A leader lock would mean the one elected
  replica reports its own healthy pool while seven saturated ones stay silent.
  Fan-out is bounded by a per-signal 30-minute cooldown instead, and every
  message carries its `FLY_MACHINE_ID` so duplicates are attributable.
  **Consequence to expect:** during a fleet-wide burst with all 8 machines
  awake, up to 8 alerts per signal per 30 minutes. That is the accepted cost of
  not going blind on 7 of 8 replicas.
- **Alert-channel volume** is the main operational risk. Mitigated by the
  cooldown, the sustain requirement, and thresholds set at documented breaking
  points rather than round numbers.
- **`get_db_pool_stats()` touches the DB executor's private attributes**
  (`_work_queue`, `_threads`, `_max_workers`) — the same ones
  `_record_db_queue_depth` and the existing gauge code already read. Every
  access uses `getattr` with a default, so a CPython change that renames them
  degrades the reading rather than raising into the loop.
- **Interaction with `loop_watchdog`:** the new loop is registered in
  `_WATCHDOG_LOOP_NAMES` and calls `record_heartbeat`, so if it dies the
  existing staleness alerting reports it. Threshold 240 s = 4× its interval,
  matching `scheduled_dispatcher`'s convention.
- **Cost:** one extra asyncio task per process (2 per machine), waking once a
  minute to read in-memory state. No DB query, no Redis call, no network I/O
  except an actual alert post.
- **Money, ride state machine, dispatch, auth:** untouched.

**Failure mode if the watchdog itself breaks:** `_tick` is wrapped so any
exception is logged and the loop continues; a webhook failure is logged and
does **not** mark the cooldown (so one flaky post cannot silence a signal for
30 minutes). The worst case is a missing alert, never a crashed process — which
is why `capacity_watchdog` is itself registered with the staleness watchdog.

## 5. User-experience effect

- **Riders / drivers / corporate admins: none.** This is backend observability;
  it changes no endpoint, response, or copy.
- **Visible mid-session?** No.
- **Internal / on-call:** a new class of alert appears in the existing
  `ALERT_WEBHOOK_URL` channel. Each message names the signal, the numbers, the
  replica, and links `docs/runbooks/capacity-scaling.md`, whose §7 playbook maps
  each symptom to the layer that binds and the action to take.
- **No-op when `ALERT_WEBHOOK_URL` is unset** (development, tests): the loop
  still refreshes the gauge but posts nothing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/capacity_watchdog.py` | New: the loop, 3 signals, sustain + cooldown logic, webhook posting | The tripwire that buys time to upgrade a non-autoscaling DB tier |
| `backend/repositories/_base.py` | Added `get_db_pool_stats()`; refreshes the queue-depth gauge on sample | Pool state must be readable without a query having just run |
| `backend/tests/test_capacity_watchdog.py` | New: 17 tests | Alerting logic that is wrong is worse than none — it teaches people to ignore the channel |
| `backend/core/lifespan.py` | `_spawn("capacity_watchdog (60s)", ...)`; added to `_WATCHDOG_LOOP_NAMES` | Starts the loop; makes its own death visible |
| `backend/utils/loop_monitor.py` | `LOOP_THRESHOLDS["capacity_watchdog (60s)"] = 240` | 4× interval, matching `scheduled_dispatcher` |
| `docs/change-log/2026-08-07-capacity-watchdog.md` | This log | CLAUDE.md mandate |

(Split across two commits to respect the ≤3-files-per-commit rule: the loop +
accessor + tests, then the lifespan wiring + this log.)

## 7. Before / after

Purely additive — no existing behavior changes. The one behavior-changing detail
is the cooldown-sentinel fix within the new code:

```python
# Before (first draft) — suppressed every alert for the first 30 min of uptime,
# because time.monotonic() is near zero early in a process's life
if now - _last_alerted.get(signal, 0.0) < COOLDOWN_SECONDS:
    return
```

```python
# After — "never alerted" is an explicit None, not a small number
last_sent = _last_alerted.get(signal)
if last_sent is not None and now - last_sent < COOLDOWN_SECONDS:
    return
```

## 8. Rollback plan

**Preferred, no redeploy:** unset the webhook so the loop goes quiet while
continuing to refresh the gauge —

```bash
fly secrets unset ALERT_WEBHOOK_URL -a spinr-backend-yyz
```

Caveat, stated because it matters: this also silences the **existing**
`loop_watchdog` staleness alerts, which share the variable. For a
watchdog-specific mute without touching that, raise the thresholds instead:

```bash
fly secrets set CAPACITY_QUEUE_DEPTH_THRESHOLD=1000000 \
                CAPACITY_RATE_LIMIT_VIOLATIONS_THRESHOLD=1000000 -a spinr-backend-yyz
```

Both are config reverts, no code deploy. Full removal is a `git revert` of the
two commits; because the change is additive and writes nothing durable, there is
no data-level remediation to perform.

## 9. Verification performed

- [x] **Blast-radius check** — `get_db_pool_stats` is new (no existing callers
      to affect). `_WATCHDOG_LOOP_NAMES` and `LOOP_THRESHOLDS` are additive
      entries. Confirmed `_breaker` is defined (`_base.py:151`) before the new
      accessor references it.
- [x] **Automated tests run** (`backend/.venv`):
      - `tests/test_capacity_watchdog.py` — **17 passed**
      - `tests/test_core_lifespan_coverage.py`, `test_loop_alert.py`,
        `test_p3_loop_jitter_metrics.py` — **31 passed**
      - `pytest -k "loop or lifespan or watchdog or heartbeat"` — **211 passed,
        1 skipped**
      - `tests/test_db_executor.py`, `test_error_handling.py` — **24 passed,
        1 skipped**; `-k "circuit or run_sync or base_repo or db_pool"` —
        **55 passed, 1 skipped**
- [x] **Lint** — `ruff check` clean on all three new/changed Python files.
- [x] **Reviewed against CLAUDE.md conventions** — background-loop replay-safety
      contract (loaded the `spinr-background-loop` skill; loop is write-free and
      the docstring explains why no claim flag applies), observability
      conventions (module-level `logging.getLogger(__name__)`, `extra={...}`
      structured context, `error` for actionable failures / `warning` for
      degraded-but-recovered), and the no-PII rule (alerts carry counts, a
      machine id, and a breaker state — no user ids, coordinates, or contact
      details).
- [x] **Error-handling convention** — the webhook failure path uses
      `logger.error(..., exc_info=True)` and does not mark the cooldown, so a
      failed alert is loud and retried on the next tick rather than swallowed.
- [ ] **Manual repro in staging** — not possible; no staging environment
      (ACTION_ITEMS E1).

## What was NOT verified

- **No alert has been fired against a real webhook.** Posting is verified with a
  fake `httpx.AsyncClient` — payload shape, cooldown, replica id, runbook link,
  and failure handling are covered, but nothing has been observed arriving in
  the actual Slack channel.
- **The thresholds are inherited, not validated.** 50 comes from
  `loadtest/README.md`'s recorded breaking point; 120 violations/min is a
  judgement call. Neither has been calibrated against production traffic, so
  expect to tune them (env vars exist for exactly that).
- **Never observed under real saturation.** The signals are exercised with
  patched stats and synthetic counters; no run has driven the actual DB pool to
  a queue depth above 50.
- **The 8-alerts-per-signal fleet-wide estimate is arithmetic** (one per
  replica), not observed.
- **`loop_alert.py`'s identical cooldown bug is flagged, not fixed, and not
  tested.** It is asserted from reading the code, not from reproducing a
  suppressed alert.
- **Not tested against live Supabase** — the pool-stats tests read the real
  executor object, but no Supabase call is made.

## 10. Sign-off

- [x] Rollback plan is concrete, with the shared-variable caveat stated rather
      than glossed
- [x] Blast radius is stated, not assumed (additive; no existing callers)
- [x] No silent behavior change to an already-shipped flow — this adds a signal,
      changes no user-facing behavior, and the one internal behavior change (the
      cooldown sentinel) is shown before/after
