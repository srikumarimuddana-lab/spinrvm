# Change Impact & Risk: Dedicated Worker Startup Guard

Date: 2026-09-23 · Surface: backend · Domain: worker operations · PR 5725

## Issue / gap

`worker.py` started background loops without the API's shared production
configuration guard, a production `SPINR_PROCESS_ROLE=worker` check, or a
required metrics bearer token.

## Root cause

The dedicated app shares settings and database initialization with the API but
has a separate FastAPI lifespan that never ran middleware's production guard
or validated worker-specific startup requirements.

## Fix / remediation

Before Sentry, Firebase, database initialization, or task creation, resolve and
validate the process role, call the shared `_validate_production_config()`,
and require `METRICS_AUTH_TOKEN` in production. Development and test starts
keep their current defaults. Tests also prove task supervisors retry following
an exception and an unexpected normal return.

## Risk & impact on existing functionality

Blast radius is isolated to future `worker:app` startup. API startup still
calls the same shared guard from `init_middleware`; its process-role behavior
is unchanged. A future production worker with invalid shared config, a role
other than `worker`, or no metrics token fails before starting tasks. No loop
is moved, and no database, Redis, ride-state, or money write path changes.
Production Fly topology is unchanged, so this guard has no live effect today.

## User experience effect

No rider, driver, or admin UI or mid-session change. This affects only whether
a future misconfigured worker starts; no customer copy or notifications
change.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/worker.py` | Check production role, shared config, and metrics auth before service initialization | Fail closed before worker side effects |
| `backend/tests/test_worker_app.py` | Cover role/config/token ordering and exception/return recovery | Verify guard order and task recovery |
| `docs/change-log/2026-09-23-worker-startup-guard.md` | Record impact and boundaries | Required change-impact record |

## Before / after

```python
# Before
async def lifespan(app):
    init_backend_sentry(process_name="spinr worker")
    init_firebase()
    await init_database()
    start_worker_tasks()

# After
async def lifespan(app):
    process_role = resolve_process_role(os.environ.get("SPINR_PROCESS_ROLE"), env=settings.ENV)
    if settings.ENV.lower() == "production" and process_role != "worker":
        raise RuntimeError(...)
    _validate_production_config()
    if settings.ENV.lower() == "production" and not _metrics_token():
        raise RuntimeError(...)
    init_backend_sentry(process_name="spinr worker")
    init_firebase()
    await init_database()
    start_worker_tasks()
```

## Rollback plan

The worker is not in Fly process configuration, so there is no current live
effect. If deployed later and blocked by the guard, revert this worker code and
redeploy; leave API role `all` so current loops continue. Do not change the
outbox producer flag as a rollback step.

## Verification performed

- `/tmp/pr5725-venv/bin/python -m pytest tests/test_worker_app.py -q --no-cov` — 16 passed.
- `git diff --check` clean; inspected worker startup, shared validator, and API role gate.
- Backend-only change; no frontend production build. No feature flag applies; topology is unchanged.

## What was NOT verified

No live Fly worker, Redis, or PostgreSQL was started. Local tests prove startup
ordering and task retry only; they do not prove Fly restart/health behavior,
private metrics scraping, DB lease replay, or production secret presence.
