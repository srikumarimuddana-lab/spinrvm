# Change Impact & Risk: Worker Loop Allowlist Runtime

Date: 2026-09-23 · Surface: worker background tasks · Domain: worker operations · PR 5725

## Issue / gap

API startup could now keep unselected worker-wave loops and monitor them, but
the worker app still started the full three-loop wave and monitored all loop
names. A single-loop canary would therefore duplicate selected work and expose
incorrect health expectations.

## Root cause

`worker.py` always iterated the full static `WORKER_LOOP_NAMES` tuple and used
that tuple for task health and heartbeat registration.

## Fix / remediation

Resolve the same validated allowlist before config checks, integrations, DB
initialization, or task creation. The worker always starts its outbox poller
and starts only selected wave-1 loops. `/health`, task gauges, and stale-loop
checks use the actual task set. An unset selector retains the full wave for a
worker role; `all` ignores a selector and retains prior behavior.

## Risk & impact on existing functionality

An explicit selector can reduce the worker's wave-1 tasks while leaving the
outbox poller running. API and worker process groups must receive the identical
selector to keep one owner per moved loop. Tests prove local task and watchdog
selection, but do not prove deployed environment parity. Do not activate the
worker topology until an external process-environment check and private health
and authenticated metrics probes pass.

## User experience effect

No rider, driver, or admin UI changes. Operators gain a worker health view
limited to tasks this worker actually starts.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/worker.py` | Start only selected worker loops and register health against actual tasks | Keep worker ownership and monitoring aligned |
| `backend/tests/test_worker_app.py` | Exercise each one-loop worker selection, health registration, and fail-fast malformed selectors | Verify runtime task and watchdog behavior |
| `docs/change-log/2026-09-23-worker-loop-allowlist-runtime.md` | Record scope and external proof gate | Required change-impact record |

## Before / after

```python
# Before
for name in WORKER_LOOP_NAMES:
    start(factory_for[name])
get_loop_status(registered_names=WORKER_LOOP_NAMES)

# After
active_names = [OUTBOX_LOOP_NAME, *selected_worker_loop_names]
for name in active_names:
    start(factory_for[name])
get_loop_status(registered_names=active_names)
```

## Rollback plan

Unset `SPINR_WORKER_LOOP_ALLOWLIST` on API and worker processes to restore the
full worker wave, or revert this worker wiring. Coordinate both process groups
before changing the selector so ownership is never split.

## Verification performed

- `/tmp/pr5725-venv/bin/python -m pytest tests/test_worker_app.py -q --no-cov` — 23 passed.
- Tests verify each selected task set includes the outbox poller and one wave loop, health reports only actual tasks, heartbeat checks use the same task names, malformed input fails before startup side effects, and `all` ignores the selector.
- `git diff --check` clean.

## What was NOT verified

No production Fly inventory, environment parity, worker health endpoint, private
metrics scrape, PostgreSQL, Redis, or replay test was verified by this slice.
Fleet configuration remains inactive and external proof is required before
canary activation.
