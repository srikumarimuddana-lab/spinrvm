# Change Impact & Risk: Inactive Worker Fly Candidate

Date: 2026-09-23 · Surface: deployment configuration · Domain: worker operations · PR 5725

## Issue / gap

There was no reviewable Fly configuration for a one-loop worker canary with
matching API and worker process roles, private health checks, and an eight
Machine candidate budget.

## Root cause

Production `backend/fly.toml` and deploy workflows intentionally still define
only the API `app` and `burst` groups. The new worker implementation had no
inactive candidate configuration to validate separately.

## Fix / remediation

Add `backend/fly.worker-canary.toml` as an explicitly inactive candidate. It
retains app and burst services, adds a private worker process without a public
service, sets the same one-loop selector in API and worker command wrappers,
and defines a process-scoped worker `/health` check. API wrappers derive the
runtime role from `SPINR_API_ROLE`, allowing a worker-first stage with API role
`all` before changing it to `api`; the worker binds IPv6 for Fly's private
health/metrics path. Comments document the candidate
`app=2, burst=5, worker=1` budget; TOML itself does not enforce counts.
The runbook requires inventory preflight, `fly config validate --strict`,
process environment parity, private health, and authenticated private metrics
verification before any separate activation.

## Risk & impact on existing functionality

The active `backend/fly.toml`, deployment workflows, existing Machines, and
secrets are unchanged. The candidate is not provider-validated in this
environment and must not be used to deploy until an operator runs Fly's
configuration validator and reviews its process/service/check behavior. A
future deployment would change the complete process map and requires its own
approved staged rollout and scaling sequence; deploying it does not guarantee
worker-first start order. The candidate does not contain a
metrics secret; authenticated metrics require an external private scraper.

## User experience effect

No production or customer-visible behavior changes. Operators get a concrete
configuration artifact for review without activating it.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/fly.worker-canary.toml` | Add inactive app/burst/worker profile, staged API role variable, matched selectors, private IPv6 worker health check, and candidate VM sizing | Make process and service topology reviewable without altering active config |
| `docs/runbooks/transactional-outbox.md` | Require worker-first staged config, candidate validation, loop proof, and external gates | Prevent accidental deployment before provider and runtime proof |
| `docs/change-log/2026-09-23-inactive-worker-fly-candidate.md` | Record candidate scope and lack of provider validation | Required change-impact record |

## Before / after

```text
# Before: production config has only app and burst process groups
# After: a separate inactive candidate also describes a one-loop worker group
#        and leaves deployment workflows and active config unchanged
```

## Rollback plan

Delete the candidate file and revert its runbook reference. No running Machine
or active config change requires rollback.

## Verification performed

- Python `tomllib` parsed the candidate and confirmed process groups `app`, `burst`, `worker` plus a worker-scoped HTTP check.
- `sh -n` accepted the embedded API and worker startup wrappers with the API role set to `all` and `api`.
- `git diff --check` clean.
- Confirmed active `backend/fly.toml` and workflows still define/scale only `app=2, burst=6`.

## What was NOT verified

`flyctl` is unavailable here, so `fly config validate -c
fly.worker-canary.toml --strict` was not run. No Fly app, Machine, process
environment, private endpoint, metrics credential, or live inventory was
accessed. The candidate is review material only until provider validation and
external canary checks pass.
