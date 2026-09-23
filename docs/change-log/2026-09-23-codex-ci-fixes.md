# PR 5725 Codex CI findings

## Issue and root cause

The staging deploy workflow passed its metrics token only to probes, leaving a
fresh Fly runtime unable to authenticate `/deploy-info`. Separately, manual
guardrail runs supplied `main` to Git even though checkout may only create
`refs/remotes/origin/main`.

## Fix and before / after

```text
Before: stage Supabase secrets; probe with a GitHub-only metrics token.
After: also stage METRICS_AUTH_TOKEN from METRICS_AUTH_TOKEN_STAGING.
Before: git merge-base main HEAD.
After: resolve main or refs/remotes/origin/main to a commit, then merge-base.
```

## Risk & impact

The secret change affects staging Fly deployment and authenticated readiness/SHA
verification only. Existing baseline capture still precedes secret staging;
secret rotation during recovery remains unsupported. The ref helper is consumed
by `changed_paths` in the Change Impact Log guard and its tests. PR and merge-queue
base commit SHAs remain authoritative; an unresolvable ref fails closed. No
production application code, database records, or financial operations change.

## User experience

Developers can bootstrap staging with authenticated build verification and run
manual guardrails without a local main branch. No rider or driver session changes.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/deploy-backend-staging.yml` | Stage metrics runtime secret | Allow authenticated build verification |
| `scripts/test_fly_staging_recovery.py` | Assert secret mapping before deploy | Prevent recurrence |
| `docs/runbooks/staging-environment.md` | Describe staged credentials | Explain recovery limits |
| `.github/workflows/ci-guardrails.yml` | Explain manual base resolution | Preserve SHA precedence |
| `scripts/check_change_impact.py` | Resolve remote tracking refs | Support checkout without local main |
| `scripts/test_check_change_impact.py` | Real temporary Git repository regression | Exercise missing local branch |

## Rollback plan

Revert the workflow/helper commits before another workflow run if necessary. No
live action was executed. If a later staging deployment changes a token, restore
the prior staging token through Fly secrets and the matching GitHub secret before
probing again; secrets are not included in image recovery. No live data rollback
is required for the Git ref helper.

## Verification performed

Focused pytest run of both test files: 14 tests and 14 subtests passed. Root
reviewed both concrete diffs. The branch regression uses a real temporary Git
repository, not a mocked merge-base command.

## What was not verified

No live Fly deployment, secret update, GitHub Actions execution, or production
build was performed. The workflow secret mapping is checked statically.
