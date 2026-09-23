# Change Impact & Risk: Shared Worker Loop Allowlist

Date: 2026-09-23 · Surface: backend loop ownership · Domain: worker operations · PR 5725

## Issue / gap

Worker wave 1 could only move as a three-loop group. There was no shared parser
and ownership rule for an explicit one-loop canary.

## Root cause

The registry classified loops by broad placement (`worker_wave1`), while API
and worker process selection had no allowlist argument.

## Fix / remediation

Add a registry resolver for the optional
`SPINR_WORKER_LOOP_ALLOWLIST` value. An unset value resolves to the current full
wave. Explicit values must contain one or more unique, known worker wave names.
Registry ownership helpers accept the same resolved names, so API and worker
selection can share one source of truth. The `all` role continues to spawn all
loops regardless of an allowlist. Startup wiring is introduced in subsequent
separate commits.

## Risk & impact on existing functionality

This commit adds selection helpers and tests only; no runtime caller reads the
new environment variable yet. Existing startup behavior is unchanged. Unknown,
duplicate, empty, or blank entries fail closed when resolved. The owner
calculation tests prove exactly one role owns each wave loop under a single
selected canary.

## User experience effect

No rider or operator-facing runtime behavior changes in this helper-only
commit. The selector is groundwork for a future, explicitly coordinated
single-loop canary.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/background_loop_registry.py` | Add explicit allowlist resolution and parameterize API/worker owner projections | Provide a shared, validated owner set |
| `backend/tests/test_background_loop_registry.py` | Test defaults, validation, and one-owner selection | Prevent split ownership or silent zero-owner selections |
| `docs/change-log/2026-09-23-worker-loop-allowlist-registry.md` | Record scope, constraints, and tests | Required change-impact record |

## Before / after

```python
# Before
worker_names = all_worker_wave1_names

# After: an explicit canary may select a subset
worker_names = resolve_worker_loop_allowlist(raw_value)
api_runs = should_spawn_on_api(name, "api", worker_names)
worker_runs = name in worker_loop_names(worker_names)
```

## Rollback plan

Remove the resolver and parameterized projections. This commit does not wire
startup behavior, so no process configuration needs to change for rollback.

## Verification performed

- `/tmp/pr5725-venv/bin/python -m pytest tests/test_background_loop_registry.py -q --no-cov` — 7 passed.
- `git diff --check` clean.
- Tests prove unset means all three wave loops, selected ownership sums to one per name, `all` remains all-loops, and malformed selectors raise.

## What was NOT verified

This commit does not prove API/worker lifespan wiring, matching runtime
environment across Fly Machines, worker health or metrics, database/Redis
readiness, or live canary behavior. Those remain separate gates.
