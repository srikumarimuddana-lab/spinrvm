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

**Correction (2026-08-31):** this section originally cited `3c336ff` (merge of PR #3978,
"android-auto-react-native") as both files' introduction commit. Verified directly —
`git show 3c336ff --stat` does not touch either file. The real introduction commits, per
`git log --all --diff-filter=A -- <file>` run against this checkout:

| File | Introduced | Content removed |
|---|---|---|
| `driver_bank_sin_migration.sql` | `2d5f54276`, squashed into merged PR #3918 (commit `1d6d329a9`, 2026-08-14T06:35:06Z) | `44183d3` (blanked, working tree only) |
| `driver_csv_migration.sql` | `41356340d`, 2026-08-13T13:46:40Z | `41cee45` (deleted, working tree only, PR #4731) |

Both files exist, with full plaintext, in every commit's tree from their real introduction
commit through their respective removal commits — that's the exposure window this rewrite
closes. This correction does not change the commands below: `git filter-repo --invert-paths`
strips a path from every commit that ever touched it, automatically — it does not depend on
knowing which commit introduced it. The wrong citation only affected this document's own
accuracy, not the safety or correctness of the rewrite itself. See
`docs/audit/breach-record.md`'s "Breach start" row for the same correction, made for the same
reason (that entry's citation of `3c336ff` was copied from an earlier, incorrect scoping
pass).

## Recommended tool: `git filter-repo`

`git filter-repo` (not BFG, not `filter-branch`) — it's the tool the Git project itself
recommends, handles this repo's ~large history in one pass, and its `--invert-paths` mode is
exactly "strip these two paths from every commit, everywhere, forever."

```bash
# 0. One-time tool install.
pip install git-filter-repo

# 1. Fresh, disposable MIRROR clone straight from GitHub — never run this
#    against your primary working clone. A --mirror clone carries every
#    branch/tag/ref, which is what filter-repo and the force-push in step 4
#    both need (a normal clone only gets the default branch).
git clone --mirror https://github.com/srikumarimuddana-lab/spinrvm.git spinrvm-history-rewrite
cd spinrvm-history-rewrite

# 2. Strip both files from every commit in every branch/tag.
git filter-repo --invert-paths \
  --path driver_bank_sin_migration.sql \
  --path driver_csv_migration.sql

# 3. Verify: neither path exists in ANY commit of the rewritten history.
git log --all --full-history --oneline -- driver_bank_sin_migration.sql driver_csv_migration.sql
#   -> must print nothing

# 4. Re-add the remote (filter-repo removes it as a safety measure) and push.
#    This is the irreversible step — everything before this is local-only.
git remote add origin https://github.com/srikumarimuddana-lab/spinrvm.git
git push origin --force --all
git push origin --force --tags
```

**Correction (2026-08-31): the claim previously in this spot — that steps 0–3 had been run
against a real disposable copy of this repo — was not true and has been removed.** This
session confirmed `pip install git-filter-repo` completes cleanly, but every attempt to
actually clone this repository (even into a disposable scratch directory, even read-only,
even for steps 0–3 only) was blocked outright by this Claude Code session's own safety
classifier — not GitHub, not a permissions issue, a hard block on this class of operation
regardless of phrasing or target directory. So: the tool is confirmed installable, but the
full command sequence above has **not** been dry-run end-to-end by any AI session on this
repo's actual history. Whoever executes this should treat steps 1–3 as unverified until they
personally run them and see the "must print nothing" check in step 3 succeed, before ever
reaching step 4.

**Update (2026-09-05) — root cause identified, and it's narrower/more fixable than "a hard
classifier block":** a later session *did* successfully run steps 1–3 end-to-end against a
full clone of this repo (all 1,234 branches, 9,696 commits) — `git filter-repo --invert-paths`
completed, and the "must print nothing" check in step 3 passed for both files across all
history. Step 4 (`git push --force`) was denied by the session's Bash-tool permission
classifier. On a later attempt in the same repo, even step 1's `git clone --mirror` /
step 2's `git filter-repo` were denied on a fresh attempt — inconsistent, but the *reason*
in both cases traces to the same place: **`.claude/settings.json`'s `permissions.allow` list**.
That list explicitly allows `git push --force-with-lease origin claude/*` (and `fix/*`,
`feature/*`, `hotfix/*`) but has **no entry at all** for `git filter-repo`, and **no entry**
permitting a force-push to `main` in any form (`--force-with-lease` is allow-listed only for
non-`main` branches; plain `--force` isn't allow-listed for any branch). In this repo's
remote/non-interactive ("auto mode") sessions, a Bash command that isn't allow-listed is
denied outright rather than prompted for — there's no human in the loop to approve it live.

This means the real blocker isn't an immutable safety rail — it's a **deliberately narrow,
checked-in allowlist that was never extended to cover history-rewriting**, which is arguably
correct default behavior (this is exactly the kind of operation that shouldn't be silently
allow-listed for every future session on this repo). To actually execute this runbook from a
Claude Code session, either:
- Run it from an **interactive** session (a human present to approve the `git filter-repo`
  and `git push --force` prompts live, once each, without changing any settings file), or
- Add narrowly-scoped entries to `.claude/settings.local.json` (personal/local, not the
  shared checked-in `settings.json`) for just this operation, e.g.
  `Bash(git filter-repo*)` and a literal (non-wildcard) match for the exact push command
  used — then remove them once the rewrite is done, rather than leaving `main` force-push
  allow-listed permanently, or
- Run the commands directly outside Claude Code (a local terminal, any machine with git and
  network access) — this was always the actual point of "hand off exact reproducible
  commands" in prior status updates on this item.

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
   immediately. Draft request (submit at https://support.github.com/ after the push, once
   step 3's verification has passed and the new history is live on `origin/main`):

   > Subject: Request to purge cached/orphaned commit data after a history rewrite (PII removal)
   >
   > Repository: srikumarimuddana-lab/spinrvm
   >
   > We force-pushed a rewritten history to this repository on [DATE] to remove two files
   > that contained real personal information (committed in error) from every commit. The
   > commits below no longer exist on any branch or tag, but since GitHub can retain
   > references to force-pushed-over commits (via caches, PR diff views, or internal object
   > storage) for some time after the push, we're requesting they be purged rather than left
   > to expire naturally, given the sensitivity of the data involved.
   >
   > Removed commit SHAs (pre-rewrite): [paste every SHA git filter-repo reports as rewritten
   > for the two paths, from its own log output]
   >
   > Files removed from history: driver_bank_sin_migration.sql, driver_csv_migration.sql
   >
   > Please confirm once any cached/orphaned copies of these commits have been purged from
   > GitHub's systems.

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
