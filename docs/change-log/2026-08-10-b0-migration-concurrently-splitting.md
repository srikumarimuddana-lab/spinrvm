# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-10 |
| Author | Claude (agent-assisted) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin (migration tooling; not a request-path domain) |
| PR / commit link | branch `claude/spinr-ai-guardrail-reviewer-o2vups` |
| Related issue or gap ID | ACTION_ITEMS.md — B0 |

## 1. Issue / gap identified

`backend/scripts/migrate.py` routed a migration to its non-transactional
autocommit path whenever the literal string `CONCURRENTLY` appeared
**anywhere** in the file text, including inside a `--` comment. That path
split the file naively on `sql.split(";")`, which corrupted two classes of
migration: a semicolon inside a prose comment leaked the comment's tail into
the next statement as raw SQL, and a semicolon inside a `$$…$$`-quoted
function body shredded the function definition into broken fragments.

## 2. Root cause

Two related shortcuts: (a) `CONCURRENTLY` detection was a whole-file
substring search over raw text rather than a check against parsed executable
SQL, so a mention in a comment or rollback note misrouted the file; (b) the
statement splitter was a naive `split(";")` with no awareness of comments,
string literals, or dollar-quoted bodies, so any semicolon anywhere was
treated as a statement boundary.

## 3. Fix / remediation

Replaced the naive splitter with a comment/literal-aware tokenizer
(`_tokenize_sql`) that classifies the file into `code` / `comment` /
`literal` chunks (covering `--` and `/* */` comments, `'...'` and `"..."`
literals, and `$tag$...$tag$` dollar-quoting). `split_sql_statements()`
splits only on semicolons inside `code` chunks. `CONCURRENTLY` routing
(`_statement_needs_autocommit`) now runs a regex over each **parsed
statement's** code-only portion, not the raw file text, so a comment mention
no longer misroutes a migration. The test suite's `_KNOWN_UNSPLITTABLE`
allowlist (34 previously-exempted migrations) was removed entirely, and the
exhaustive-splitting property test now runs over every migration file in the
repo, not just the ones outside that allowlist.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the migration runner itself.** `migrate.py` is
  invoked only via `python scripts/migrate.py` (manual/CI ops tooling per
  root `CLAUDE.md`), never imported by the running backend process, request
  handlers, or any of the 18 background loops. Grepped
  `backend/` for other importers of `migrate.py` — none found.
- All 34 previously-merged migrations that were on the `_KNOWN_UNSPLITTABLE`
  allowlist are already recorded as applied in `schema_migrations` in every
  live environment, so this change does not re-run or re-execute anything
  against production/staging — it only changes behavior the next time the
  full migration set is applied to a **fresh** database (new staging
  project, DR rebuild, new-province project).
- No interaction with the ride state machine, money/wallet deltas, or RLS.

## 5. User-experience effect

None. This is internal ops tooling; no rider, driver, corporate-admin, or
internal-admin-facing surface is touched.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/scripts/migrate.py` | Added `_tokenize_sql`, rewrote `split_sql_statements`/`_strip_leading_comments`/`_statement_needs_autocommit`/`_apply_migration_autocommit` to be comment/literal-aware and to detect `CONCURRENTLY` from parsed statements instead of raw text | Fix the shredding + misrouting bug (B0) |
| `backend/tests/test_migration_concurrently_splitting.py` | Removed `_KNOWN_UNSPLITTABLE`; imports the real splitter/detector from `migrate.py`; added targeted cases (mid-comment semicolon, `$$` function body, named `$tag$`, real `CONCURRENTLY` migration, comment-only mention, quoted-string semicolon); made the exhaustive property test run over every migration file | Prove the fix holds for the whole corpus, not a hand-picked subset |

## 7. Before / after

```python
# Before — backend/scripts/migrate.py (_apply_migration_autocommit)
if "CONCURRENTLY" in sql:  # raw substring match over the whole file
    for stmt in sql.split(";"):  # naive split, blind to comments/literals
        ...

# After
statements = split_sql_statements(sql)  # comment/literal-aware
needs_autocommit = any(_statement_needs_autocommit(s) for s in statements)
```

## 8. Rollback plan

`git revert` is sufficient and complete — this change touches no live data,
no migration content, and no schema. Reverting restores the old (buggy but
previously-tolerated, since already-applied migrations are never re-run)
behavior with no data-level cleanup required.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_migration_concurrently_splitting.py backend/tests/test_migrate_autocommit_chunks.py` — 362 passed (property test now covers every migration file in the repo, not a subset). Also re-run combined with the AI1 test files below in the same session: 421 passed total, 0 failures.
- [ ] Manual repro steps followed in staging — **not performed**; no live Postgres/Supabase instance available in this environment. Only unit-level verification against the literal text of every real migration file, plus a mocked-cursor assertion (`test_migrate_autocommit_chunks.py`) of the exact statements sent to `cur.execute()`.
- [x] Blast-radius grep performed: confirmed `migrate.py` has no other importers in `backend/`.
- [x] Reviewed against relevant CLAUDE.md convention: `backend/migrations/CLAUDE.md` (append-only rule — not applicable, no new migration added; this only changes the runner).
- [x] Not user-visible; feature flag not applicable.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data impact)
- [x] Blast radius is stated, not assumed (isolated to ops tooling, no other callers)
- [x] No silent behavior change to an already-shipped flow — the only behavior change is in a manually-invoked ops script, not a running production code path

## What was NOT verified

Not tested against a real fresh-database `python backend/scripts/migrate.py`
run end to end — no live Postgres instance was available in this
environment. Verification was limited to: unit tests of the tokenizer/
splitter/detector logic, the exhaustive property test run over the literal
text of every migration file currently in the repo, and the existing mocked-
cursor test asserting the exact statement sequence sent to `cur.execute()`.
Recommend a dry-run (`--dry-run`) against a scratch Supabase project before
this is relied on for the next real fresh-environment migration apply.
