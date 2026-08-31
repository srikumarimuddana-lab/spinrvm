# Git history rewrite plan — driver_bank_sin_migration.sql / driver_csv_migration.sql

**Status: PREPARED, NOT EXECUTED.** Per explicit owner instruction (2026-08-31): write the
plan/script and stop short of force-pushing. Nothing below has been run against the real
remote. This document is the thing to review/hand off before anyone pulls the trigger.

## Why this is needed

Both files' *working-tree* content is gone (`driver_bank_sin_migration.sql` blanked in
`44183d3`, `driver_csv_migration.sql` deleted in `41cee45` / #4731), but git history is
append-only — every commit before those still serves the full plaintext PII (157 drivers'
SIN/bank data; 189 drivers' name/email/phone/license/lat-lng) to anyone with `git log -p`,
`git show <sha>:<path>`, or a clone made before either removal. See
`docs/audit/breach-record.md` Incident 1 for the full breach record — this rewrite is one
of that entry's three still-open items.

## Scope — every commit touching either path

Confirmed via `git log --all --full-history` — no renames, both files introduced together,
one straight line of history each:

| File | Introduced | Content removed |
|---|---|---|
| `driver_bank_sin_migration.sql` | `3c336ff` (merge of PR #3978) | `44183d3` (blanked, working tree only) |
| `driver_csv_migration.sql` | `3c336ff` (merge of PR #3978) | `41cee45` / `9856108` (deleted, working tree only, PR #4731) |

Both files exist, with full plaintext, in every commit's tree from `3c336ff` through their
respective removal commits — that's the exposure window this rewrite closes.

## Recommended tool: `git filter-repo`

`git filter-repo` (not BFG, not `filter-branch`) — it's the tool the Git project itself
recommends, handles this repo's ~large history in one pass, and its `--invert-paths` mode is
exactly "strip these two paths from every commit, everywhere, forever."

```bash
# 1. Fresh, disposable clone — filter-repo refuses to run on a clone that still
#    has a working set of remotes/refs it might clobber by accident. Never run
#    this against your primary working clone.
git clone --no-local /path/to/spinrvm.git spinrvm-history-rewrite
cd spinrvm-history-rewrite

# 2. Strip both files from every commit in every branch/tag.
git filter-repo --invert-paths \
  --path driver_bank_sin_migration.sql \
  --path driver_csv_migration.sql

# 3. Verify: neither path exists in ANY commit of the rewritten history.
git log --all --full-history --oneline -- driver_bank_sin_migration.sql driver_csv_migration.sql
#   -> must print nothing

# 4. Re-add the remote (filter-repo removes it as a safety measure) and push.
git remote add origin <repo-url>
git push origin --force --all
git push origin --force --tags
```

## Before pushing — required steps, in order

1. **Confirm repo visibility is actually private** (still unverified independently as of
   this writing — see `docs/audit/breach-record.md`'s "Still-open items" #2). Rewriting
   history on a public repo does not un-expose it retroactively for anyone who already
   cloned or crawled it during the exposure window; do this after visibility is locked
   down, not instead of it.
2. **Coordinate with the other cofounder before pushing, not after.** A force-push of
   rewritten history breaks every existing local clone and any open branch/PR based on
   old commits:
   - Their local `main` (and any other branch) will diverge from the rewritten remote —
     `git pull` will fail or silently create a mess of conflicting history.
   - The safe fix for them is `git fetch origin && git reset --hard origin/<branch>` on
     every local branch they have checked out **after** confirming they have no
     uncommitted/unpushed work — anything of theirs not already on `origin` is lost by
     that reset.
   - Any of their open branches based on pre-rewrite commits need to be re-based onto the
     new history (or abandoned and recreated) — `git filter-repo` rewrites every commit's
     SHA from the first touched commit onward, so *all* downstream commits get new hashes.
   - Tell them the exact time window so they don't push mid-rewrite.
3. **Snapshot first.** Before running filter-repo, take a full mirror clone
   (`git clone --mirror`) and keep it somewhere offline as a rollback point, in case the
   rewrite itself goes wrong (wrong path, tool bug, etc.) — this is a rollback plan for the
   *rewrite operation itself*, not a way to undo closing the PII exposure once confirmed
   done and re-pushed.
4. **Re-run this repo's CI/security-gates once, locally or on a throwaway branch, against
   the rewritten history** before trusting it — specifically confirm the `spinr-sin-bank-pii`
   and (once merged) `spinr-driver-csv-pii` gitleaks rules report clean on a full-history
   scan of the rewritten repo, as a sanity check that the rewrite actually removed what it
   was supposed to.
5. **GitHub-side cleanup after the push**: GitHub caches old commits reachable from PRs,
   forks, and its own internal object store for a period after a force-push even once no ref
   points to them directly. File a support request with GitHub to purge cached views of the
   removed commits (`docs/runbooks/data-breach.md` §2 covers this as part of containment)
   — the local rewrite alone does not guarantee GitHub's UI/API stop serving the old blobs
   immediately.

## What this does NOT fix by itself

- Anyone who already cloned, forked, or downloaded the repo (or scraped it via GitHub's API)
  during the exposure window keeps their own copy of the old history regardless of any
  rewrite done here — this closes the *ongoing* exposure vector (the file being trivially
  reachable from the current public/former-public repo), not past access. That distinction
  is already recorded in the breach register's RROSH determination.
- It does not substitute for the SGI notification question already flagged in the breach
  record (driver license numbers were in `driver_csv_migration.sql`) — that's a separate,
  still-open item.

## Rollback plan for the rewrite operation itself

If the rewrite is found to be wrong post-push (missed a path, corrupted history, etc.): the
mirror clone taken in step 3 above has the exact pre-rewrite state. Force-push that mirror
back to `origin` to restore, then start over. This is why step 3 is not optional.
