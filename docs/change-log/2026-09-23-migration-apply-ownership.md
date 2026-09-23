# Serialize migration runners before database changes

## Issue / root cause
Concurrent providers could both read a migration as pending and execute it. Per-file provenance and transactions did not establish ownership across the batch.

## Fix / remediation
Apply now takes one database-scoped session advisory lock before tracking bootstrap or classification. Contention or acquisition failure exits nonzero without applying. The connection closes on every exit, releasing ownership even after failure. Session ownership survives per-file commits and CONCURRENTLY autocommit work. Classification's read transaction is explicitly closed before applying.

Alternative: workflow concurrency only. Rejected because independent Fly/Railway/manual runners would not share it. Apply requires a direct PostgreSQL URL; known Supabase poolers, port 6543, pgbouncer options and non-URL connection strings are rejected. A custom proxy cannot be identified from its URL: operators must provide an actual direct connection.

## Risk & impact
The CLI apply path and private `_connect` helper change; status/dry-run remain read-only and do not acquire the lock. Existing skip-list, checksums, file transactions and concurrent-index behavior remain. Apply users of pooler or keyword DSNs must change their connection configuration. No deployment workflow invokes this runner, and no automated migration application is enabled. Only cooperating runners use this lock; it cannot block an operator executing independent SQL.

## User experience
Operator-only: a competing migration run fails promptly and must be retried after the owner finishes. No customer data or financial behavior changes in this task.

## Files modified
| File | Change | Reason |
|---|---|---|
| `backend/scripts/run_migrations.py` | Direct-session validation and advisory ownership | Serialize apply before reads and writes |
| `backend/tests/test_migration_runner_lock.py` | Contention/error/order/connection regressions | Verify fail-closed ownership |
| `docs/change-log/2026-09-23-migration-apply-ownership.md` | Impact record | State operational limits |

## Before / after
Before: connect, bootstrap, classify, apply. After: validate direct connection, connect, try session lock, bootstrap, classify, end read transaction, apply; always close the connection.

## Rollback plan
No migration was applied. Revert this runner change if required, but serialize all apply callers externally until ownership is restored. This does not undo schema or data, and a failed concurrent index still requires its existing partial-apply inspection.

## Verification performed
Eight new tests failed before implementation; all 25 migration audit, ownership, skip-list, autocommit-chunk and batch-failure tests pass. Lock errors expose only exception class, not credentials. Independent review requested before commit.

## Not verified
No real concurrent PostgreSQL runner or container execution locally. Session behavior requires real database CI/staging proof before automated apply activation. Existing live provenance discrepancies must be reconciled first.
