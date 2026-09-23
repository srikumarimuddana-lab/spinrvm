# Fly staging recovery impact

## Issue/gap identified
The staging Fly workflow had no required served-build SHA check and no restore path after a candidate deploy or readiness failure.

## Root cause
Staging was scaffolded with an optional `/health` probe and no immutable release baseline.

## Fix/remediation
Require staging-only probe secrets, stamp the candidate SHA, require `/ready` plus served-SHA proof, and record a digest-pinned baseline with the checked-in config at the currently served SHA before Fly mutations. Keep recovery metadata free of machine JSON and secrets.

## Risk & impact on existing functionality
Blast radius is only `.github/workflows/deploy-backend-staging.yml` for `spinr-backend-staging`. Existing staging Supabase secrets are still staged before candidate deploy and are not snapshotted; do not rotate them as part of a recovery drill. No production app, database, payment, or ride state is touched. Empty app bootstrap has no old image to restore.

## User-experience effect
No rider, driver, or admin UX change. Staging deploys now fail closed when probe secrets are absent, and candidate promotion requires readiness and exact-SHA checks. A failed candidate triggers a digest-pinned restore with the previously served config, then readiness and prior-SHA verification. The candidate run remains failed after restoration.

## Files modified
| File | Change | Reason |
|---|---|---|
| `.github/workflows/deploy-backend-staging.yml` | Snapshot, required probes, candidate SHA check, guarded restore and forced-failure input | Exercise recoverable staging promotion |
| `scripts/test_fly_staging_recovery.py` | Snapshot and workflow wiring assertions | Verify baseline gate and restoration wiring |
| `docs/runbooks/staging-environment.md` | Required probe secrets, bootstrap and recovery drill steps | Guide safe staging setup and acceptance |
| `docs/change-log/2026-09-23-staging-recovery.md` | Impact record | Record scope and verification boundary |

## Before/after
```yaml
# Before: post-deploy health was optional and only checked /health.
if: ${{ env.FLY_HEALTH_URL_STAGING != '' }}
```
```yaml
# After: failed staging candidate restores the digest-pinned prior image/config.
- name: Restore verified staging baseline after candidate failure
  if: ${{ failure() && steps.snapshot.outputs.has_baseline == 'true' }}
```

## Rollback plan
Revert this staging-only workflow change if it blocks staging promotion. No production deploy or live data rollback is involved. If automatic restore itself fails, retain the failed run, inspect its sanitized error summary and artifact, and restore only the recorded staging digest/config after human confirmation; never use this path against production.

## Verification performed
`python3 -m unittest discover -s scripts -p test_fly_staging_recovery.py` — 8 passed. PyYAML parsed the workflow; `bash -n` checked each workflow shell step; `git diff --check` passed.

## What was NOT verified
No staging Fly app, Supabase project, or secrets were available or accessed. No live Machine list/deploy-info fixture, candidate deploy, forced recovery drill, image restore, or readiness/SHA probe was run. Machine `image_ref` parsing follows the official Fly Machines API shape; CLI response parity remains to be confirmed by the operator on staging. The workflow does not snapshot Fly runtime secrets.
