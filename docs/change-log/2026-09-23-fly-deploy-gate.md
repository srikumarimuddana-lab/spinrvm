# Fly production deploy gate

## Issue/gap identified

The production Fly workflow could deploy independently of CI. Its readiness and
served-SHA probes were optional, and manual dispatch could select an arbitrary
ref. A build stamp identified the running commit but did not establish that the
same commit passed the backend and security gates.

## Root cause

The Fly workflow was triggered directly by `push` and `workflow_dispatch`, with
no evidence link to the independent CI and Security Gates workflows. Probe
steps had `if:` conditions that treated absent secrets as successful skips.

## Fix/remediation

Every push to `main` now starts a deploy attempt. The gate waits for successful
`CI/CD Pipeline` and `Security Gates` runs for the exact SHA in this repository,
and checks for executed `backend-test`, Semgrep, pip-audit, and Trivy
container-scan jobs. After those gates pass, it reads current `main` again; a
newer commit blocks the stale run before any Fly secret is staged. The
production probe validator requires the Fly app URL and a nonblank metrics
token. Readiness and served-SHA probes are unconditional. The separate
signed-image workflow remains manual-only and experimental.

## Risk & impact on existing functionality

Blast radius is production deployment automation only. Every main push,
including documentation-only commits, may rebuild/redeploy the same backend
source. This closes a stale-run gap: a docs-only commit must also trigger a new
exact-SHA run when it advances `main` during CI wait. The upstream backend and
selected security jobs run on all non-PR pushes; a skipped or absent job is
denied by the evaluator. The workflow makes no application-code, database,
payment, or worker-topology changes. The normal Fly builder still compiles from
source, so passing SHA evidence is not proof of byte-identical scanned/signed
artifact provenance.

## User-experience effect

There is no rider, driver, or admin UI change. Operators will see failed Fly
workflow runs when CI is red/missing, `main` advances during the wait, or
production probe secrets are absent. A valid latest main push will deploy after
both required workflows and post-deploy checks succeed. There is no manual
bypass on this workflow.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/deploy-fly.yml` | Push-only main trigger, read-only Actions access, exact-SHA gate, required probe preflight and unconditional readiness/SHA checks | Prevent untested, stale, or unverified production promotion |
| `scripts/fly_deploy_gate.py` | Evaluates workflow/job evidence, polls GitHub, rechecks main, and validates production probe config | Keep fail-closed policy directly testable |
| `scripts/test_deploy_gate.py` | Exercises evaluator, polling, probe validation, and workflow wiring | Catch missing/skipped/failing or mismatched evidence before merge |
| `docs/change-log/2026-09-23-fly-deploy-gate.md` | This impact record and operator notes | Record changed behavior and verification boundary |

## Before/after

```yaml
# Before: incomplete or skipped probe configuration could still yield green.
- name: Verify the deployed build SHA is serving
  if: ${{ env.FLY_HEALTH_URL != '' && env.METRICS_AUTH_TOKEN != '' }}
```

```yaml
# After: configuration is checked, and the serving-SHA probe always runs.
- name: Verify production probe configuration
  run: python3 scripts/fly_deploy_gate.py --check-probes
- name: Verify the deployed build SHA is serving
```

## Rollback plan

If valid commits are blocked, inspect the Actions log and exact workflow/job
evidence, then repair CI, token permissions, or missing probe secrets and push a
new commit. Stale commits intentionally cannot be replayed by this workflow.
If the workflow code itself must be reverted, restore the prior workflow and
redeploy the recorded immutable image/config, then verify readiness and served
SHA. No live data is mutated by this change, and it does not add automatic
rollback.

## Verification performed

`python -m unittest discover -s scripts -p 'test_deploy_gate.py'` covers exact
SHA/repository/main/push selection, required job execution, pending and failed
runs, skipped/missing jobs, stale main, missing/invalid production probes, and
workflow wiring. PyYAML parses the workflow and `git diff --check` is clean.
No production deployment, live probe, or deliberately failing PR run was
performed.

## What was NOT verified

No live GitHub Actions run was used to confirm API payload behavior or the
workflow/job display names against the repository’s real run data. The
`FLY_HEALTH_URL`, `METRICS_AUTH_TOKEN`, `FLY_API_TOKEN`, and `SENTRY_DSN`
secrets were not inspected. No Fly deployment, staging probe, forced failure,
or rollback drill was performed. CI success for a SHA does not establish that
Fly's remote-built image is byte-identical to the separately scanned or signed
CI artifact.

## Operator runbook

Configure `FLY_API_TOKEN` scoped to `spinr-backend-yyz`, `SENTRY_DSN`,
`FLY_HEALTH_URL` exactly `https://spinr-backend-yyz.fly.dev`, and
`METRICS_AUTH_TOKEN` for `/deploy-info`. Missing runs poll for up to 50 minutes;
missing or skipped required jobs, workflow failure, mismatched repo/SHA/branch,
or a changed main tip denies deployment. After the rolling deploy and mixed-pool
scale step, `/ready` must return HTTP 200 and `/deploy-info` must report
`GITHUB_SHA`.
