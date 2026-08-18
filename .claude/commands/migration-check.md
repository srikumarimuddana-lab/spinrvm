# /migration-check — Supabase Migration Safety Review

Delegate to the `spinr-migration-reviewer` agent to validate new SQL migrations against Spinr conventions before merge.

## Usage

```
/migration-check                          # audits newly-added backend/migrations/*.sql in the diff
/migration-check backend/migrations/38_driver_insurance_corrections.sql
```

## What it does

1. Scopes:
   - No args → `git diff --cached --name-only --diff-filter=A backend/migrations/` plus any modified migrations (modifications are a red flag — migrations are append-only)
   - Path args → those files
2. Loads context: `@.claude/context/regulatory-sk.md` (retention rules), `CLAUDE.md` (migration conventions)
3. Runs `ls backend/migrations/ | sort | tail -5` to confirm next-free number
4. Dispatches `spinr-migration-reviewer` with the scope
5. Reports findings — no edits applied automatically

## What gets checked

From the agent:

- **Numbering** — `NN_*.sql`, next free, no conflict, no reuse
- **Append-only** — no edits to previously merged migrations
- **RLS** — every new user-data table has explicit policies in the same file
- **Reversibility** — top-of-file rollback comment present
- **Forward-compat** — no blocking `ALTER TABLE` on hot tables without batching
- **Indexes** — match new query patterns added in app code
- **Money functions** — `SECURITY DEFINER` + `SET search_path` + row lock, revoke from anon
- **Retention** — no `ON DELETE CASCADE` on retention-sensitive tables (trips, insurance periods, safety incidents)

## Output

```
SPINR MIGRATION REVIEW — <file(s)>
==================================
NUMBERING, APPEND-ONLY, RLS, REVERSIBILITY, FORWARD-COMPAT, INDEXES, MONEY SAFETY, RETENTION
BLOCKERS / WARNINGS
VERDICT: SAFE TO APPLY / FIX BLOCKERS / NEEDS DBA REVIEW
```

## When to run

- Before raising a PR that adds a migration
- Before a production migration apply window
- When inheriting a migration from another contributor

## Do NOT

- Do not run `python -m backend.scripts.run_migrations` from this command — that's a deploy step, not an audit
- Do not auto-renumber conflicting migrations — surface the conflict, let the human pick the slot
