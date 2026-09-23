# Change Impact & Risk: API Worker Loop Allowlist

Date: 2026-09-23 · Surface: API background tasks · Domain: worker operations · PR 5725

## Issue / gap

The registry could describe a single worker-owned loop, but API startup still
excluded every wave-1 loop whenever the process role was `api`. That left no
runtime way to move only one loop for a canary.

## Root cause

The API lifespan selected tasks and watchdog expectations from the process
role alone; it did not resolve `SPINR_WORKER_LOOP_ALLOWLIST`.

## Fix / remediation

Resolve the shared worker allowlist before database initialization or task
creation, then pass the same selected set to API task selection and watchdog
registration. In `api` role, wave-1 loops absent from the allowlist stay on the
API and remain watchdog-monitored. The unset allowlist retains the previous
behavior of excluding all three wave-1 loops from API role. The `all` role
ignores the allowlist and continues to start all current loops. Empty, unknown,
or duplicate names fail before initialization.

## Risk & impact on existing functionality

This changes only API role selection when an allowlist is explicitly set. API
and worker Machines must receive the identical allowlist during a canary; if
the environment differs, a loop could be duplicated or have no owner. Runtime
configuration matching cannot be proven in local tests, so rollout must verify
the deployed environment on every process group before enabling traffic. Fly
configuration and deployment workflows are unchanged.

## User experience effect

No direct UI or customer copy changes. A future single-loop rollout can leave
the other two wave loops running and monitored on the API while the worker
owns the selected loop.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/lifespan.py` | Resolve allowlist and use it for API spawn/watchdog selection | Apply the shared owner decision at startup |
| `backend/tests/test_lifespan_watchdog_coverage.py` | Exercise selected API loops, watchdog registration, malformed input, and all-role compatibility | Prove actual lifespan behavior and fail-fast validation |
| `docs/change-log/2026-09-23-api-worker-loop-allowlist.md` | Document matching-environment rollout requirement and rollback | Record operational impact |

## Before / after

```python
# Before: api role skips every worker_wave1 loop
should_spawn_on_api(name, process_role)

# After: only names in the validated moved set leave the API
should_spawn_on_api(name, process_role, worker_loop_allowlist)
```

## Rollback plan

Unset the allowlist on every process or revert this API wiring commit. With the
allowlist unset, the API role returns to the existing behavior. Do not leave
API and worker roles with mismatched selectors during a rollback.

## Verification performed

- `/tmp/pr5725-venv/bin/python -m pytest tests/test_lifespan_watchdog_coverage.py::TestProcessRoleLoopOwnership -q --no-cov` — 10 passed.
- Tests run the actual API lifespan for each single-loop selection, compare spawned names and watchdog registrations, reject malformed selectors before DB init, and prove `all` ignores a malformed selector.
- `git diff --check` clean.

## What was NOT verified

No deployed process environment, Fly health check, worker task selection, or
external worker metrics probe was verified here. A canary still requires the
worker to use the same selector, live configuration parity checks, health and
metrics probes, and rollback observation.
