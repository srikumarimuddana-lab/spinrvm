# Change Impact & Risk Log — Migration Runner: Explicit Transaction Block Splitting

**Date:** 2026-09-20
**Author:** Claude Code (session), on behalf of ittalenthire.ca@gmail.com
**Surfaces:** backend (migration runner tooling)
**Domain:** infra / database migrations

## Issue/gap identified
`tests/test_migration_concurrently_splitting.py::test_no_prose_or_body_fragment_leaks_out[426_service_area_scheduled_rides.sql]` fails: `backend/scripts/run_migrations.py`'s `_split_sql_statements()` produces a bare `"BEGIN"` string as its own "statement" when splitting migration 426.

## Root cause
Migration `426_service_area_scheduled_rides.sql` wraps a `SET LOCAL lock_timeout` + two `ALTER TABLE` + a `DO $$...$$` block in an explicit `BEGIN;`/`COMMIT;`, with a `CREATE INDEX CONCURRENTLY` deliberately left outside it (CONCURRENTLY can never run inside a transaction block). Because the file contains CONCURRENTLY, `_apply_one` routes the whole file through `_apply_one_autocommit`, which executes each split statement as its own separate `cur.execute()` call. The splitter had no concept of explicit `BEGIN`/`COMMIT` — it just split on every top-level `;`, so `BEGIN` and `COMMIT` came out as standalone (meaningless) statements.

This was not just a test-hygiene issue: executing `SET LOCAL lock_timeout` as its own standalone autocommit call **silently discards the lock-timeout protection** the migration author intended — `SET LOCAL` only lasts for the current transaction, and each individually-executed autocommit statement is its own implicit transaction. The two `ALTER TABLE` statements that followed would run with no lock-timeout bound at all, contrary to the migration's own intent.

## Fix/remediation
Added `_merge_explicit_transaction_blocks()`, called at the tail of `_split_sql_statements()`. It folds everything between a top-level `BEGIN`/`BEGIN TRANSACTION`/`BEGIN WORK`/`START TRANSACTION` and its matching `COMMIT`/`COMMIT TRANSACTION`/`COMMIT WORK`/`END` into **one** joined statement, dropping the literal transaction-control keywords. Matches only a *whole* statement (case-insensitive), never a substring, so a PL/pgSQL `BEGIN`/`END` inside a dollar-quoted function body (never split out separately by the existing lexer) can't false-match. If no matching close is found, the `BEGIN` is left as-is so the existing keyword-allowlist test still flags a malformed file loudly rather than this function guessing at intent.

Postgres treats a multi-statement string sent via one `cur.execute()` call as one implicit transaction unless the string itself contains explicit `BEGIN`/`COMMIT` — sending the merged interior as one call reproduces the exact atomicity/lock-timeout-scoping the migration author wrote, without ever handing Postgres a bare `BEGIN`/`COMMIT`.

## Risk & impact on existing functionality
- **Blast radius:** grepped all 68 CONCURRENTLY-containing migrations in `backend/migrations/` — migration 426 is the *only* one that also uses explicit `BEGIN;`/`COMMIT;` wrapping. The new merge function is a pure no-op for every other migration (nothing to match, list passes through unchanged).
- Operates purely as a post-process on the already-correctly-split statement list — does not touch the char-by-char lexer's comment/string/dollar-quote state machine or the CONCURRENTLY-detection logic that decides autocommit routing.
- `spinr-migration-reviewer` review in progress (background) — this entry will be updated if it surfaces anything; not merging until that review lands, per this repo's mandatory pre-merge review-agent gate for migration-adjacent changes.

## User experience effect
None — internal tooling only, never reaches a running app surface.

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/scripts/run_migrations.py` | Added `_TXN_OPEN`/`_TXN_CLOSE` keyword sets + `_merge_explicit_transaction_blocks()` | Fix real correctness bug + the failing test |

## Before/after snippet
Before: `_split_sql_statements` returned `["BEGIN", "SET LOCAL lock_timeout = '5s'", "ALTER TABLE ...", "ALTER TABLE ...", "DO $$...$$", "COMMIT", "CREATE INDEX CONCURRENTLY ..."]` — `BEGIN`/`COMMIT` sent to Postgres as their own meaningless statements, and the `SET LOCAL` scoping lost after its own autocommit call returned.

After:
```python
return _merge_explicit_transaction_blocks([s.strip() for s in statements if s.strip()])
```
produces `["SET LOCAL lock_timeout = '5s'; ALTER TABLE ...; ALTER TABLE ...; DO $$...$$", "CREATE INDEX CONCURRENTLY ..."]` — one atomic, lock-timeout-scoped call, then the CONCURRENTLY statement on its own as before.

## Rollback plan
`git revert` — pure code change to a script, no data/schema touched, no live migration in flight depends on this.

## Verification performed
- `pytest tests/test_migration_concurrently_splitting.py tests/test_run_migrations_autocommit_chunks.py tests/test_migration_ordering.py tests/test_run_migrations_batch_failure.py tests/test_run_migrations_skip_list.py tests/test_unbalanced_scoped_migration.py --no-cov -q` — 91/91 pass (independently re-run and confirmed).
- `spinr-migration-reviewer` agent review — in progress at time of this commit; will not merge until it reports back.

## What was NOT verified
- **Not run against a real Postgres instance.** This is unit/lexer-level verification only (mocked `cur.execute` capture, matching how the existing splitting tests already operate) — the actual server-side behavior of sending the merged multi-statement string via one `cur.execute()` call under `autocommit=True` has not been confirmed end-to-end against a live target. Recommend a human verify with `python -m backend.scripts.run_migrations --dry-run` (or against a throwaway/staging Postgres, once one exists per ACTION_ITEMS E1) before the next real application of migration 426, if it hasn't already been applied to production.
- Whether migration 426 has already been applied to production (and with what actual statement-splitting behavior, pre-fix) was not checked — this fix only affects future runs of `run_migrations.py` against environments where it hasn't yet been applied.
