# Migration audit commands must be read-only

## Issue / root cause
`run_migrations --status` and `--dry-run` bootstrapped a missing tracking table and committed a provenance row before checking their read-only flags. A release audit could therefore change the database while reporting no changes.

## Fix / remediation
The tracking-table helper now accepts `allow_create`; audit modes disable creation and treat a missing table as zero recorded migrations. Apply mode retains bootstrap behavior. Alternative: use only the separate drift-audit script; rejected because it leaves the documented runner commands unsafe.

## Risk & impact
Scope: `backend/scripts/run_migrations.py` CLI and its private tracking helper. The drift auditor imports only discovery/checksum helpers; restore/verifier scripts are unchanged. No deployment workflow currently invokes this runner. A missing tracking table now reports every eligible migration pending; it does not claim those migrations are absent in the live schema.

## User experience
Operator-only change. Audit commands no longer write schema or provenance. No rider, driver, payment, or insurance behavior changes.

## Files modified
| File | Change | Reason |
|---|---|---|
| `backend/scripts/run_migrations.py` | Optional tracking bootstrap | Keep audit commands read-only |
| `backend/tests/test_migration_runner_read_only.py` | Existing/missing tracking cases and apply control | Verify no DDL, INSERT or commit in audit modes |
| `docs/change-log/2026-09-23-migration-audit-read-only.md` | Impact record | Document scope and evidence |

## Before / after
Before: always bootstrap, then inspect `--status` / `--dry-run`. After: bootstrap only in apply mode; missing read-only tracking yields an empty applied map.

## Rollback plan
Revert the code if needed; no data was changed by this task. Until fixed, use the separate read-only drift auditor instead of the runner's audit modes on uninitialized databases.

## Verification performed
The new audit tests reproduced two failures before the fix. The five new cases plus existing skip-list, batch-failure and autocommit-chunk tests pass (17 tests total). Independent review requested before commit.

## Not verified
No real PostgreSQL service is available locally. Tests inspect emitted SQL and transaction calls through a connection double; no production migration was run.
