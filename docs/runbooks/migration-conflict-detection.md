# Migration Conflict Detection — Runbook

**Audience:** anyone authoring a new Postgres migration in `backend/migrations/`.

**Why this exists:** on 2026-04-28, two parallel PRs both used migration slot
56 to `CREATE OR REPLACE` the same Postgres function (`purge_pii_retention`).
The alphabetically-second migration silently overwrote the first one's body
— and was forked from an *older* version of the function, regressing two
fixes that had landed in migration 51. The result was a production bug that
silently disabled all PIPEDA + Saskatchewan Transportation Act retention
enforcement until a forward fix shipped in migration 57.

This runbook captures the failure mode and gives the next migration author
a checklist that catches it before merge.

---

## The failure mode in one paragraph

Spinr's migration runner (`backend/scripts/run_migrations.py` — not
`backend/scripts/migrate.py`, which targets a different, never-actually-used
`schema_migrations` shape; see `ACTION_ITEMS.md` A39) iterates files in
alphanumeric order via `sorted(glob.glob('*.sql'))`. Two migrations
sharing the same numeric prefix (e.g. `56_a.sql` and `56_b.sql`) both run,
but the second one wins for any object both modify. If both call
`CREATE OR REPLACE FUNCTION foo(...)` with different bodies, the second
one's body is what production runs — even if the second author had no
visibility into the first's changes. CLAUDE.md tolerates duplicate
prefixes (the runner uses the full filename as the idempotency key), so
the migration-safety CI gate doesn't flag this as an error. Forks of
older function bodies silently undo intervening fixes.

---

## The 5-minute pre-merge check

Run these BEFORE asking for review on any PR that touches
`backend/migrations/*.sql`:

### 1. Confirm migration number is fresh on `origin/main`

```bash
git fetch origin main
ls backend/migrations/ | sort -V | tail -5
```

If `origin/main` already has a file with your number, **rename yours to the
next free slot.** Same number with a different filename works in production
but creates ordering ambiguity that bites later.

### 2. List every CREATE OR REPLACE / ALTER TABLE in your migration

```bash
grep -nE 'CREATE OR REPLACE FUNCTION|CREATE OR REPLACE PROCEDURE|ALTER TABLE|CREATE TRIGGER|DROP TRIGGER' backend/migrations/<your-file>.sql
```

Every line that matches is an object that *some other migration may already
own*. Walk each one through step 3.

### 3. For every CREATE OR REPLACE target, find its history

```bash
target='purge_pii_retention'   # replace
git log --all --oneline -p -S "${target}" -- backend/migrations/ | head -100
```

This shows every migration that has ever defined or modified the target,
in commit order. **Read every prior body.** The most recent one before
yours is the source of truth — fork your replacement from THAT, not from
an earlier version.

### 4. Diff your function body against the latest prior body

If the prior body is in `backend/migrations/51_audit_logs_lockdown.sql`
and yours is in `57_*.sql`, run:

```bash
diff <(awk '/CREATE OR REPLACE FUNCTION purge_pii_retention/,/\$\$;/' backend/migrations/51_audit_logs_lockdown.sql) \
     <(awk '/CREATE OR REPLACE FUNCTION purge_pii_retention/,/\$\$;/' backend/migrations/57_*.sql)
```

You should be able to *defend every difference*. A diff that drops a step
or changes column names by accident is the failure this runbook prevents.

### 5. Check for parallel PRs that also touch the same target

```bash
gh pr list --state open --search 'in:diff backend/migrations'
```

For every open PR that touches a migration:
- Read its diff for `CREATE OR REPLACE` against the same target as yours.
- If overlap exists, **coordinate explicitly**. Either:
  - Wait for the other PR to merge, then rebase and merge the prior body in.
  - Land yours first and ask the other author to rebase on top of it.

---

## Red flags during review

A PR description should explicitly call out — for any
`CREATE OR REPLACE FUNCTION`:

1. The migration number of the prior body it forked from.
2. A brief summary of what changed vs that prior body.
3. Confirmation that no other migration between that prior and this PR
   modified the function (or, if one did, why this PR is compatible).

If the PR description doesn't mention any of these, push back during
review. Three lines of provenance prevent a regression incident.

---

## What CI catches today (and what it doesn't)

The `Migration Safety` guard rail in `.github/workflows/ci-guardrails.yml`
checks:

- ✅ New migrations don't drop or rename existing tables/columns without
  guards.
- ✅ Migrations are append-only (no edits to already-applied files).
- ✅ Forward-compatible (no exclusive locks on hot tables without
  `CONCURRENTLY`).

It does NOT catch:

- ❌ Two migrations with the same numeric prefix where the
  alphabetically-second one's `CREATE OR REPLACE` overwrites the first's
  function body. This is a **manual review responsibility** today; this
  runbook is the checklist.

A future enhancement (tracked separately) would have CI walk every PR's
new migrations, extract every `CREATE OR REPLACE FUNCTION <name>(...)`
target, and fail the build if a different open PR or a recently-merged
PR ALSO modifies that target without explicit annotation in the body
("REPLACES function from migration 51 — see § X"). Until that ships,
this runbook is the gate.

---

## Real-world example: the 2026-04-28 incident

| When | What | Outcome |
|---|---|---|
| 2026-04-27 23:46 | PR #138 opened — adds `56_audit_logs_delete_lockdown.sql` with new BEFORE DELETE trigger and a `CREATE OR REPLACE` of `purge_pii_retention()` forked from migration 51 (good) | Merged |
| 2026-04-28 ~01:30 | PR #141 opened — adds `56_purge_dsar_pending_deletions.sql` with a `CREATE OR REPLACE` of `purge_pii_retention()` forked from migration **50** (bad — missed migration 51's fixes and missed PR #138's Step G) | Merged |
| 2026-04-28 02:15 | Conflict-safety audit on a follow-up PR detected the regression: PR #141's body uses `(actor_id, actor_role, action, resource, details)` — columns that don't exist on `audit_logs`, so every retention loop tick rolls back the entire transaction | — |
| 2026-04-28 02:30 | PR #166 opens migration 57 that merges PR #141's DSAR step + restores migration 51's INSERT schema fix + restores PR #138's session-flag pattern | Merged |

**Total time silently broken in production:** ~45 minutes. Caught by the
next PR's audit before any retention loop tick had a chance to fail in
production (pre-launch). The fix shipped within 30 minutes of detection.

**What would have prevented it:** PR #141's author following step 3 of
this runbook would have seen migration 51's commit message that explicitly
calls out the schema fix. Forking from migration 51 instead of 50 would
have produced a body that's compatible with PR #138 (the trigger from
138 still requires the session flag, but 141 didn't include the audit-log
DELETE step at all, so the conflict never had to be resolved).

---

## TL;DR

Before merging a migration that calls `CREATE OR REPLACE`:

1. Find every prior migration that defined the target.
2. Fork your body from the **most recent** one, not an older one.
3. Diff your body against that latest prior body and defend every change.
4. Check open PRs for parallel modifications of the same target.
5. State the provenance ("forked from migration N") in the PR body.

If you skip these, eventually you will silently overwrite someone else's
fix and ship a bug to production. We have a real example in our git
history; let it be the only one.
