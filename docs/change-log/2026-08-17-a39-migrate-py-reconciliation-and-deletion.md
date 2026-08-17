# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | ACTION_ITEMS.md A39 (deferred decision), B0 |

## 1. Issue / gap identified

`ACTION_ITEMS.md` A39 (closed 2026-08-17 earlier the same day) found `backend/scripts/` contained two independent migration runners — `migrate.py` and `run_migrations.py` — with two different `schema_migrations` table shapes, and confirmed live that only `run_migrations.py`'s shape matches what's actually deployed to production. That fix corrected every documented command to point at `run_migrations.py` but deliberately left `migrate.py` itself untouched, flagging the reconcile-vs-delete decision as one only a human should make.

Investigating that decision surfaced a second, more consequential gap: `run_migrations.py` (the canonical runner) has **no handling at all** for `CREATE`/`DROP INDEX CONCURRENTLY` — it wraps every migration in a single transaction, which Postgres rejects for `CONCURRENTLY` statements. `migrate.py` already had a real, tested fix for exactly this (`ACTION_ITEMS.md` B0, `_split_sql_statements` + autocommit routing) — it was just built against the wrong runner.

## 2. Root cause

`migrate.py` and `run_migrations.py` evolved independently: `migrate.py` predates migration 24's `schema_migrations` redesign and was never updated to match it, while `run_migrations.py` (built to match migration 24) never received the B0 CONCURRENTLY fix that was applied to `migrate.py` around the same time. Neither runner was ever the single source of truth for both problems at once.

## 3. Fix / remediation

Asked the product owner directly (via `AskUserQuestion`) how to resolve this, now armed with the sharper finding that `run_migrations.py` has a real functional gap `migrate.py` had already solved. Decision: **reconcile, then delete** — port `migrate.py`'s tested logic into `run_migrations.py`, then remove `migrate.py` so there is exactly one correct runner.

Executed:
- Ported `_split_sql_statements` (the lexical SQL-statement scanner — comment/string/dollar-quote aware) verbatim into `run_migrations.py`.
- Added `_apply_one_autocommit`, adapted from `migrate.py`'s `_apply_migration_autocommit` for `run_migrations.py`'s `(filename, checksum)` schema instead of `migrate.py`'s `(version)`-only one.
- `_apply_one` now detects `CONCURRENTLY` from the comment-free split statements (never raw text, so a `CONCURRENTLY` mention only inside a rollback comment doesn't misroute a file with no actual concurrent index) and routes to the autocommit path; every other migration is unaffected, unchanged behavior.
- Moved and adapted both B0 regression test files: `test_migration_concurrently_splitting.py` now imports from `run_migrations`, and `test_migrate_autocommit_chunks.py` was replaced by `test_run_migrations_autocommit_chunks.py` (renamed, and its assertions updated for the `(filename, checksum)` INSERT shape plus a new dedicated test pinning that exact shape).
- Fixed every other living reference to `migrate.py`: `CLAUDE.md` (two separate blocks — the "Database Migrations" note and a stale "Database & Migration Conventions" line that still said `migrate.py` even after the earlier A39 fix), `AGENTS.md` (same two-block pattern), `backend/migrations/CLAUDE.md`, `docs/dev-setup.md`, `docs/runbooks/migration-conflict-detection.md`, `.github/workflows/migration-check.yml`'s explanatory comments, `.claude/commands/migration-check.md` (a live slash-command definition), and a **real runtime admin-facing error message** in `backend/routes/admin/auto_payouts.py` that told an operator to run a script that no longer exists.
- Deleted `backend/scripts/migrate.py`.
- **Deliberately left untouched:** every `docs/audit/*`, `docs/change-log/*`, `docs/reviews/*`, and `.planning/*` reference to `migrate.py` — these are point-in-time historical records, not living documentation, and retroactively editing them would falsify what was actually true when they were written. Same convention this session already followed for superseded audit findings.

## 4. Risk & impact on existing functionality

- **Blast radius: the migration tooling itself, not application code.** `run_migrations.py` is invoked manually/via CI runbooks, not imported by any backend application module — grepped, confirmed no route/service/background loop imports either `run_migrations` or the now-deleted `migrate`.
- **Direction of the change is additive for `run_migrations.py`'s capability**: it previously could not apply a `CONCURRENTLY` migration at all (would fail with a Postgres transaction-block error); it now can, via the same tested logic that already worked in `migrate.py`. Every migration that does **not** contain `CONCURRENTLY` goes through the exact same code path as before (`cur.execute(sql)` inside the existing single transaction) — unchanged.
- **`migrate.py` had zero production usage to protect.** A39 already confirmed live that `migrate.py` would fail immediately if run against production today (`column "version" does not exist` — production's `schema_migrations` doesn't have that column). Deleting it removes a script that could not have worked, not a working one.
- **One real runtime-visible change**: `routes/admin/auto_payouts.py`'s error message (shown to an admin when the `auto_payouts` table is missing) now points at the real, working command instead of a deleted script. This is a pure improvement — the old message would have sent an operator to run a script that no longer exists (and, even before deletion, would have failed against the real `schema_migrations` shape).
- **No schema, migration, or data change of any kind.**

## 5. User-experience effect

None rider/driver/corporate-facing. One internal-admin-facing improvement: the `auto_payouts`-table-missing error message now names a command that actually works.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/scripts/run_migrations.py` | Added `_split_sql_statements`, `_apply_one_autocommit`; `_apply_one` now routes CONCURRENTLY-containing migrations through the autocommit path | Port the tested B0 fix into the canonical runner, which never had it |
| `backend/scripts/migrate.py` | **Deleted** | Targeted a `schema_migrations` shape never actually applied to production (A39); its one useful piece was ported first |
| `backend/tests/test_migration_concurrently_splitting.py` | Import + doc/comment updates to reference `run_migrations` instead of the deleted `migrate` | Keep testing the real function, now in its new location |
| `backend/tests/test_migrate_autocommit_chunks.py` | **Deleted** (replaced) | Tested `migrate.py`'s now-deleted `_apply_migration_autocommit` |
| `backend/tests/test_run_migrations_autocommit_chunks.py` | New — replaces the above, adapted for `_apply_one_autocommit`'s `(filename, checksum)` signature, plus a new test pinning that exact INSERT shape | Regression coverage for the ported function in its real location |
| `backend/tests/test_financial_events_ride_id_fk_contract.py` | One stale comment referencing `migrate.py` corrected | Accuracy |
| `backend/tests/test_unbalanced_scoped_migration.py` | One stale comment referencing `migrate.py` corrected | Accuracy |
| `backend/routes/admin/auto_payouts.py` | Error message now names the real command | A real user (admin)-facing message was pointing at a deleted script |
| `CLAUDE.md` | Two blocks updated: the A39-era "Use run_migrations.py, not migrate.py" note (now describes the deletion + port), and a separate stale "Database & Migration Conventions" line the earlier A39 fix had missed | Both were living docs describing a script that no longer exists |
| `AGENTS.md` | Two blocks updated: a command example (`python migrate.py --env production`, which was never even migrate.py's real CLI) and the "Database & Migration Conventions" line | Same |
| `backend/migrations/CLAUDE.md` | Updated to note deletion | Same |
| `docs/dev-setup.md` | Command example fixed | Same |
| `docs/runbooks/migration-conflict-detection.md` | Reference updated to note deletion | Same |
| `.github/workflows/migration-check.yml` | Two explanatory comments updated to point at the new location | Same |
| `.claude/commands/migration-check.md` | Command example fixed | Live slash-command definition, not a historical doc |
| `backend/requirements.in` | Comment referencing `migrate.py` corrected | Living dependency-rationale comment |
| `backend/.coveragerc` | Removed two stale `omit` entries (`migrate.py`, `scripts/migrate.py`) for a file that no longer exists | Stale coverage-exclusion entries are silently harmless but misleading during audit |
| `docs/runbooks/admin-rollback.md` | Command fixed | Living runbook |
| `docs/runbooks/supabase-region-migration.md` | 5 occurrences fixed — **not just the script name**: the original also documented `migrate.py`'s `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` connection interface, which is wrong for `run_migrations.py` (requires `DATABASE_URL`, a direct Postgres/pooler connection); rewrote the connection example. Also removed a stale "highest migration is 135" reference (repo is now at 321) | A region-migration runbook with a wrong connection interface would have sent an operator down the wrong path during an actual region cutover |
| `docs/runbooks/deploy-migration-64-65.md` | 3 occurrences fixed, same connection-interface correction | Same |
| `docs/runbooks/deploy-migration-297.md` | 2 occurrences fixed, same connection-interface correction | Same |
| `docs/runbooks/stripe-identity-drift-manual-test.md` | 2 occurrences fixed | Living runbook, found in a follow-up sweep |
| `.planning/ROADMAP.md` | 1 line fixed (`REL-1` deliverable) | Living planning doc |
| `.planning/REQUIREMENTS.md` | 1 line fixed (`REQ-DEPL-7`) | Same |
| `docs/driver-faqs-saskatchewan.md` | 1 line fixed | Living doc (driver-facing FAQ seed instructions) |
| `docs/change-log/2026-08-17-a39-migrate-py-reconciliation-and-deletion.md` | This file | Mandatory Change Impact Log |

### Second-pass correction (2026-08-17, same day)

`spinr-migration-reviewer` reviewed the first commit before PR creation (per standing instruction to wait for review before opening the PR) and found the reference-cleanup claim in §3/§9 above was **not actually complete**: a full-repo grep it ran turned up 11 more files still containing actionable `migrate.py` references that the first pass missed — verdict "FIX BEFORE MERGE (non-blocking to production safety, blocking to this change's own completeness claim)". The reviewer was explicit that the runner code itself (the CONCURRENTLY port) was correct, faithfully tested, and safe — only the reference-cleanup was incomplete.

Of the 11 flagged files, 9 were living docs/config and are now fixed (listed in the table above). 2 (`backend/scripts/verify_migrations_286_291.sql`, `backend/scripts/verify_migrations_292_293.sql`) were deliberately left untouched after inspection: `verify_migrations_292_293.sql`'s own header comment states editing it "would break the provenance of" a result cited in `docs/change-log/2026-08-08-migration-verification-result.md` — both are self-declared point-in-time provenance records, not living documentation, consistent with the historical-record convention already applied elsewhere in this change and this session. One additional file (`docs/runbooks/stripe-identity-drift-manual-test.md`) was found and fixed via an independent final sweep, not on the reviewer's original list.

Also checked and correctly left alone: `.planning/graphs/graph.json` and `.planning/graphs/.last-build-snapshot.json` contain dozens of `migrate.py` path references, but per `AGENTS.md` these are auto-generated Graphify build outputs (regenerated on rebuild from the live codebase), not hand-authored docs — editing them by hand would be overwritten by the next rebuild and is not the intended fix path.

## 7. Before / after

```python
# Before (backend/scripts/run_migrations.py, _apply_one)
def _apply_one(conn, path: Path) -> None:
    """Apply one migration file inside a transaction + record provenance."""
    sql = path.read_text()
    checksum = _checksum(path)
    print(f"[apply] {path.name}  ({len(sql):,} bytes, sha256={checksum[:12]}…)")
    with conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(
            "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
            (path.name, checksum),
        )
    conn.commit()
```

```python
# After
def _apply_one(conn, path: Path) -> None:
    sql = path.read_text()
    checksum = _checksum(path)
    print(f"[apply] {path.name}  ({len(sql):,} bytes, sha256={checksum[:12]}…)")

    statements = _split_sql_statements(sql)
    needs_autocommit = any("CONCURRENTLY" in stmt.upper() for stmt in statements)

    if needs_autocommit:
        _apply_one_autocommit(conn, path.name, checksum, statements)
        return

    with conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(
            "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
            (path.name, checksum),
        )
    conn.commit()
```

## 8. Rollback plan

`git-revert-safe`. This is pure code/docs, no migration, no data written. Reverting restores `migrate.py` (still broken against production's real schema, per A39) and reverts `run_migrations.py` to lacking CONCURRENTLY support (the pre-existing state, not a regression this PR introduced). If ever needed again, `git log` / `git show` on this commit recovers `migrate.py`'s exact prior content.

## 9. Verification performed

- [x] `pytest backend/tests/test_migration_concurrently_splitting.py backend/tests/test_run_migrations_autocommit_chunks.py backend/tests/test_financial_events_ride_id_fk_contract.py backend/tests/test_unbalanced_scoped_migration.py backend/tests/test_auto_payout.py backend/tests/test_migration_ordering.py backend/tests/test_migration_fk_column_types.py -q --no-cov` — 128/128 pass.
- [x] `ruff check` + `ruff format --check` on every touched Python file — clean.
- [x] Blast-radius grep: confirmed no application code (routes/services/background loops) imports either `run_migrations` or the deleted `migrate` module.
- [x] Grepped the entire repo for every remaining `migrate.py` reference; fixed all living docs/CI/code, deliberately left historical audit/change-log records untouched (same convention as the rest of this session). This grep was re-verified by `spinr-migration-reviewer`, which found the first pass had actually missed 11 files (see §3's "Second-pass correction") — all now fixed or explicitly judged out of scope.
- [ ] Not run against a live Postgres instance — same limitation as the original B0 fix and every other change to this tooling in this repo's history; verified via the real function against every CONCURRENTLY-mentioning migration file's actual text (unchanged from B0's original validation) plus unit tests with a mocked connection.

## What was NOT verified

- Not exercised end-to-end against a real, fresh Postgres database (no such test harness exists anywhere in this repo's history for either migration runner) — same limitation the original B0 fix already carried and disclosed.
- Whether any deploy pipeline or runbook outside this repo (e.g. a CI/CD system's own scripted steps) references `backend/scripts/migrate.py` by path — only this repo's own tracked files were checked; A39's own "what was NOT verified" already flagged this same boundary.
