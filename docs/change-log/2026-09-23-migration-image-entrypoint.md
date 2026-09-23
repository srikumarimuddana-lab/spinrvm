# Migration image entrypoint proof

## Issue and root cause
The repository module path differs from the flat `/app` layout in the backend image. Source tests alone cannot establish that a future release command imports its driver and shipped SQL.

## Fix and before/after
Before: the container gate built and scanned the image only. After: it also imports psycopg and `scripts.run_migrations`, discovers SQL, checks the tracking-table migration and executes CLI help inside that exact image with networking disabled.

## Risk and blast radius
Only CI is changed; no release command, migration apply or database connection is enabled. The gate fails if the image lacks the entrypoint or its required driver/files.

## UX impact
No rider, driver or admin interface changes. Broken migration packaging fails the build earlier.

## Files
| File | Change |
|---|---|
| `.github/workflows/security-gates.yml` | Isolated migration image smoke step |
| `docs/change-log/2026-09-23-migration-image-entrypoint.md` | Evidence and limits |

## Verification
Source-layout import/discovery and CLI help checked locally; workflow YAML parsed. The actual Docker execution requires the CI runner because Docker is unavailable here.

## Compatibility and not verified
This is packaging proof, not schema compatibility proof. Before enabling release migration automation, resolve provenance gaps, run pending migrations on disposable PostgreSQL, test both previous and candidate application versions against the resulting schema, and prove concurrent apply exclusion on a stable direct session. Destructive schema changes need a separate expand/contract rollout. Production automation remains disabled.

## Rollback
Revert the image smoke step if its packaging contract changes intentionally; keep release migration automation disabled until replacement proof passes.
