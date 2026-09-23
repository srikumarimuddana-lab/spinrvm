# Change Impact & Risk: Worker Canary Runbook

Date: 2026-09-23 · Surface: operations documentation · Domain: worker rollout · PR 5725

## Issue / gap

The outbox runbook described a full worker rollout and Fly scaling commands as
though the production process group were already configured. It did not
document a single-loop canary or require verification of matching API/worker
ownership settings and external probes.

## Root cause

Deployment topology and process-role implementation evolved separately from
the runbook. The earlier operational text overstated the current Fly
configuration and omitted environment parity as a release gate.

## Fix / remediation

Clarify that worker activation is a future, separately approved rollout. Add
the single-loop role/selector mapping, candidate inventory preflight, exact
environment parity requirement, private health and authenticated metrics
checks, API watchdog verification, replay-window observation, and a rollback
that confirms API role `all` before scaling down the worker. Include the
executable inventory pipeline. Correct Fly notes to state that current
production config and workflows do not activate a worker.

## Risk & impact on existing functionality

Documentation only. No Fly config, workflow, Machine, secret, or process was
changed. The runbook intentionally makes no claim that the external checks
have passed. Do not enable the producer or change topology until a separate
deployment change is reviewed and the listed checks are performed.

## User experience effect

No customer-facing effect. Operators receive accurate future rollout and
rollback instructions.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/runbooks/transactional-outbox.md` | Document candidate canary, ownership parity, external gates, and current inactive topology | Prevent an unsafe rollout based on stale process configuration instructions |
| `docs/change-log/2026-09-23-worker-canary-runbook.md` | Record the documentation change and its limits | Required change-impact record |

## Before / after

```text
# Before: scaling commands implied a worker process group was active
# After: check inventory, process-role parity, private /health, and authenticated /metrics first;
#        do not scale until a separate deployment change declares the worker group
```

## Rollback plan

Restore the previous documentation text if needed. No service rollback is
required because no deployment or configuration changed.

## Verification performed

- Manually checked the runbook against current `fly.toml`, deploy workflow, worker health/metrics routes, and `--workers 1` preflight profile.
- `git diff --check` clean.

## What was NOT verified

No Fly API, remote environment, worker Machine, private probe, metrics scrape,
database, or Redis was accessed. All listed external checks remain deployment
gates.
