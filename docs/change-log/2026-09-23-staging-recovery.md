# Fly staging recovery impact

## Issue/gap identified
The staging Fly workflow had no required served-build SHA check and no restore path after a candidate deploy or readiness failure.

## Root cause
Staging was scaffolded with an optional `/health` probe and no immutable release baseline.

## Fix/remediation
Require staging-only probe secrets, stamp the candidate SHA, require `/ready` plus served-SHA proof, and record a digest-pinned baseline with the checked-in config at the currently served SHA before Fly mutations. Keep recovery metadata free of machine JSON and secrets.

## Risk & impact on existing functionality
Blast radius is only `.github/workflows/deploy-backend-staging.yml` for `spinr-backend-staging`. Existing staging Supabase secrets are still staged before candidate deploy and are not snapshotted; do not rotate them as part of a recovery drill. No production app, database, payment, or ride state is touched. Empty app bootstrap has no old image to restore. This slice records recovery evidence; the restore handler is a separate follow-up commit.

## User-experience effect
No rider, driver, or admin UX change. Staging deploys now fail closed when probe secrets are absent, and candidate promotion requires readiness and exact-SHA checks. A failed candidate leaves the workflow failed; the restore handler is a separate follow-up commit.

## Files modified
| File | Change | Reason |
|---|---|---|
| `.github/workflows/deploy-backend-staging.yml` | Snapshot, required probes, candidate SHA check | Gate staging promotion and capture recovery evidence |
| `scripts/test_fly_staging_recovery.py` | Snapshot and workflow wiring assertions | Verify ordering and fail-closed baseline capture |
| `docs/change-log/2026-09-23-staging-recovery.md` | Impact record | Record scope and verification boundary |

## Before/after
```yaml
# Before: post-deploy health was optional and only checked /health.
if: ${{ env.FLY_HEALTH_URL_STAGING != '' }}
```
```yaml
# After: exact production-independent staging URL, /ready, and served SHA are required.
- name: Verify staging build SHA is serving
```

## Rollback plan
Revert this staging-only workflow slice if it blocks staging promotion. No production deploy or live data rollback is involved. This snapshot commit does not automatically restore a failed candidate; use operator judgment and the captured digest/config until the restore-handler follow-up is merged.

## Verification performed
`python3 -m unittest discover -s scripts -p test_fly_staging_recovery.py` — 7 passed. PyYAML parsed the workflow; `bash -n` checked each workflow shell step; `git diff --check` passed.

## What was NOT verified
No staging Fly app, Supabase project, or secrets were available or accessed. No live Machine list/deploy-info fixture, candidate deploy, forced recovery drill, image restore, or readiness/SHA probe was run. Machine `image_ref` parsing follows the official Fly Machines API shape; CLI response parity remains to be confirmed by the operator on staging. The workflow does not snapshot Fly runtime secrets.
