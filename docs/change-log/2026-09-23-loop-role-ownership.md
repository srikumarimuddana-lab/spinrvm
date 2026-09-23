# Change Impact & Risk: Background Loop Role Ownership

## Issue/gap identified

The process-role catalog initially omitted two loops that `core/lifespan.py`
already started and registered with the watchdog. The lifespan also ignored
the process role, so the API could not safely exclude worker-wave loops and
the watchdog could not match the loops selected for that role.

## Root cause

The ownership inventory drifted from lifespan's `_spawn()` calls, leaving
`route_deviation_alerter (30s)` and `insurance_period_reconciler (10min)`
without explicit placement. Although registry helpers defined role selection,
lifespan did not call them and still used a fixed watchdog list.

## Fix/remediation

Classified both existing loops as API-owned and added a regression guard that
requires every spawned loop to be in the catalog. Lifespan now resolves the
role from `SPINR_PROCESS_ROLE` (default `all`), starts only loops owned by the
API role, and gives the watchdog the selected spawned loops. Worker role starts
no API loops or API watchdog. `ENV=test` continues to record selections while
skipping loop task creation. The H3 reconciler remains catalogued as deferred
and dormant.

## Risk & impact on existing functionality

Blast radius is limited to the startup loop registry and `core/lifespan.py`.
The lifespan is the sole production consumer of role placement, deciding both
task creation and watchdog registration; the existing worker app owns a
separate `backend/worker.py` path. Tests cover registry drift and actual
lifespan selection. Default `all` keeps current loop ownership, including
deferred loops. `api` omits only `worker_wave1` loops. An accidental
`worker`-role API process starts no API loops or loop watchdog. No database,
network, or deployment configuration is modified; enabling a separately
deployed worker remains an operational gate.

## User experience effect

No rider, driver, or admin UI change. Task ownership changes only at process
startup and have no mid-session user-visible effect. A correctly configured
API retains the same loops; setting API role removes push retry, Zoho sync, and
driver onboarding reminders from that process.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/background_loop_registry.py` | Added API placements for route-deviation and insurance-period loops | Match the existing lifespan spawns to process-role ownership |
| `backend/core/lifespan.py` | Resolve process role; filter loop creation and watchdog registrations; keep test environment spawn skip | Make the registry control runtime ownership and prevent false watchdog alerts |
| `backend/tests/test_lifespan_watchdog_coverage.py` | Guard registry drift; execute lifespan under all/api/worker and ENV=test | Verify task creation and watchdog names follow runtime role selection |
| `docs/change-log/2026-09-23-loop-role-ownership.md` | Recorded catalog and runtime impact | Required change-impact record |

## Before/after snippet

```python
# Before
def _spawn(name, coro_factory):
    task = asyncio.create_task(_restartable(name, coro_factory), name=name)
    background_tasks.append(task)

# After
def _spawn(name, coro_factory):
    if not should_spawn_on_api(name, process_role):
        return
    task = asyncio.create_task(_restartable(name, coro_factory), name=name)
    background_tasks.append(task)
```

## Rollback plan

Revert the lifespan role-wiring commit to restore unconditional API loop
spawning, and revert the preceding catalog correction if necessary. This
changes no live data or deployment configuration, so no data repair is
required.

## Verification performed

- The classification regression failed before the catalog correction on the
  two missing names; the runtime behavior test failed before role wiring.
- `/tmp/pr5725-venv/bin/python -m pytest tests/test_lifespan_watchdog_coverage.py -q --no-cov` — 12 passed.
- No production build is applicable to this backend-only change.

## What was NOT verified

No staging or production process was started. Tests use mocked startup
dependencies and intercepted asyncio task creation, not live database/Redis
services. Deployment configuration and the operational gate for starting a
dedicated worker were not changed or verified.
